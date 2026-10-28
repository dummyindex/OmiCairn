# Core Beliefs — OmiCairn Engineering

These beliefs guide every decision in the OmiCairn codebase. When in doubt,
defer to these principles.

## 1. The repository is the single source of truth

Any knowledge that should influence agent behavior must be committed to
the repo. Information that lives only in Slack, Google Docs, or
someone's head is invisible to OmiCairn agents. If it matters, write it
down in a file that agents can read.

## 2. The orchestrator must stay thin

The orchestrator's job is to launch phases, verify contracts, and
advance state. All research intelligence lives inside the Claude Code
phase sessions. If you're adding research logic to `omicairn/*.py`, you're
in the wrong layer — put it in a prompt or agent spec instead.

## 3. Multi-turn agent sessions over single-turn API calls

A single Claude Code session can make hundreds of tool calls, inspect
results, iterate, and self-correct. This is fundamentally better than
chaining 60+ single-turn LLM calls through a Python orchestrator.
Never go back to the AutoResearchClaw pattern.

## 4. Mechanical enforcement over written rules

Contracts, cross-checks, and tool allowlists are enforced by code.
Written instructions in prompts are suggestions the agent may ignore
under pressure. When a rule truly matters, encode it as a verifier
that blocks phase completion.

## 5. Domain knowledge flows from RESEARCH.md, never from code

OmiCairn is a generic research system. No gene names, method names,
disease terms, or field-specific keywords belong in infrastructure
code. All domain specificity comes from RESEARCH.md fields at runtime.
This ensures OmiCairn works for any research domain.

## 6. Every agent failure is a harness failure

When OmiCairn produces bad output, the correct response is to diagnose
the root cause (prompt ambiguity, missing tool permission, bad
contract, architectural gap) and fix the system. Fixing output by
hand defeats the purpose of building OmiCairn.

## 7. Small loops, fast feedback

A single-phase smoke test on 20-row synthetic data is worth five
full end-to-end runs that take hours. Iterate fast on cheap tests,
then validate on real data.

## 8. Agents replicate patterns faithfully — including bad ones

If the codebase contains a suboptimal pattern, agents will reproduce
it everywhere. This means architectural rules must be mechanically
enforced (linters, contracts, cross-checks), not just documented.
Periodic entropy sweeps catch drift before it compounds.

## 9. Scarcity inversion: compute is cheap, attention is expensive

OmiCairn can run many agent sessions in parallel. The bottleneck is human
review time. Design the system so that human attention is only needed
for genuine judgment calls, not routine verification that machines
can handle.

## 10. Honest science above all

Every reported number in a OmiCairn run must trace back to a real
computation on real data. Simulated results, hallucinated numbers, and
overclaimed conclusions are system failures. The cross-check verifier
and the REVIEW phase (with its devil's-advocate counter-critique) exist
specifically to catch these. Negative results are results: every run
records refuted, inconclusive and untestable hypotheses, and dropped or
failed arms, in `negative_results.md` with evidence paths.

## 11. The knowledge base is the product

The durable product of a OmiCairn run is a **knowledge base/graph**:
every hypothesis, analysis, reproducible result, figure, dataset pointer,
and wet-lab metadata record, linked by provenance (supports / refutes /
derived-from / raises / cites), together with the stage artifacts that
back it (`results.json`, `analysis.md`, figures, code, cross-check
findings, review notes, the negative-results record).

Consequences:
- Findings are first-class and foundational, not by-products. The graph
  is written continuously as the science happens.
- The graph outlives any single run and is the unit we accumulate across
  runs (`omicairn labkb`).
- "What do we now know, with what evidence, reproducibly?" is the success
  question. Interpreting and communicating the analyses is the human
  expert's job; OmiCairn does not draft or format manuscripts.
- When any summary and the graph disagree, the graph (with its on-disk
  evidence) wins.

## 13. Trust reproducible internal results over external claims

Published papers and literature are evidence, not gospel. The field has
real rates of irreproducibility, p-hacking, and outright fraud. OmiCairn
should be **skeptical of external claims** and give **more epistemic
weight to its own reproducible results** — numbers it computed from real
data, with code and an environment lockfile on disk — than to an
unreplicated assertion in a paper.

Mechanics:
- In the knowledge graph, a `finding`/`result_ref` backed by an on-disk
  `evidence_path` outranks a `cites`-only claim. The honesty rule
  (belief 10, enforced in `knowledge_graph.set_hypothesis_status`) already
  refuses to mark a hypothesis supported/refuted without resolvable
  internal evidence — external citations alone never suffice.
- **Source-credibility weighting is OPTIONAL and configurable.** When
  `prefer_reputable_sources` is enabled (omicairn_config.yaml), papers and
  datasets from well-established groups and integrity-clean sources get
  a higher prior weight than unknown or flagged ones. This is an
  admitted *bias*: it is off by default (neutral), opt-in, and we expect
  to revisit it soon — so it must never be hardcoded into infrastructure
  (belief 5) and must be a knob, not an assumption. Credibility weight
  adjusts ranking/prior only; it never overrides on-disk reproducible
  evidence.

## 12. A workspace is a multi-goal, multi-hypothesis accumulator

Modern research is systematic and network-like: dense, interconnected
analysis, not a single headline claim. A OmiCairn workspace holds
**one or more goals**; each goal carries **one or more hypotheses**; and
new hypotheses are **raised on the fly** as execution surfaces them
(from results, literature, or agent discussion). The pre-registered
set declared at PLAN time is a seed, not a ceiling.

Consequences:
- Reject "one claim per run." One run may validate or refute many
  hypotheses; one workspace may pursue several goals that share data and
  machinery.
- Hypotheses have lifecycle state (proposed → testing → supported /
  refuted / inconclusive / superseded) and provenance back to the
  evidence that raised and resolved them. On-the-fly hypotheses are
  recorded with the same rigor as pre-registered ones, explicitly
  flagged as post-hoc so multiple-testing accounting stays honest.
- The orchestrator's job grows from "run one pipeline to answer one
  question" to "grow a workspace's knowledge graph." This
  is a *direction*; see ARCHITECTURE.md § "Target: knowledge graph as the
  product" for how the current single-goal pipeline is the first special
  case of it.
