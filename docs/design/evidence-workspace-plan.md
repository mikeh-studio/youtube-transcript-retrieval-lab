# Evidence workspace design and implementation

The approved direction consolidates the local app into **Library** and **Search**,
with **Evaluation** and **Settings** as persistent utilities. The selected Search
mock uses a transcript reader; Evaluation centers on saved runs and inspectable
measurements. This document records the implemented behavior and acceptance scope.

## Find and inspect evidence

Find is the default Search mode. The selected video scope stays visible, and a
searchable picker changes it. A passage outline and original transcript reader
share exact source identities and timestamp links. Consecutive passages from the
same video share a heading without changing ranked order across videos. Nearby
transcript is excluded from answer evidence until explicitly selected.

An optional answer binds to the server's canonical evidence snapshot. Its cost
review binds question, passages, provider/model, language and output budget;
changed or stale evidence invalidates the estimate. Answer generation performs no
silent fresh retrieval or video fallback. Citation IDs must belong to the selected
evidence. No speaker is inferred solely from the video title.

Compare retains the A/B retrieval, relevance labeling and query-set handoff.
Secondary operator tools remain reachable without becoming primary navigation.

## Review saved evaluations

Evaluation opens saved run history. Query sets retains dataset creation, labels
and existing browser storage. Explicit CLI, API and offline benchmark JSON imports
become sanitized immutable local snapshots; opening a report launches no model,
grading job or private-folder scan.

The overview separates the report's two acceptance targets, retrieval ranking,
answer/evidence quality, interaction measures and execution/cost. Initial retrieval
and final evidence selection are distinct stages. Precision/Recall use the selected
cutoff; nDCG and MRR retain their explicit cutoff. AUC needs a labeled candidate
pool. Semantic supporting-passage recall and citation relevance require grades;
exact quote/timestamp checks do not establish relevance.

Metric links use recorded contributor IDs scoped to the report/profile, stage,
cutoff and workflow. Where mappings are absent, the overview explains the limit.
Question views group workflow attempts by question. Failure details preserve the
recorded reason, rejected attempted output and ranked evidence; long diagnostics
remain available through disclosures. Canonical quotes and source links remain
inspectable.

Version comparisons require compatible known corpus, query-set, criteria and
dataset identities, model/effort, stage and cutoff. Conflicting scoped metadata
rejects a match. Unknown identifiers never certify compatibility. Cost eligibility
requires paired recorded grades, complete case costs and matching cost and
failed-attempt bases. API-equivalent estimates are not subscription invoices, and
reasoning tokens are included in output rather than added a second time.

## Settings and spending

Settings has explicit Save/Cancel for safe browser defaults. Credentials remain in
server configuration; only their configuration state is exposed. Search needs no
answer-provider call. Generation always requires estimate review. Dollar pricing
is not configured for new answers, so the UI shows unavailable pricing and
requires explicit acknowledgement instead of inventing a rate.

## Acceptance and review

Terra High implemented the approved direction. Astra High reviewed the UI against
the approved mocks, source contracts and browser evidence. Its initial four P2
findings led to fixes for failure preservation, scoped drilldowns, baseline
compatibility and keyboard focus. The follow-up found no remaining P1/P2 in the
reviewed local workflow; [findings and limits](evidence-workspace-review.md) record
what that acceptance covers.

Offline tests cover source continuity, strict citations, normalization/sanitizing,
comparison eligibility, contributor navigation, focus, failure details and preserved
legacy routes. No paid evaluation or frozen-result changes were needed. Screenshots
show real local reader and saved-report states; synthetic browser fixtures stay in
tests and never supply results to a historical report.
