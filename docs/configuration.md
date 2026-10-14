# Configuration

`local_preview/local_api.py` loads `.env`, then `.env.local`; `.env.local` wins.
Both are gitignored, and `.env.example` is the checked-in template.

## Answer providers

Search, evaluation, and the offline benchmark need no provider key. Q&A, TLDR,
and Study Studio use the provider selected in the UI.

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | ChatGPT provider |
| `OPENAI_MODEL` | Default ChatGPT model (`gpt-5.4-mini` if unset) |
| `ANTHROPIC_API_KEY` | Claude provider |
| `SAKANA_API_KEY` | Sakana AI provider |
| `SAKANA_MODEL`, `SAKANA_BASE_URL` | Sakana model (`fugu`) and endpoint (`https://api.sakana.ai/v1`) |
| `YOUTUBE_API_KEY` | Optional YouTube Data API metadata; without it, ingestion uses best-effort oEmbed metadata |

The UI lists selectable models per provider; Study Studio maps its Economy,
Balanced, and Quality profiles to models for each provider.

## Embeddings

| Variable | Purpose |
| --- | --- |
| `YT_RAG_EMBED_MODEL` | Embedding model (default `intfloat/multilingual-e5-large`, about 2.2 GB) |
| `YT_RAG_FORCE_HASH_EMBEDDINGS=1` | Skip the model and use local hashing embeddings |

E5-family models get `query:` and `passage:` prefixes automatically; other
models, such as `BAAI/bge-m3`, are embedded without prefixes. Changing the model
rebuilds the transcript index once on the next start. OCR indexes are skipped
with a warning until each video is re-embedded with
`python pipelines/embed_ocr.py --video-id <id>`.

### Offline mode

```bash
YT_RAG_FORCE_HASH_EMBEDDINGS=1 python local_preview/local_api.py
```

Hashing embeddings keep the app usable without the model download, but they are
not equivalent to E5 semantic retrieval. If the model is unavailable at startup,
a saved model-built index is preserved: dense search is disabled and lexical
search keeps working. New YouTube ingestion and provider-backed answers still
need network access.

## Retrieval switches

| Variable | Purpose |
| --- | --- |
| `YT_RAG_HYBRID_PROFILE` | `baseline_rrf` (default) or `optimized_v1` |
| `YT_RAG_RERANKER=1` | Enable cross-encoder reranking for every request |
| `YT_RAG_RERANKER_MODEL` | Cross-encoder model override |
| `YT_RAG_AGENTIC_RETRIEVAL=1` | Enable agentic search for every request |
| `YT_RAG_FEEDBACK_TUNING=0` | Disable feedback-based score adjustment |

Each switch can also be set per request; see the [retrieval guide](retrieval.md).

## Catalog

| Variable | Purpose |
| --- | --- |
| `CONTENT_LIBRARY_CATALOG_URL` | Numeric loopback URL with port of the private catalog service, for example `http://127.0.0.1:8100` |

Without it, the Catalog page shows an unavailable state; the rest of the lab
works normally. See the [catalog browser](catalog-browser.md) for its limits.

## Evaluation dataset generator

| Variable | Purpose |
| --- | --- |
| `YT_RAG_CODEX_BIN` | Codex CLI executable |
| `YT_RAG_CODEX_MODEL` | Codex model override |

Codex authentication uses `codex login`; provider keys are not forwarded.

## Port and local-video OCR

The server listens on `127.0.0.1:8000`. Pass another port as the first argument:

```bash
python local_preview/local_api.py 8011
```

Local-video OCR also needs `ffmpeg` and `ffprobe` on `PATH`.

## Development and tests

```bash
python -m pip install pytest
python -m pytest tests/ multilingual/tests/ -q
HF_HUB_OFFLINE=1 python -m pytest multilingual/tests/test_chunking_strategies.py -q
```

The React shell needs Node.js 20.19+ or 22.12+ (required by Vite 7). The build
writes the bundle that the Python API serves to `local_preview/web/`, and CI
checks that the committed bundle matches the source.

```bash
npm ci
npm run build:web
npx playwright install chromium
npx playwright test e2e/qa-answer-mode.spec.ts
```

Unit tests and browser fixtures do not establish live ingestion or answer
quality. For real-backend checks, see
[ingestion verification](../local_preview/README.md#ingestion-verification).
