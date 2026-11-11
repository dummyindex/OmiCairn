"""Project Descriptor Protocol — make any project folder OmiCairn-interoperable.

A OmiCairn workspace is already semi-self-describing (RESEARCH.md, STATE.md,
knowledge/graph.jsonl). This module generalizes that into a portable
descriptor so OmiCairn can interoperate with *foreign* project folders too —
ones made manually, by other agents, by collaborators, or by other OmiCairn
runs. Two operations sit on top of this protocol (see cli `omicairn project`):

  - SCAN / distill: read a folder; if it carries a descriptor (or is a OmiCairn
    workspace) read it deterministically, else an agent infers one. Foreign
    findings/hypotheses are ingested ``verified=False`` — recorded but not
    trusted until OmiCairn re-derives them (skeptical-of-external-claims).
  - EXTEND (next slice): seed a fresh workspace's graph from a foreign
    project + a new goal, then run the pipeline to push the research further.

Design choices (locked with the user):
  - ``project.json`` is the CANONICAL, machine-readable descriptor. It is
    never produced by parsing markdown.
  - ``PROJECT.md`` is an OPTIONAL, one-way human view rendered *from* the
    descriptor. OmiCairn never reads it back.
  - ``knowledge/graph.jsonl`` remains the deep knowledge layer; the
    descriptor carries a compact summary + pointers, not a full copy.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
import pathlib
from typing import Any

from .constants import (
    KG_GRAPH_FILE,
    KNOWLEDGE_DIR,
    PROJECT_DESCRIPTOR_FILE,
    PROJECT_MD_FILE,
    PROJECT_SCHEMA_VERSION,
    RESULTS_JSON,
    NodeType,
)
from . import knowledge_graph as kg

# Allowed `origin` values: who/what produced the project.
ORIGINS = ("omicairn", "manual", "agent", "other")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@dataclasses.dataclass
class ProjectDescriptor:
    """The portable project manifest (serialized to ``project.json``).

    ``knowledge`` holds COMPACT summaries (lists of dicts with title/status/
    verified/evidence), not the full graph — the graph.jsonl pointer in
    ``structure`` is authoritative for depth.
    """
    project: str = ""                       # slug
    title: str = ""
    domain: str = ""
    origin: str = "other"                   # one of ORIGINS
    created_by: str = ""
    research_question: str = ""
    structure: dict[str, Any] = dataclasses.field(default_factory=dict)
    knowledge: dict[str, list] = dataclasses.field(default_factory=dict)
    open_questions: list[str] = dataclasses.field(default_factory=list)
    suggested_extensions: list[str] = dataclasses.field(default_factory=list)
    provenance: dict[str, Any] = dataclasses.field(default_factory=dict)
    schema_version: str = PROJECT_SCHEMA_VERSION
    scanned_at: str = ""
    scanned_by: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ProjectDescriptor":
        fields = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in (d or {}).items() if k in fields})


# ---------------------------------------------------------------------------
# Validation (deterministic — the agent's emitted descriptor must pass this)
# ---------------------------------------------------------------------------

_KNOWLEDGE_KEYS = ("goals", "hypotheses", "findings", "datasets")


def validate(d: dict[str, Any]) -> list[str]:
    """Return a list of schema problems; empty list == valid."""
    errs: list[str] = []
    if not isinstance(d, dict):
        return ["descriptor is not a JSON object"]
    if not d.get("project"):
        errs.append("missing required field: project")
    origin = d.get("origin")
    if origin not in ORIGINS:
        errs.append(f"origin must be one of {ORIGINS}, got {origin!r}")
    for key in ("structure", "knowledge", "provenance"):
        if key in d and not isinstance(d[key], dict):
            errs.append(f"{key} must be an object")
    know = d.get("knowledge") or {}
    if isinstance(know, dict):
        for k in _KNOWLEDGE_KEYS:
            if k in know and not isinstance(know[k], list):
                errs.append(f"knowledge.{k} must be a list")
    for k in ("open_questions", "suggested_extensions"):
        if k in d and not isinstance(d[k], list):
            errs.append(f"{k} must be a list")
    return errs


# ---------------------------------------------------------------------------
# Deterministic describe — for OmiCairn workspaces (no LLM needed)
# ---------------------------------------------------------------------------

def _node_summary(n: kg.Node) -> dict[str, Any]:
    return {
        "title": n.title,
        "status": n.status or "",
        "verified": bool(n.extra.get("verified", True)),
        "evidence": list(n.evidence_paths),
    }


def describe_workspace(ws_root: pathlib.Path) -> ProjectDescriptor:
    """Build a descriptor from a OmiCairn workspace, deterministically.

    Reads the knowledge graph for the compact knowledge summary and
    RESEARCH.md (best-effort) for identity. Native OmiCairn knowledge is treated
    as ``verified=True`` (it came from OmiCairn's own evidence-gated graph)."""
    ws_root = pathlib.Path(ws_root)
    g = kg.load_graph(ws_root)

    domain = title = question = slug = ""
    try:
        from . import workspace as _ws
        rmd = _ws.load_workspace(ws_root).research_md
        domain, slug, question = rmd.domain, rmd.topic_slug, rmd.research_question
        title = slug
    except Exception:
        slug = ws_root.name

    knowledge = {
        "goals": [_node_summary(n) for n in g.nodes_of_type(NodeType.GOAL)],
        "hypotheses": [_node_summary(n)
                       for n in g.nodes_of_type(NodeType.HYPOTHESIS)],
        "findings": [_node_summary(n)
                     for n in g.nodes_of_type(NodeType.FINDING)],
        "datasets": [_node_summary(n)
                     for n in g.nodes_of_type(NodeType.DATASET_REF)],
    }
    structure = _detect_structure(ws_root)
    return ProjectDescriptor(
        project=slug or ws_root.name, title=title or ws_root.name,
        domain=domain, origin="omicairn", created_by="OmiCairn",
        research_question=question, structure=structure, knowledge=knowledge,
        provenance={"verified": True, "notes": "native OmiCairn workspace"},
        scanned_at=_now(), scanned_by="omicairn project describe",
    )


def _detect_structure(folder: pathlib.Path) -> dict[str, Any]:
    """Best-effort, deterministic structure pointers (relative paths)."""
    folder = pathlib.Path(folder)
    out: dict[str, Any] = {}
    if (folder / "data").exists():
        out["data"] = ["./data"]
    graph = folder / KNOWLEDGE_DIR / KG_GRAPH_FILE
    if graph.exists():
        out["graph"] = f"./{KNOWLEDGE_DIR}/{KG_GRAPH_FILE}"
    # execute results.json files
    results = sorted(folder.glob(f"*_execute/{RESULTS_JSON}"))
    if results:
        out["results"] = ["./" + str(p.relative_to(folder)) for p in results]
    return out


# ---------------------------------------------------------------------------
# Serialization + render
# ---------------------------------------------------------------------------

def write_descriptor(folder: pathlib.Path, d: ProjectDescriptor,
                     write_md: bool = True) -> pathlib.Path:
    """Write project.json (canonical) and, optionally, PROJECT.md (view)."""
    folder = pathlib.Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / PROJECT_DESCRIPTOR_FILE
    path.write_text(json.dumps(d.to_dict(), indent=2, ensure_ascii=False),
                    encoding="utf-8")
    if write_md:
        (folder / PROJECT_MD_FILE).write_text(
            render_markdown(d), encoding="utf-8")
    return path


def read_descriptor(folder: pathlib.Path) -> ProjectDescriptor | None:
    """Read project.json (JSON only — PROJECT.md is never parsed)."""
    path = pathlib.Path(folder) / PROJECT_DESCRIPTOR_FILE
    if not path.exists():
        return None
    try:
        return ProjectDescriptor.from_dict(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError):
        return None


def render_markdown(d: ProjectDescriptor) -> str:
    """One-way human view of the descriptor. Never parsed back."""
    lines = [
        f"# {d.title or d.project}", "",
        "> Generated from `project.json` — do not edit by hand; "
        "edit the JSON (the canonical descriptor) instead.", "",
        f"- **project**: {d.project}",
        f"- **domain**: {d.domain}",
        f"- **origin**: {d.origin}  ·  **created_by**: {d.created_by}",
        f"- **scanned**: {d.scanned_at} by {d.scanned_by}",
    ]
    if d.research_question:
        lines += ["", "## Research question", d.research_question]
    if d.structure:
        lines += ["", "## Structure"]
        lines += [f"- **{k}**: {v}" for k, v in d.structure.items()]
    know = d.knowledge or {}
    for kind in _KNOWLEDGE_KEYS:
        items = know.get(kind) or []
        if not items:
            continue
        lines += ["", f"## {kind.capitalize()} ({len(items)})"]
        for it in items[:50]:
            st = f"[{it.get('status')}] " if it.get("status") else ""
            v = "" if it.get("verified", True) else "  _(unverified)_"
            lines.append(f"- {st}{it.get('title', '')}{v}")
    if d.open_questions:
        lines += ["", "## Open questions"] + [f"- {q}" for q in d.open_questions]
    if d.suggested_extensions:
        lines += ["", "## Suggested extensions"] + \
                 [f"- {q}" for q in d.suggested_extensions]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Ingest a descriptor's knowledge into a graph (used by scan + extend)
# ---------------------------------------------------------------------------

def ingest_descriptor_to_graph(
    target_ws_root: pathlib.Path,
    d: ProjectDescriptor,
    mark_unverified: bool = True,
) -> dict[str, int]:
    """Fold a (typically foreign) descriptor's knowledge into a graph.

    Findings and hypotheses are recorded with ``extra.verified`` —
    ``False`` when ``mark_unverified`` (the default for foreign projects):
    OmiCairn records the claim but does not trust it until re-derived. Native
    OmiCairn descriptors (origin == 'omicairn') keep their stated verified flag.
    Returns counts by kind ingested.
    """
    target_ws_root = pathlib.Path(target_ws_root)
    foreign = d.origin != "omicairn"
    unverified = mark_unverified and foreign
    counts = {"findings": 0, "hypotheses": 0, "datasets": 0}
    know = d.knowledge or {}
    src_tag = f"project:{d.project}"

    for f in (know.get("findings") or []):
        title = f.get("title")
        if not title:
            continue
        node = kg.Node(
            id=kg.finding_id(f"{d.project}:{title}"),
            type=NodeType.FINDING, title=title, body=f.get("body", ""),
            origin="external" if foreign else "omicairn",
            evidence_paths=list(f.get("evidence") or []),
            tags=[src_tag],
            extra={"verified": False if unverified
                   else bool(f.get("verified", True)),
                   "source_project": d.project, "source_origin": d.origin},
        )
        kg.upsert_node(target_ws_root, node)
        counts["findings"] += 1

    for h in (know.get("hypotheses") or []):
        title = h.get("title")
        if not title:
            continue
        node = kg.Node(
            id=kg.hypothesis_id(f"{d.project}:{title}"),
            type=NodeType.HYPOTHESIS, title=title, body=h.get("body", ""),
            status=h.get("status", ""),
            origin="external" if foreign else "omicairn",
            evidence_paths=list(h.get("evidence") or []),
            tags=[src_tag],
            extra={"verified": False if unverified
                   else bool(h.get("verified", True)),
                   "source_project": d.project, "source_origin": d.origin},
        )
        kg.upsert_node(target_ws_root, node)
        counts["hypotheses"] += 1

    for ds in (know.get("datasets") or []):
        title = ds.get("title") or ds.get("name")
        if not title:
            continue
        node = kg.Node(
            id=kg._slug(f"dataset:{d.project}:{title}"),
            type=NodeType.DATASET_REF, title=title, body=ds.get("body", ""),
            origin="external" if foreign else "omicairn",
            evidence_paths=list(ds.get("evidence") or []),
            tags=[src_tag],
            extra={"source_project": d.project, "source_origin": d.origin},
        )
        kg.upsert_node(target_ws_root, node)
        counts["datasets"] += 1

    return counts
