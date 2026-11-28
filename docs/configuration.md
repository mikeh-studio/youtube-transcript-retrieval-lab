# Configuration

`local_preview/local_api.py` loads `.env`, then `.env.local`; `.env.local` wins.
Both are gitignored, and `.env.example` is the checked-in template.

## Answer providers

Search, evaluation, and the offline benchmark need no provider key. Q&A, TLDR,
and Study Studio use the provider selected in the UI.

| Variable                          | Purpose                                                                                    |
| --------------------------------- | ------------------------------------------------------------------------------------------ |
| `OPENAI_API_KEY`                  | ChatGPT provider                                                                           |
| `OPENAI_MODEL`                    | Default ChatGPT model (`gpt-5.4-mini` if unset)                                            |
| `ANTHROPIC_API_KEY`               | Claude provider                                                                            |
| `SAKANA_API_KEY`                  | Sakana AI provider                                                                         |
| `SAKANA_MODEL`, `SAKANA_BASE_URL` | Sakana model (`fugu`) and endpoint (`https://api.sakana.ai/v1`)                            |
| `YOUTUBE_API_KEY`                 | Optional YouTube Data API metadata; without it, ingestion uses best-effort oEmbed metadata |

The UI lists selectable models per provider; Study Studio maps its Economy,
Balanced, and Quality profiles to models for each provider.

## Embeddings

| Variable                         | Purpose                                                                                                    |
| -------------------------------- | ---------------------------------------------------------------------------------------------------------- |
| `YT_RAG_EMBED_MODEL`             | Embedding model (default `intfloat/multilingual-e5-large`, about 2.2 GB)                                   |
| `YT_RAG_FORCE_HASH_EMBEDDINGS=1` | Skip the model and use local hashing embeddings                                                            |
| `YT_RAG_EMBED_LOCAL_PATH`        | Optional path to a previously cached model snapshot; keep `YT_RAG_EMBED_MODEL` set to its logical model ID |
| `YT_RAG_EMBED_REVISION`          | Optional model revision recorded in index metadata; a changed revision makes the saved index incompatible  |
| `HF_HUB_OFFLINE=1`               | Load model files locally without a Hugging Face download                                                   |

E5-family models get `query:` and `passage:` prefixes automatically; other
models, such as `BAAI/bge-m3`, are embedded without prefixes. Changing the model
rebuilds the transcript index once on the next start. OCR indexes are skipped
with a warning until each video is re-embedded with
`python pipelines/embed_ocr.py --video-id <id>`.

For a cached snapshot, point `YT_RAG_EMBED_LOCAL_PATH` to the model's local
directory and set `YT_RAG_EMBED_REVISION` to its revision. Use the matching
logical `YT_RAG_EMBED_MODEL` so E5 query/passage prefixes stay correct. Local
paths are not saved as model identities. Changing the revision rebuilds the
index from stored chunks; expect local embedding work on startup. OCR indexes
must also be rebuilt when their embedding space changes.

Adding a source embeds only that source's chunks and appends them to the
index. Replacing, rechunking or deleting a source still rebuilds the whole
index. To re-embed every chunk in one pass, for example after a batch ingest or
a change to passage formatting, stop the app and run:

```bash
python pipelines/reembed_library.py --dry-run
python pipelines/reembed_library.py
```

The command refuses to run on the hashing fallback unless you pass
`--allow-hash`, so a missing model never overwrites a model-built index.

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

| Variable                     | Purpose                                                                                    |
| ---------------------------- | ------------------------------------------------------------------------------------------ |
| `YT_RAG_HYBRID_PROFILE`      | `baseline_rrf` (default) or `optimized_v1`                                                 |
| `YT_RAG_RERANKER=1`          | Enable cross-encoder reranking for every request                                           |
| `YT_RAG_RERANKER_MODEL`      | Cross-encoder model override                                                               |
| `YT_RAG_AGENTIC_RETRIEVAL=1` | Enable agentic search for every request                                                    |
| `YT_RAG_FEEDBACK_TUNING=0`   | Disable feedback-based score adjustment by default (Search always disables it per request) |

Each switch can also be set per request; see the [retrieval guide](retrieval.md).

## Audio ingestion

Install the optional dependencies with `python -m pip install -r requirements-audio.txt`.
The default engine, `faster_whisper`, transcribes on this machine. Its first run
may download model weights. Budget several GB of free disk space for the model
cache, or set `YT_RAG_STT_MODEL` to a compatible model directory for offline use.
The `openai` engine requires `OPENAI_API_KEY` and sends 16 kHz mono WAV chunks of
up to ten minutes to OpenAI. Choose it only for audio you are permitted to send.

| Variable | Default | Purpose |
| --- | --- | --- |
| `YT_RAG_STT_ENGINE` | `faster_whisper` | Default engine. A request can select `faster_whisper` or `openai`. |
| `YT_RAG_STT_MODEL` | `large-v3-turbo` | Local model name or directory. |
| `YT_RAG_STT_COMPUTE_TYPE` | `int8` | faster-whisper compute type. |
| `YT_RAG_STT_DEVICE` | `auto` | faster-whisper device, such as `cpu` or `cuda`. |
| `YT_RAG_STT_CPU_THREADS` | `4` | Set a positive integer so faster-whisper uses an explicit CPU thread count despite the server's `OMP_NUM_THREADS=1` setting for retrieval. |
| `YT_RAG_STT_OPENAI_MODEL` | `whisper-1` | OpenAI model supporting verbose JSON and timestamp granularities. |
| `YT_RAG_STT_WORD_TIMESTAMPS` | `1` | Use `0` to request segment timestamps only. |
| `YT_RAG_AUDIO_MAX_MB` | `500` | Maximum input size in MiB for each enclosure download or local file. |
| `YT_RAG_KEEP_AUDIO` | `1` | Keep an exact playback copy under `data/media/`. Use `0` to discard the copy after indexing. |

Keep audio when citations must replay the indexed version of a podcast.
Dynamically inserted ads can move timestamps between downloads of the same
enclosure URL. With retention disabled, RSS citations use the enclosure URL
and local-file sources have no playback URL. The app leaves the original local
file alone. YouTube audio is never downloaded.

## Feed subscriptions

`YT_RAG_FEED_POLL_MINUTES` defaults to `0`. Set it to a positive number
to poll in the running server after each interval. Manual polls are available
at any setting.

| Route | Request and response |
| --- | --- |
| `GET /v1/subscriptions` | Returns `subscriptions`. |
| `POST /v1/subscriptions` | Creates a subscription from `url` and optional settings. Include `id` to update an existing subscription's settings. Returns `subscription`. |
| `DELETE /v1/subscriptions/{id}` | Returns `deleted.id`. Release history and indexed sources are retained. |
| `POST /v1/subscriptions/{id}/poll` | Accepts optional `dry_run` and returns `poll`. |
| `POST /v1/subscriptions/poll-all` | Accepts optional `dry_run` and returns `polls`, with individual feed failures reported under `error`. |
| `GET /v1/releases?since=...` | Reconciles podcast jobs and returns `releases`. An optional ISO 8601 timestamp with a timezone filters inclusively by release `updated_at`. |

Subscription settings include `kind` (`youtube` or `podcast`, inferred from
the feed URL by default), `language` (`en` by default, or `ja`),
`domain_tag` (optional display text, default `null`),
`auto_ingest` (default `false`), `title_include` and `title_exclude`
(optional regexes of up to 200 characters, without nested repetition or overlapping
repeated alternatives, matched against the first 300 title characters), and `min_duration_s` (default `600`,
`0` disables the duration filter). Changing the URL or kind requires a
new subscription. Enabling auto-ingest applies to newly discovered items
and resumes pending items when re-enabled.

Polls check ETag and Last-Modified and deduplicate by feed item ID. On the first
poll with auto-ingest enabled, older or undated releases are marked `discovered`
with reason `backlog`. Releases dated at or after subscription creation are eligible
for auto-ingest, as are newly seen releases on later polls. Each subscription
can attempt five auto-ingests per poll, including any podcast jobs still in flight.
The poll result reports `ingesting_count`. Pending items carry over
to later polls, including when the feed returns HTTP 304. A YouTube throttle
pauses automatic YouTube ingestion across subscriptions until the reported
`retry_at` time. Server polls queue podcast transcription in the background.
Standalone CLI polls wait for their audio jobs to finish.

Release rows contain `subscription_id`, `external_id`, `title`, `link`,
`published_at`, `source_id`, `duration_seconds`, `reason`, `discovered_at`,
`updated_at`, and `status` (`discovered`, `pending`, `ingesting`, `ingested`,
`skipped`, or `failed`). Podcast rows may have a `job_id`. A job missing after a
restart returns to `pending` with reason `job_lost` and a cleared `job_id`, so a
subsequent poll can retry it. The duration filter uses the last YouTube caption endpoint or
measured podcast audio duration. New short sources stay out of the library,
while existing sources are preserved. For eligible sources, the feed date is
stored as `source.published_at` when available. State lives in
`data/runtime/subscriptions.json` and `data/runtime/releases.jsonl`.

```bash
.venv/bin/python -m local_preview.subscriptions list
.venv/bin/python -m local_preview.subscriptions poll --sub ID --dry-run
.venv/bin/python -m local_preview.subscriptions poll
```

A dry run fetches and filters feed metadata without modifying local state or
starting ingestion. Use the API for a live poll while the server is running.

## Catalog

| Variable                      | Purpose                                                                                            |
| ----------------------------- | -------------------------------------------------------------------------------------------------- |
| `CONTENT_LIBRARY_CATALOG_URL` | Numeric loopback URL with port of the private catalog service, for example `http://127.0.0.1:8100` |

Without it, the Catalog page shows an unavailable state; the rest of the lab
works normally. See the [catalog browser](catalog-browser.md) for its limits.

## Evaluation dataset generator

| Variable             | Purpose              |
| -------------------- | -------------------- |
| `YT_RAG_CODEX_BIN`   | Codex CLI executable |
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

## Browser defaults

Settings stores provider/model, answer language and response length defaults in
this browser. Save and Cancel are explicit; no API key value is exposed or saved
in browser preferences. Credentials still come from local server configuration.
Defaults affect future answers only; saved report snapshots remain unchanged.

Search itself remains provider-free. The selected-passage answer flow displays a
token-budget estimate before generation. Pricing is not configured in this
initial workspace implementation: USD cost is shown as unavailable and requires
explicit acknowledgement, rather than inventing a price.
