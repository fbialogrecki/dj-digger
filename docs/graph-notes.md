# Knowledge-graph notes

Rebuilt on 2026-09-05 from the post-1.0 refactor at `007882e` plus the
specification/README updates in the working tree. This replaces the historical
0.5/0.6 analysis: its module names, counts and centrality measurements no longer
describe the current application.

## Corpus and outputs

The graph covers 100 code/configuration files and 10 current documents, including
application code, tests, scripts, the specification, architecture notes and CI
workflows. Local AST extraction contributes 3,034 nodes; document extraction
contributes 61 nodes and 193 relations, including references to existing code
symbols. The resulting undirected navigation graph has **3,095 nodes, 8,181 edges
and 126 named communities**.

`.graphifyignore` excludes agent tooling, historical design plans, recorded
third-party payloads and this generated commentary. Test code using those
recordings remains indexed. The detector also appends graphify memory files;
these were explicitly removed from the extraction corpus to prevent historical
answers from feeding back into the new architecture graph. Specification reading
followed the generated section map rather than reading the document end to end.

Local generated outputs remain ignored by Git:

- [Interactive graph](../graphify-out/graph.html), open directly in a browser.
- [Graph report](../graphify-out/GRAPH_REPORT.md), with community cohesion scores.
- [Navigation data](../graphify-out/graph.json) and
  [raw extraction](../graphify-out/extraction.json).
- [Extraction diagnostics](../graphify-out/GRAPH_HEALTH.md) and their
  [machine-readable form](../graphify-out/diagnostics.json).

The manifest records all 110 input files; semantic results are cached for all
10 documents. Rebuild after code or contract changes with the graphify skill,
retaining the corpus exclusions above. Graphify is installed separately from
the application's locked environment; it is not a runtime dependency.

## How to read the graph

This is a navigation aid, not proof of dependency direction or correctness.
The default graph is undirected. Multiple relations between the same pair of
nodes collapse into one edge, and imports of external modules can lack a target
node. Raw extraction preserves the original evidence for inspection.

The extraction diagnostic reports **519 dangling-endpoint edges** (all imports
or dependency declarations), **320 same-endpoint edges merged in the undirected
view**, no missing endpoint fields and no self-loops. These are limitations of
the extractor/export, not newly discovered application defects. No external
nodes or call relationships were invented to make the diagnostic pass.

The AST emits 459 inferred `uses` relations with weight 0.8. These still need
source verification; the old graph's confidence-0.5 filter is not a sufficient
check for this extractor version. A reference or shared module does not
establish a runtime call. Semantic contract-to-symbol references
identify implementation evidence; they do not prove that every path satisfies
the contract. Use the executable boundary tests and current specification for
that assessment.

`Track` is the largest hub (degree 283), followed by TUI test helpers `run()`
(185) and `make_app()` (150). These reflect shared data and extensive integration
tests. A high degree alone does not justify splitting a class. In particular,
the historical description of `DiggerApp` as owning all business operations is
obsolete: it now composes controllers and routes lifecycle and UI actions.

## Useful architecture paths

Start with these contracts and their code references in the graph:

- **Runtime and cancellation:** `ApplicationServices`, `OperationCoordinator`
  and `OperationHandle` connect lazy resource ownership to operation settlement.
- **Download effects:** the shared single/batch workflow connects
  `DownloadWorkflow`, file publication and `PublishedFileUnrecorded`, including
  the case where the file exists but library persistence fails.
- **Presentation and persistence:** typed account/profile answers connect to
  `AccountService`; committed status mirrors connect to `TrackState` and render
  updates without per-row database queries.
- **Database lifecycle:** single-thread ownership and schema registration link
  to `Database` and the schema/backup helpers.
- **Diagnostics:** literal, redacted external text links to `log_safe_text()`.

Useful follow-up questions are how a completed file is recorded after the user
switches playlists, and why cancellation retains an operation slot until its
workers settle. Consult source evidence along those paths; graph reachability
alone cannot answer the concurrency semantics.

## Verification and measurement limits

The exported graph has unique node IDs, valid exported edge endpoints and
community assignments for every node. Semantic references resolve against the
AST/document node set. The raw diagnostic limitations above remain visible.

Host semantic-agent token counters were unavailable. `cost.json` marks this run's
usage as unknown, and the report does not present placeholder zeroes as measured
cost. The local graphify benchmark estimated 4.0× token reduction, but its naive
154,750-word corpus differs from this build's filtered corpus. That estimate is
not measured model usage or an application responsiveness benchmark. Executed
application checks remain documented in [refactor verification](refactor/verification.md).
