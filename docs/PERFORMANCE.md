# Ungraded retrieval performance

`evals/performance_runner.py` measures local retrieval on a frozen, private
library snapshot. It runs the production `TextProcessor`, `VideoLibrary`,
`RAGEngine`, and `LocalRAGService.retrieve` paths. The result kind is
`ungraded_retrieval_performance`; quality is always `not_evaluated`. It cannot
serve as a relevance, noninferiority, or promotion gate.

## Private inputs and offline execution

Create a private snapshot of `data/library/videos/*/record.json` outside the
repository. The runner reads only those records, copies them into a fresh
temporary directory, and builds a new FAISS index there. It never loads the
source's saved index, because its embedding revision may be unknown. When
`--source-data` is supplied, the runner compares original and snapshot record
and manifest hashes before work and checks both again afterward. The manifest
must name exactly the available video records. The original is only read.
The source and snapshot arguments name their `data/` directories.

A complete cached `intfloat/multilingual-e5-large` Hugging Face snapshot is
required. Pass its local `snapshots/<40-character-revision>` path. The runner
sets offline environment flags and a socket guard before importing production
modules, disables dotenv loading and global service creation, clears provider
keys from its process environment, and rejects a hashing/model fallback. It
does not ingest, fetch transcripts, invoke ASR, or load feedback. The chosen
output directory must be new, private, and outside the repository and input
trees. The runner writes read-only `workload.json` and `report.json` there, or
a `failure.json` with the exception type if setup or execution fails.

```sh
python -m evals.performance_runner \
  --source-data /private/original/data \
  --snapshot-data /private/snapshot/data \
  --model-path /private/model-cache/snapshots/REVISION_SHA \
  --model-revision REVISION_SHA \
  --output /private/results/run-001
```

The default workload is six bounded, source-derived midpoint transcript
probes: three English and three Japanese, with both global and single-video
scope. It is deterministic for a given snapshot. These are fixed regression
inputs, not representative user questions or relevance judgments. To supply a
different private workload, pass `--workload /private/workload.json` using this
schema:

```json
{
  "schema": "ungraded_retrieval_workload_v1",
  "queries": [
    {"id": "q1", "text": "example query", "language": "en", "scope": {"kind": "global"}},
    {"id": "q2", "text": "example query", "language": "ja", "scope": {"kind": "video", "video_id": "VIDEO_ID"}}
  ]
}
```

The schema rejects judgments, answerability flags, and review claims. Keep
actual query text, video IDs, ranks, evidence hashes, and detailed reports
private. Public summaries should contain only deliberately sanitized aggregate
counts and timings.

## Measurement contract

The report pins the source commit; corpus, workload, config, and rebuilt index
hashes; actual embedding backend/model/revision/dimension; video and chunk
counts; package and machine metadata; setup timings; per-request timings; and
per-stage diagnostic status. It hashes the cached model files before and after
the run and checks source checkout identity. Required English/Japanese query
tokenizers and Japanese morphological lexical tokenization must be available.
The rebuilt index embeds each saved chunk's `embed_text` as stored; the runner
does not regenerate or repair that text.
Copy/parse, import, model/tokenizer initialization, and index rebuild are
separate setup measurements. Requests run once in fixed query/mode order as
`first_use`, followed by three ordered `warm` repetitions. Dense, lexical, and
baseline hybrid modes each use `k=5`, no cross encoder, and no feedback tuning.

One process shares one loaded model and index. The service's lexical cache
starts empty; neither the model nor OS/disk caches are reset between requests.
First-use may include lazy lexical preparation. Reported p50 and p95 use the
nearest-rank rule on successful samples: sort durations and select the
one-based `ceil(p*n)` item. Counts show attempted, successful, and failed or
degraded calls for every phase and mode. A stage exception or degraded fallback
is a failure even if it returns rows; such durations are excluded from the
successful percentile distribution.

Repeated snapshots compare ordered video/chunk IDs, start/end timestamps,
scores, and SHA-256 hashes of evidence text. Exact parity on these fixed probes
supports regression inspection only. Timing from a small local library is
descriptive; a speedup claim requires a controlled before/after comparison on
the same machine, workload, corpus, model, and cache policy. A relevance claim
requires separately adjudicated quality evidence.
