# Real retrieval pilot

`make eval` uses the actual `TextProcessor`, `VideoLibrary`, `RAGEngine.search`,
and `LocalRAGService.retrieve` pipeline. It rebuilds an index in a temporary
library from supplied transcript chunks. It does not initialize the normal
service, load the user's library/feedback, read dotenv files, acquire videos,
call paid providers, or download models. Network connections are disabled.

`make eval-smoke` retains the separate fixture runner. Fixture results are
component checks and do not establish real retrieval quality.

The Query sets workbench shows annotation completeness and labels its
retrieved-pool metric accordingly. [Synthetic UI example](../docs/media/evaluation-annotation-metrics.png)
shows this presentation; it is not a measured retrieval result.

## Prepare private inputs

Place corpus, dataset, and manifest files in ignored `data/evaluation/`, or outside
this checkout. The runner refuses tracked private inputs and requires private
paths inside this checkout to be ignored. Keep these files and per-query
results out of commits. Reusable configuration JSON may be tracked.

Use transcript chunks from a local corpus you are permitted to process.
Maintain a stable chunk order: evidence IDs are `video_id:chunk_index`, with
zero-based indices. Only these corpus fields are accepted:

```json
{
  "schema_version": 1,
  "videos": [{
    "video_id": "REPLACE_WITH_SOURCE_ID",
    "title": "REPLACE_WITH_SOURCE_TITLE",
    "language": "en",
    "chunks": [{"start": 0, "end": 10, "raw_text": "REPLACE_WITH_ACTUAL_TRANSCRIPT"}]
  }]
}
```

These are schema placeholders, not a curated corpus. Each video needs at least
one chunk; languages are `en` and `ja`. Timestamps must be finite with
`0 <= start < end`. The runner regenerates application embedding text from
`raw_text`; supplied embeddings, fixture scores, or semantic-term hints are
rejected. This pilot accepts presegmented transcript chunks; it does not fetch
or automatically segment transcript files.

Create each query from a concrete retrieval need, inspect candidate chunks,
and record relevance judgments: `0` irrelevant, `1` relevant, `2` strongly
relevant. Include difficult negatives and review the grading consistently.
Do not copy generated expectations into a file and call them reviewed labels.
Each JSONL line follows this shape:

```json
{"id":"REPLACE_WITH_QUERY_ID","query":"REPLACE_WITH_QUERY","language":"en","query_type":"factual","difficulty":"easy","group":"REPLACE_WITH_ANALYSIS_GROUP","split":"dev","unanswerable":false,"judgment_source":"REPLACE_WITH_REVIEWER_AND_REVIEW_METHOD","judgments":{"REPLACE_WITH_SOURCE_ID:0":2}}
```

`query_type`, `difficulty`, `group`, and `judgment_source` are required nonempty
strings. Query IDs must be unique. Every judgment must reference a corpus
chunk and have an integer grade. An answerable query needs at least one
positive grade. An explicitly unanswerable query must have only zero grades;
its ranking metrics are null. An optional `video_ids` list restricts retrieval
to known videos; positive judgments must lie inside that scope.

Use distinct dev and holdout files. Dev runs never open the holdout dataset.
Holdout runs verify the dev file as well and reject duplicate query IDs or
normalized query text across the two splits. This exact-duplicate check cannot
detect paraphrase, topic, or source leakage; establish that separation during
label review. `group` is an analysis category, not an enforced leakage barrier.

Hash the actual files after review:

```sh
python -m evals.contracts data/evaluation/corpus.json
python -m evals.contracts data/evaluation/dev.jsonl
```

Create a local manifest using those digests and an immutable Hugging Face model
commit. Placeholder values below must be replaced; this example cannot run:

```json
{
  "schema_version": 1,
  "evidence_level": "user_asserted_reviewed",
  "embedding": {
    "model": "intfloat/multilingual-e5-base",
    "revision": "REPLACE_WITH_40_CHARACTER_MODEL_COMMIT"
  },
  "corpus": {"path": "corpus.json", "sha256": "REPLACE_WITH_CORPUS_SHA256"},
  "datasets": {
    "dev": {"path": "dev.jsonl", "sha256": "REPLACE_WITH_DEV_SHA256"},
    "holdout": {"path": "holdout.jsonl", "sha256": "REPLACE_WITH_HOLDOUT_SHA256"}
  }
}
```

Paths are resolved relative to the manifest. A dev-only pilot may omit
`holdout`. Allowed evidence levels are `real_component_smoke` for synthetic
component checks and `user_asserted_reviewed` for supplied reviewed labels.
The latter records a user assertion, not independent verification of review.
The harness creates no curated labels or real-quality benchmark results.

The model must already exist at its exact cached snapshot. Both
`intfloat/multilingual-e5-base` and `intfloat/multilingual-e5-large` are supported
and reported under their actual identities. There is no model substitution.
An optional embedding `local_path` must identify the model's own
`models--OWNER--NAME/snapshots/COMMIT` directory. The default cache honors
`HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, and `HF_HOME`. Missing models, incomplete
snapshots, hashing fallback, tokenizer failure, and index-space mismatch fail
the evaluation. Install/cache models separately before running this offline
workflow; the runner has no download option.

## Configure and run

The tracked baseline uses dense, lexical, and baseline hybrid retrieval.
A minimal reusable configuration is:

```json
{
  "schema_version": 1,
  "top_k": 10,
  "latency_repetitions": 3,
  "runs": [{"name": "baseline", "retrieval_mode": "hybrid", "retrieval_profile": "baseline_rrf", "reranker": "none"}]
}
```

`retrieval_mode` is `dense`, `lexical`, or `hybrid`; hybrid profiles are
`baseline_rrf` and `optimized_v1`. `top_k` is 10–12 to support the @10 metrics.
Repetitions are 1–20. Feedback is disabled. Agentic acquisition and generated
answer evaluation are outside this pilot.

For `reranker: "cross_encoder"`, add a top-level config object
`"reranker": {"model": "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1", "revision": "REPLACE_WITH_40_CHARACTER_MODEL_COMMIT"}`.
An optional `local_path` follows the same exact-snapshot convention. The
cross-encoder is loaded before evaluation, and silent unavailable-reranker
fallback is treated as failure.

```sh
make eval PYTHON=/path/to/python MANIFEST=data/evaluation/manifest.json OUT=evals/reports/dev-001
make eval PYTHON=/path/to/python MANIFEST=data/evaluation/manifest.json SPLIT=holdout HOLDOUT_FLAG=--allow-holdout OUT=evals/reports/holdout-001
make eval-smoke PYTHON=/path/to/python
make experiment PYTHON=/path/to/python ARGS='--help'
```

Choose a fresh output directory for each run. The runner reserves the output
before model startup, refuses concurrent writers, and never replaces an
existing `results.json`, including a failed report. A process killed before
cleanup may leave a lock; use a new output directory. Direct CLI use is
`python -m evals.real_runner --manifest ... --config ... --out ...`.
The default split is dev. Holdout requires both `--split holdout` and
`--allow-holdout`. Failed evaluations exit with status 2 and, when output can be
reserved, save an inspectable failure report.

## Interpret the report

The local `results.json` contains:

- Git commit, dirty-state and source-tree hashes; manifest, corpus, dataset,
  and configuration hashes; effective configuration; installed package versions;
  index type/count/dimensions, serialized size and digest; actual embedding backend,
  model revision, dimensions, cached snapshot, and reranker identity.
- Per-query ranked chunk IDs, failures, judged fraction, metadata, latency
  samples, and recall@5/10, MRR@10, and graded nDCG@10. No query or transcript
  text is copied into reports. IDs and metadata can still be private.
- Overall and group/language/query-type/difficulty means, query counts,
  failure counts, and seeded query-bootstrap 95% intervals (10,000 resamples).
  Failed answerable queries receive zero scores and remain in denominators.
  Unanswerable queries are retained with null ranking metrics.
- p50 and p95 retrieval timings, with nearest-rank p95. They include application
  retrieval and exclude startup/index construction. The shared process has no
  explicit warmup; later requests benefit from caches. Setup time is separate. Per-stage dense/lexical durations and statuses are
  retained for every repetition; skipped and unavailable stage timings are explicit.

Recall and nDCG are relative to the supplied positive judgments. Unjudged
chunks count as nonrelevant, so incomplete judgments can distort comparisons;
`judged_fraction` exposes coverage of returned results. A small pilot's
bootstrap intervals do not establish generalization to new topics/sources.
Returned chunks for an unanswerable query are diagnostic only: the runner does
not test answer generation or abstention. Any degraded retrieval or failed
requested reranking makes the report fail. A completed execution is evidence
that this configured pilot ran; it makes no promotion or deployment decision.
