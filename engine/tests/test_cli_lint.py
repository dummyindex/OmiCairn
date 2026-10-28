"""CLI subcommand drift lint for omicairn/cli.py.

The CLI module has FIVE surfaces that must all list the same set of
subcommands, any two of which can drift apart without the runtime
tests noticing:

1. The module-level docstring under a `Subcommands:` heading — this
   is what `help(omicairn.cli)` prints and what readers see first when
   they open the file.

2. The `epilog=textwrap.dedent(...)` block inside `main()` — this is
   what `omicairn --help` prints at the terminal.

3. The `def cmd_<name>(args)` function definitions — each one is a
   subcommand handler. An unregistered handler is dead code; a missing
   handler raises AttributeError at runtime (the parser registers a
   subcommand whose set_defaults(func=) points at nothing).

4. The `sub.add_parser("<name>", ...)` calls inside `main()` — these
   are the argparse registrations that actually gate the CLI.

5. The `set_defaults(func=cmd_<name>)` calls — these bind each
   registered subparser to a handler. Forgetting one makes `omicairn <sub>`
   crash with a missing-func error.

Existing `tests/test_cli.py` exercises each subcommand via argv, so it
would catch a subcommand that is fully removed from (3)+(4)+(5) and
still referenced in a test. It would NOT catch:

- Removing a subcommand from runtime but forgetting to update (1) and
  (2) — users see stale help text but every runtime test still passes.
- Adding a new `cmd_foo()` handler but forgetting `sub.add_parser("foo")`
  — the handler is dead code and no runtime test exercises it.
- Registering `sub.add_parser("foo")` with `set_defaults(func=cmd_foo)`
  but never defining `cmd_foo` — the import succeeds (argparse lazy-
  binds `func`) and the failure only surfaces when a user runs `omicairn foo`.

All five checks are cheap static-text scans on omicairn/cli.py, with no
side effects. If the source is refactored (e.g. moved into a sub-
package), these regexes will fail loudly — which is better than silently
passing an empty set check.
"""
from __future__ import annotations

import ast
import pathlib
import re


CLI_PATH = pathlib.Path(__file__).resolve().parents[1] / "omicairn" / "cli.py"


# ---------------------------------------------------------------------------
# Helpers — five static scans over omicairn/cli.py
# ---------------------------------------------------------------------------


def _cli_source() -> str:
    return CLI_PATH.read_text()


def _cmd_function_names() -> set[str]:
    """Every `def cmd_<name>(...)` at module scope in omicairn/cli.py."""
    src = _cli_source()
    return set(re.findall(r"^def cmd_([a-z_]+)\(", src, re.MULTILINE))


def _add_parser_names() -> set[str]:
    """Every `sub.add_parser("<name>", ...)` call in omicairn/cli.py."""
    src = _cli_source()
    return set(re.findall(r'sub\.add_parser\("([a-z_]+)"', src))


def _set_defaults_handler_names() -> set[str]:
    """Every `set_defaults(func=cmd_<name>)` call in omicairn/cli.py."""
    src = _cli_source()
    return set(re.findall(r"set_defaults\(func=cmd_([a-z_]+)\)", src))


def _docstring_subcommand_names() -> set[str]:
    """Parse the module docstring for `  <name>  <description>` rows
    appearing under the `Subcommands:` heading."""
    tree = ast.parse(_cli_source())
    docstring = ast.get_docstring(tree)
    assert docstring is not None, (
        "omicairn/cli.py has no module docstring — the Subcommands: list "
        "lives there. Either restore the docstring or update this lint."
    )
    # Match `  <name>   <rest-of-line>` rows. Requires at least two
    # spaces of indent (the docstring block uses two) and at least two
    # spaces between name and description to avoid matching prose like
    # `  (note: this is X)`.
    return set(re.findall(r"^  ([a-z_]+)\s{2,}\S", docstring, re.MULTILINE))


def _epilog_subcommand_names() -> set[str]:
    """Parse the `epilog=textwrap.dedent(...)` block inside `main()`
    for subcommand rows."""
    src = _cli_source()
    m = re.search(
        r'epilog=textwrap\.dedent\("""\n(.*?)"""\s*\)',
        src,
        re.DOTALL,
    )
    assert m is not None, (
        "omicairn/cli.py has no `epilog=textwrap.dedent(...)` triple-quoted "
        "block in main(). Either restore it or update this lint."
    )
    epilog = m.group(1)
    # Same row shape as the docstring: `  <name>  <description>`. The
    # epilog uses deeper indent (12 spaces) because it's nested inside
    # textwrap.dedent + the triple-quoted literal.
    return set(re.findall(r"^\s+([a-z_]+)\s{2,}\S", epilog, re.MULTILINE))


# ---------------------------------------------------------------------------
# Sanity: every scan finds at least one subcommand
# ---------------------------------------------------------------------------


def test_every_cli_scan_finds_at_least_one_subcommand():
    """Each of the five static scans must return a non-empty set. An
    empty result almost certainly means the regex stopped matching
    after a refactor (e.g. someone moved the subparsers out of
    main()), not that the CLI genuinely has zero subcommands. Fail
    loud so the refactor updates this lint instead of silently
    passing empty-vs-empty comparisons.

    Catching this separately (rather than letting the parity tests
    "pass" via empty-set equality) is essential: an empty set equals
    an empty set, which would silently pass every parity check below."""
    scans = {
        "cmd_<name> functions": _cmd_function_names(),
        "sub.add_parser calls": _add_parser_names(),
        "set_defaults(func=) handlers": _set_defaults_handler_names(),
        "docstring subcommand list": _docstring_subcommand_names(),
        "epilog subcommand list": _epilog_subcommand_names(),
    }
    empty = [label for label, s in scans.items() if not s]
    assert not empty, (
        f"CLI subcommand scans returned empty for: {empty}. The lint "
        f"regex likely no longer matches the source (cli.py was "
        f"refactored). Update tests/test_cli_lint.py to track the new "
        f"shape before asserting parity."
    )


# ---------------------------------------------------------------------------
# Parity: the runtime surfaces (3 + 4 + 5) must all agree on the same set
# ---------------------------------------------------------------------------


def test_cmd_functions_match_add_parser_registrations():
    """Every `cmd_<name>()` handler must have a corresponding
    `sub.add_parser("<name>")` registration, and vice versa. A
    mismatch means either dead code (unregistered handler) or a
    registered subcommand whose handler binding is missing.

    Drift this catches: someone adds `cmd_purge()` to omicairn/cli.py,
    writes a plausible body, but forgets to wire up
    `sub.add_parser("purge", ...)` in main(). Every existing test
    passes — the handler is dead code — and the failure is invisible
    until someone wonders why `omicairn purge` raises an "invalid choice"
    error."""
    handlers = _cmd_function_names()
    parsers = _add_parser_names()
    only_handlers = sorted(handlers - parsers)
    only_parsers = sorted(parsers - handlers)
    errors: list[str] = []
    if only_handlers:
        errors.append(
            f"cmd_<name> functions WITHOUT a sub.add_parser registration "
            f"(dead code): {only_handlers}"
        )
    if only_parsers:
        errors.append(
            f"sub.add_parser registrations WITHOUT a cmd_<name> handler "
            f"(runtime AttributeError on dispatch): {only_parsers}"
        )
    assert not errors, "CLI handler/parser drift:\n  " + "\n  ".join(errors)


def test_set_defaults_func_matches_cmd_functions():
    """Every `sub.add_parser` block must end with
    `set_defaults(func=cmd_<name>)`, and the name passed must match a
    real cmd_<name> function. This is the third leg of the runtime
    tripod: handler definition, parser registration, and binding.

    Drift this catches: renaming `cmd_run` to `cmd_drive_pipeline`
    without updating `p_run.set_defaults(func=cmd_run)` — argparse
    happily stores a reference to a free-variable lookup that will
    NameError on the first `omicairn run` invocation."""
    handlers = _cmd_function_names()
    bindings = _set_defaults_handler_names()
    # Every binding name must resolve to a real handler.
    dangling = sorted(bindings - handlers)
    unbound = sorted(handlers - bindings)
    errors: list[str] = []
    if dangling:
        errors.append(
            f"set_defaults(func=cmd_X) references that have no matching "
            f"def cmd_X(): {dangling}"
        )
    if unbound:
        errors.append(
            f"cmd_<name> functions that no set_defaults(func=) binds to: "
            f"{unbound}"
        )
    assert not errors, (
        "CLI set_defaults/handler drift:\n  " + "\n  ".join(errors)
    )


# ---------------------------------------------------------------------------
# Parity: documentation surfaces (1 + 2) must match the runtime surface
# ---------------------------------------------------------------------------


def test_module_docstring_subcommand_list_matches_registered_parsers():
    """The `Subcommands:` block in the module docstring must list
    every `sub.add_parser` registration and nothing else. A drifting
    docstring is the polite kind of drift: it confuses readers but
    doesn't crash. Still worth catching at edit time because a stale
    docstring is a loud signal that someone removed a subcommand and
    forgot to clean up."""
    docstring = _docstring_subcommand_names()
    parsers = _add_parser_names()
    missing_from_doc = sorted(parsers - docstring)
    extra_in_doc = sorted(docstring - parsers)
    errors: list[str] = []
    if missing_from_doc:
        errors.append(
            f"registered subcommands missing from module docstring: "
            f"{missing_from_doc}"
        )
    if extra_in_doc:
        errors.append(
            f"module docstring lists subcommands that are not registered: "
            f"{extra_in_doc}"
        )
    assert not errors, (
        "CLI module docstring drift:\n  " + "\n  ".join(errors)
    )


def test_epilog_subcommand_list_matches_registered_parsers():
    """The `epilog=textwrap.dedent(...)` block inside main() is what
    `omicairn --help` prints. It must agree with the subparsers
    registered in the same function. Drift here is the most
    user-visible of the documentation drifts: a user types `omicairn
    --help`, reads "literature  fetch/re-fetch literature", tries
    `omicairn literature`, and gets "invalid choice" because someone
    removed the parser but left the epilog."""
    epilog = _epilog_subcommand_names()
    parsers = _add_parser_names()
    missing_from_epilog = sorted(parsers - epilog)
    extra_in_epilog = sorted(epilog - parsers)
    errors: list[str] = []
    if missing_from_epilog:
        errors.append(
            f"registered subcommands missing from --help epilog: "
            f"{missing_from_epilog}"
        )
    if extra_in_epilog:
        errors.append(
            f"--help epilog lists subcommands that are not registered: "
            f"{extra_in_epilog}"
        )
    assert not errors, "CLI --help epilog drift:\n  " + "\n  ".join(errors)


def test_docstring_and_epilog_agree():
    """Final parity check: the two documentation surfaces must agree
    with each other. This is implied by both agreeing with the parsers
    (transitive), but asserting it directly gives a cleaner error
    message when both docs are stale and only one parser registration
    was removed."""
    docstring = _docstring_subcommand_names()
    epilog = _epilog_subcommand_names()
    assert docstring == epilog, (
        f"module docstring and --help epilog list different subcommands. "
        f"docstring: {sorted(docstring)}; epilog: {sorted(epilog)}. "
        f"Keep the two lists in sync — they're both manually maintained "
        f"copies of the same set."
    )
