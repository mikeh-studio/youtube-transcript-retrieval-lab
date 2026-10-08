# YouTube Transcript Retrieval Lab

[![CI](https://github.com/mikeh-studio/youtube-transcript-retrieval-lab/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/mikeh-studio/youtube-transcript-retrieval-lab/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Local-first transcript retrieval workbench that compares semantic, lexical,
hybrid, and deterministic agentic search before the answer layer. It preserves
the raw timestamped transcript as evidence, derives E5 + FAISS and Japanese
BM25 search views, and uses retrieved evidence for citation-backed Q&A.

Compare retrieval runs with Precision@K, Recall@K, MRR, and nDCG@K, then inspect
the timestamped evidence behind each result. The included eight-query benchmark
is a deterministic regression fixture with hand-authored dense scores; it does
not establish real-world retrieval quality. Baseline RRF remains the default
hybrid profile. See the [benchmark details](evals/README.md#fixture-result).

![Evaluation Metrics](docs/media/02-evaluation-metrics.png)

## Quick Start

Python 3.11 is used in CI. From the repository root, create an isolated environment
(macOS/Linux commands):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
# Preserve any existing machine-specific configuration.
[ -f .env.local ] || cp .env.example .env.local
```

Transcript search and the offline benchmark do not require an answer-provider
key. For citation-backed Q&A, add the key for your selected provider to
`.env.local` before starting the server:

```env
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
SAKANA_API_KEY=your_sakana_api_key_here
```

Optional Sakana overrides:

```env
SAKANA_MODEL=fugu
SAKANA_BASE_URL=https://api.sakana.ai/v1
```

If `OPENAI_MODEL` is not set, ChatGPT calls default to `gpt-5.4-mini`.

Start the API and UI together:

```bash
python local_preview/local_api.py
```

Open the [main console](http://127.0.0.1:8000/index.html), ingest a video with an
available transcript, then search its evidence. Use the real API server for
these workflows; a static file server cannot ingest or search videos.

| Workspace | Use it to |
| --- | --- |
| [Main console](http://127.0.0.1:8000/index.html) | Ingest, search, ask questions, and use Study Studio |
| [Reviewed chunks](http://127.0.0.1:8000/reviews.html) | Inspect search-result feedback |
| [Evidence curation](http://127.0.0.1:8000/evidence.html) | Inspect generated curation artifacts |
| [Evaluation](http://127.0.0.1:8000/evaluation.html) | Label results, compare runs, and draft evaluation datasets |
| [Chunking Lab](http://127.0.0.1:8000/chunking.html) | Compare chunking strategies on stored transcripts |

Notes:

- `.env` and `.env.local` are gitignored. `.env.example` is the checked-in template.
- `local_preview/local_api.py` loads `.env`, then `.env.local`; `.env.local` wins for machine-specific overrides.
- The default embedding model is `intfloat/multilingual-e5-large` (~2.2 GB download
  on first start). Override it with `YT_RAG_EMBED_MODEL` (E5-family models get the
  `query:`/`passage:` prefixes automatically; other models such as `BAAI/bge-m3`
  are embedded without prefixes). Changing the model triggers a one-time automatic
  rebuild of the transcript index on the next start; OCR indexes are skipped with
  a warning until re-embedded per video with `python pipelines/embed_ocr.py --video-id <id>`.
- If model download is unavailable, local preview can fall back to local hashing
  embeddings. A saved model-built index is preserved on disk (dense search is
  disabled, lexical search keeps working) until the model is available again.
- To skip the embedding-model download, run
  `YT_RAG_FORCE_HASH_EMBEDDINGS=1 python local_preview/local_api.py`. Hashing is a
  fallback for local operation, not equivalent to E5 semantic retrieval. New
  YouTube ingestion and provider-backed answers still require network access.
- Japanese lexical (BM25) search tokenizes with fugashi morphological analysis,
  falling back to character bigrams if fugashi is unavailable.
- Optional `YOUTUBE_API_KEY` enables YouTube Data API metadata lookup; without
  it, ingestion uses best-effort oEmbed metadata. It is not an answer-provider key.
- Local-video OCR additionally requires `ffmpeg` and `ffprobe` on `PATH`.
- The built frontend is checked in; running it through the Python API does not
  require Node.js. See [Development and Tests](#development-and-tests) for frontend
  builds and browser checks.

### Seed an Evaluation Dataset with Codex

The Evaluation workspace can turn one to three already-ingested transcripts into
a six-case retrieval draft. Install the Codex CLI, run `codex login`, then open
`http://127.0.0.1:8000/evaluation.html`. Every proposal remains a draft until you
approve, edit, or reject each case. Finalization creates a browser query set and
a downloadable JSONL dataset under the gitignored `data/runtime/eval_generator/`
directory.

The local API invokes one ephemeral Codex pass in a read-only sandbox, validates
all cited chunk references against the selected videos, and does not forward
provider API-key environment variables. `YT_RAG_CODEX_BIN` can select a specific
CLI executable, and `YT_RAG_CODEX_MODEL` can optionally override its model.

## What It Solves

Most RAG demos stop after retrieving chunks. This project focuses on whether retrieval is actually good.

The local workflow lets you:

- ingest transcript evidence
- compare semantic, lexical, hybrid, and agentic retrieval
- label retrieved results
- compute ranking metrics
- compare runs before and after changes
- use retrieved evidence in citation-backed answers
- add OCR evidence from local files you own or have permission to process

## Core Workflow

1. Ingest a YouTube transcript.
2. Normalize and chunk the transcript with timestamp metadata.
3. Build a local FAISS index.
4. Retrieve chunks directly, or let the deterministic agent choose semantic,
   keyword, and nearby-context tools.
5. Label results in the Evaluation workspace.
6. Compare retrieval runs with ranking metrics.
7. Ask questions backed by retrieved evidence.
8. Optionally merge transcript evidence with OCR evidence from local video frames.

## Current Capabilities

- English and Japanese transcript workflows.
- YouTube video and playlist transcript ingestion.
- Timestamped chunking with strategy comparison in Chunking Lab.
- Local FAISS indexes for transcript and OCR evidence.
- Retrieval modes: `dense`, `lexical`, and `hybrid`.
- Opt-in cross-encoder reranking stage for retrieval candidates.
- Opt-in deterministic agentic search across semantic, Japanese keyword, and
  raw timestamp-context tools, with an auditable decision trace.
- Citation-backed Q&A with fallback states and selectable OpenAI, Claude, or Sakana AI providers.
- Video-first Ask routing that shortlists relevant videos before retrieving
  transcript chunks when Q&A Studio searches all videos.
- Study Studio for transcript-grounded flashcards, topic maps, per-topic explanations, run history, and study-quality checks.
- Search-result review and assisted labeling helpers.
- Codex CLI-assisted retrieval dataset drafts with required human review and JSONL export.
- Browser-local evaluation query sets, labels, run snapshots, and metrics.
- Read-only evidence curation reports from local pipeline artifacts.
- Local-video OCR for `.mp4`, `.m4v`, `.mov`, `.mkv`, and `.webm` files.

Historical release notes live in [`CHANGELOG.md`](CHANGELOG.md).

## Architecture

```text
Browser UI
  index.html / reviews.html / evidence.html / evaluation.html / chunking.html
      |
      v
local_preview/local_api.py
  local HTTP API + static files
      |
      v
multilingual/
  transcript processing, chunking, embeddings, retrieval
      |
      v
local files
  data/library/          library manifest and per-video records
  data/index/            transcript FAISS indexes
  data/index/ocr/        OCR FAISS indexes
  data/frames/           extracted local-video frames
  data/processed/        frame and OCR metadata
  data/runtime/          feedback, ask history, ingest logs, curation artifacts
  data/cache/summaries/  per-video TLDR cache files
  browser localStorage   evaluation query sets, runs, and labels
  browser sessionStorage Study Studio run history
```

## Project Structure

- `local_preview/` - local web UI, API, and review workflow helpers.
- `multilingual/` - transcript processing, chunking, embeddings, and retrieval.
- `evals/` - offline retrieval benchmark datasets, configs, scoring, and reports.
- `pipelines/` - local OCR, embedding, and evidence curation scripts.
- `retrieval/` - multimodal search helpers.
- `production_cloudflare/` - separate Cloudflare deployment stack.
- `tests/` - repository-level regression tests.
- `multilingual/tests/` - multilingual module tests.

## Local Video OCR Boundary

The YouTube flow retrieves transcripts and metadata and links evidence to video
timestamps. Playlist ingestion currently fetches the YouTube playlist page and
parses video IDs from its HTML; that step can fail if the page structure or
access behavior changes.

Frame extraction and OCR accept local video files that you own or have permission
to process. This workflow does not download public YouTube video files or bypass
access restrictions.

For implementation details, see [`docs/multimodal_ocr_design.md`](docs/multimodal_ocr_design.md).

## Evidence Curation

`pipelines/curate_evidence.py` turns already-ingested transcript chunks into local evidence artifacts under `data/runtime/`. It adds heuristic quality signals, topic tags, retrieval eligibility, run metadata, and a quality report without calling external services.

Example:

```bash
python pipelines/curate_evidence.py \
  --dataset-id demo_transcript_evidence \
  --dataset-version v1 \
  --language ja \
  --limit 200
```

Open `http://127.0.0.1:8000/evidence.html` to inspect the generated artifacts.

## Assisted Labeling

`local_preview/review_agent_workflow.py` batches live `/v1/search` results, renders reviewer prompts, builds adjudication inputs, and applies approved labels through `/v1/feedback/search-review`.

See [`local_preview/README.md`](local_preview/README.md) for the full local operator flow.

## Retrieval Add-ons

Both add-ons are opt-in; the default retrieval pipeline is unchanged.

Cross-encoder reranking rescores fused candidates with a multilingual
cross-encoder (default `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`) before
feedback tuning and diversity selection. Enable it per request with
`"reranker": "cross_encoder"` on `/v1/search` or `/v1/ask`, or globally with
`YT_RAG_RERANKER=1`. Override the model with `YT_RAG_RERANKER_MODEL`. If the
model cannot be downloaded, reranking is skipped and the load error is
reported in `retrieval_details.reranker`.

Agentic search is available on `/v1/search` and `/v1/ask`. Its deterministic,
provider-free policy chooses Japanese BM25 or E5 + FAISS, can try the other
search tool when evidence is weak, and calls `read_context` around strong
timestamp anchors in the canonical raw transcript. Enable it per request with
`"agentic": true` or globally with `YT_RAG_AGENTIC_RETRIEVAL=1`.
The auditable tool trace is returned in `retrieval_details.agentic_retrieval`.
If no path reaches sufficient evidence, the strongest attempted result set is
returned and the normal insufficient-evidence answer behavior still applies.

For Q&A across a library, video-first routing and agentic chunk retrieval can
be combined. See the [video-first Ask request example](local_preview/README.md#video-first-ask-routing)
for `video_routing`, `video_top_k`, and the returned routing details.

## Offline Retrieval Benchmark

Run the checked-in benchmark without network access or provider keys.

```bash
python -m evals.runner \
  --dataset evals/datasets/jp_core_v1.example.jsonl \
  --config evals/configs/baseline.yaml \
  --out evals/reports/latest
```

The runner compares dense, lexical, baseline hybrid, optimized hybrid, and
agentic retrieval, then writes a compact leaderboard plus machine-readable
metrics. The documented fixture scores are nDCG@10 **0.8452** for baseline RRF
and **1.0000** for optimized hybrid across eight queries. These test-harness
results use hand-authored dense scores, not a live embedding-index evaluation.

The local app keeps baseline RRF as the default hybrid profile unless
`retrieval_profile` or `YT_RAG_HYBRID_PROFILE` explicitly opts into another
profile.
See [`evals/README.md`](evals/README.md) for metrics, latest fixture results,
and sample-set limitations.

## Known Limitations

- Evaluation is single-reviewer.
- Evaluation workspace query sets, labels, and run snapshots are browser-local;
  clearing browser storage removes them. Search-review feedback is stored
  separately under `data/runtime/`.
- Inter-rater agreement and adjudication UI are not implemented.
- Retrieval quality depends on transcript availability from YouTube.
- Chunking Lab requires videos with stored `full_transcript`; re-ingest older videos if preview/search comparison reports missing transcript data.
- The checked-in retrieval benchmark is a small deterministic fixture, not a statistically stable corpus.

## Development and Tests

Install the test runner in the active Python environment and run both suites:

```bash
python -m pip install pytest
python -m pytest tests/ multilingual/tests/ -q
```

For chunking-specific checks:

```bash
HF_HUB_OFFLINE=1 python -m pytest multilingual/tests/test_chunking_strategies.py -q
python -m pytest tests/test_chunking_api.py -q
```

To rebuild the React shell, use Node.js 20 (the version used in CI):

```bash
npm ci
npm run build:web
```

The build writes the bundle served by the Python API to `local_preview/web/`.
CI checks that the committed bundle matches the source and runs the answer-mode
browser regression. Run that same browser check locally with:

```bash
npx playwright install chromium
npx playwright test e2e/qa-answer-mode.spec.ts
```

Unit tests and browser fixtures do not establish live YouTube ingestion or
answer quality. See [Ingestion Verification](local_preview/README.md#ingestion-verification)
for the real-backend checks.
