# What's-new digest

Build a digest from indexed sources with `POST /v1/digest/whats-new`. Send
`{"source_ids":["source_id"],"language":"en"}` or
`{"window":{"start":"2026-03-01T00:00:00Z","end":"2026-03-08T00:00:00Z"}}`.
The window includes its start and excludes its end. The request accepts the
same `provider` and `model` options as key areas, and language defaults to
`en`. The route returns `201` with `{"ok":true,"digest":{...}}`. An empty
window saves an empty digest. Browser writes use the standard browser session
and CSRF token.

The release date comes from stored publication metadata, then `ingested_at`.
For older records, the digest can use the stored transcription creation time
or recover a successful ingest timestamp from the ingest log. The digest
reports this effective date in `published_at`. A source with no usable date
is excluded from windows and prior scopes. An explicit selection of such a
source returns an input error. Equal dates are excluded from the prior scope.

Each release uses up to 12 distinct, line-cited points from key areas. Points
with no importance rating use 1 on the 1–3 scale. A hybrid query retrieves five
results per claim and per translated variant. Translations target languages
of earlier sources and search within the corresponding earlier sources.
The classifier receives all claims and their passages in one call per release
with claims. Earlier source IDs and all retrieved passages are saved with the
claims, including covered claims. Passage IDs are local to a claim. A
`covered`, `extends`, or `contradicts` prediction with no resolvable passage
ID becomes `new` and is flagged as `downgraded`. A `new` prediction describes
the available retrieval evidence. Citation validity measures ID resolution
and does not measure semantic support.

Highlights rank by importance multiplied by 1.0 for `new` or `contradicts`,
or 0.6 for `extends`. The release also reports `covered_count` and a
`disagreements` list with the contradiction highlights. Digest quality
aggregates checked claims, downgraded claims, and the fraction of submitted
citation IDs that resolve. Repeated IDs receive credit once per claim. The
fraction is null when no citations were submitted. `usage` contains classifier
token counts if supplied by the configured generation adapter.

- `GET /v1/digest/{id}` returns the saved digest. `GET /v1/digests` lists
  summary metadata, release titles, and the number of reviewable claims.
- `POST /v1/digest/{id}/feedback` accepts
  `{"item_id":"<highlight claim_id>","rating":"up","note":"optional"}`.
  Ratings are `up` or `down`. Feedback is recorded locally.
- Digests and feedback are owner-readable and owner-writable under
  `data/runtime/digests/` and `data/runtime/digest_feedback.jsonl`.

## Review and score

In Label review, choose **Digest novelty** and select a saved digest. The
draft includes `new` and `contradicts` claims with the actual retrieved
passages and citation references. Approve a `new` claim if it is genuinely
new relative to the prior evidence. Approve a contradiction if the cited
evidence establishes an incompatible earlier assertion. Finalization retains
approvals and rejections so both count toward precision. The same task is
available through `POST /v1/labels/jobs` with
`{"task":"digest_novelty","digest_id":"<id>"}`.

Run offline scoring with `.venv/bin/python -m evals.digest.runner --digest
<saved-digest.json> --labels <finalized-labels.jsonl> --preferences
<choices.jsonl>`. Any of those files can be supplied independently. Precision
is the owner approval rate within each proposed relation. Evidence validity
uses citation resolution across all claims in the supplied digest, or the
labeled claims when only labels are supplied. Each preference row is
`{"choice":"digest"}`, `{"choice":"baseline"}`, or `{"choice":"tie"}`. Ties
count as half a win. Metrics with no observations are null.
`--self-novelty` and `--empty-library` run synthetic checks without provider
calls.
