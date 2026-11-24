# Local App Guide

Run the Retrieval Lab UI and API: catalog browsing, transcript indexing, retrieval testing, and evaluation.
For configuration and design background, see [`docs/`](../docs/architecture.md).

## Start

From the repository root:

```bash
python local_preview/local_api.py
```

Open:

- `http://127.0.0.1:8000/catalog.html` - Catalog (needs `CONTENT_LIBRARY_CATALOG_URL`)
- `http://127.0.0.1:8000/index.html` - Library, Search (`#/qa`), What’s new (`#/whats-new`), and Settings (`#/settings`)
- `http://127.0.0.1:8000/evaluation.html` - Evaluation
- `http://127.0.0.1:8000/chunking.html` - Settings → Chunking
- `http://127.0.0.1:8000/evidence.html` - Settings → Data quality
- `http://127.0.0.1:8000/reviews.html` - Settings → Search feedback

Use this command for fully offline startup without loading the embedding model:

```bash
YT_RAG_FORCE_HASH_EMBEDDINGS=1 python local_preview/local_api.py
```

## What This Includes

- One local process for the HTTP API and static UI.
- Catalog: a read-only view of discovered videos from the optional private catalog service.
- Library: YouTube video and playlist transcript ingestion, and a table of indexed videos with chunk counts, chunking status, and catalog review status.
- Search: Find evidence groups timestamped passages by video, expands canonical transcript context, and generates optional citation-backed answers from a saved selection. Compare retrieval runs two setups on the same query with relevance labels, per-query metrics, optional reranking, and retrieval traces. Compare supports `hybrid`, `dense`, `lexical`, and deterministic agentic retrieval.
- [What’s new](../docs/digest.md): follow feeds, check for releases, and build a cited digest from indexed sources.
- Evaluation: review explicitly imported saved CLI/API reports, ranking and answer metrics, citations, token usage, and supplied costs. Query sets, labels, retrieval runs, and Codex-drafted datasets remain in the linked Query sets workbench.
- Settings: browser defaults, provider configuration status (no keys), and links to Catalog, Chunking, Data quality (evidence curation reports), Search feedback (`relevant` / `not_relevant` labels from Search), and Local video OCR for files you own or have permission to process.
- Study (secondary): flashcards, topic maps, Explain Topic notes, summaries, run history, and quality checks.
- UI language switch for English and Japanese.

## Runtime Data

Local preview writes generated state under `data/`:

- `data/library/` - library manifest and per-video records.
- `data/index/` - transcript FAISS indexes.
- `data/index/ocr/` - OCR FAISS indexes.
- `data/runtime/` - feedback, ask history, ingest logs, review batches, and curation artifacts.
- `data/cache/summaries/` - TLDR cache files.

Imported evaluation reports are immutable local files under `data/runtime/evaluation_reports/`. Query sets, labels, and retrieval run snapshots in the Query sets workbench are stored in browser `localStorage`.
Study Studio run history is stored in browser `sessionStorage` for the current tab session.

## Ingestion Verification

Use the real backend for ingestion checks:

```bash
python local_preview/local_api.py
```

Then test through the Library at `http://127.0.0.1:8000/index.html` or the JSON API.

For an end-to-end ingestion check, capture:

- `POST /v1/ingest/videos` response
- `GET /v1/ingest/jobs` status, which should reach `completed`
- `GET /v1/videos` result with the ingested video and chunk count

Static hosting or mocked `/v1/*` responses are not valid ingestion verification.

## Podcast and local audio ingestion

Install the optional audio dependencies and start the local app. Audio
ingestion is available under **Library → Add audio** or through the JSON API.
Preview an RSS feed and choose an episode, or enter a file path on the server.
See the engine setup and retention settings in
[Audio ingestion](../docs/configuration.md#audio-ingestion).

- `GET /v1/stt/capabilities` returns the configured engine, models, device,
  compute type and word-timestamp setting, plus package and key availability.
- `POST /v1/feeds/preview` accepts `{"url":"https://radio.example/feed.xml"}`.
  It returns the feed title, link, language and items without subscribing. An
  item includes `external_id`, title, publication time, link, enclosure URL,
  MIME type, byte length (`media_length_bytes`), and duration when available.
- `POST /v1/ingest/audio` queues one of the requests below and returns HTTP 202
  with a `job`. The language is `en` or `ja`. Optional `engine` selects the
  adapter for this job. Use `"force":true` to transcribe an existing source again.

```json
{"kind":"rss_episode","feed_url":"https://radio.example/feed.xml","guid":"episode-1","language":"en"}
```

```json
{"kind":"local_file","path":"recordings/episode.wav","language":"en"}
```

Use the preview item's `external_id` as the RSS request's `guid`. Local files may
use a repository-relative or absolute path. Supported audio extensions are
`.mp3`, `.m4a`, `.aac`, `.wav`, `.flac`, `.ogg`, and `.opus`. The local video
formats listed below can also be transcribed when they contain an audio stream.
Send JSON POSTs with the same-origin session cookie and the `X-CSRF-Token`
from `GET /browser-session`.

Poll `GET /v1/ingest/jobs/{job_id}` for `status`, `step`, `progress` (0 to 1),
and the source ID in `video_id`. Jobs run one at a time in memory. Jobs that
transcribed audio also report `transcription_seconds`, `audio_duration_seconds`,
`stt_engine`, and `stt_model` on completion. Completed sources appear in
`GET /v1/videos` alongside other indexed transcripts.
`GET /v1/sources/{id}/media` serves a retained copy and supports a single
byte range for seeking. When word timestamps are enabled, the library stores
them in `data/library/videos/{id}/stt_words.json` with second offsets and word
text grouped by segment. YouTube feed entries can be previewed as metadata.
YouTube audio is never downloaded.

## Video-first Ask routing

`POST /v1/ask` can shortlist relevant videos before searching transcript chunks:

```json
{
  "question": "How does fermentation timing affect the starter?",
  "video_routing": "multi_vector",
  "video_top_k": 3,
  "agentic": true
}
```

Direct API clients can request this routing with the fields above. The response
includes selected video IDs, fallbacks, and latency under `retrieval_details.video_routing`.
In the browser, Find runs `/v1/search` and answers from selected passages
through `/v1/evidence/answer`.

## Agentic Search

`POST /v1/search` and `POST /v1/ask` accept `"agentic": true`. The deterministic
policy selects Japanese BM25 or E5 + FAISS, can try the other search tool, and
then reads nearby evidence from canonical `full_transcript.segments`. The
existing plain search behavior remains the default. Tool decisions are returned
under `retrieval_details.agentic_retrieval`.

`POST /v1/search` also accepts `video_ids` (a list of indexed video IDs) and
`feedback_tuning: false`. `GET /v1/feedback/search-review` accepts `query` to
list the labels for one query. See [Search request options](../docs/retrieval.md#search-request-options).

Each YouTube record also exposes `source.platform`, canonical video URL, and
channel ID/name/URL. Set optional `YOUTUBE_API_KEY` to use the YouTube Data API;
without it, ingestion uses best-effort oEmbed metadata and keeps working when
metadata is unavailable.

## Local Video OCR

Local Video OCR is intentionally separate from the public YouTube transcript flow.

- It accepts local `.mp4`, `.m4v`, `.mov`, `.mkv`, and `.webm` files.
- It creates timestamped frame, OCR, and OCR-index artifacts under `data/`.
- Local video can search transcript evidence, OCR evidence, or both.
- It requires `ffmpeg` and `ffprobe` on `PATH`.

The main endpoints are:

- `POST /v1/local-video-ocr/jobs`
- `GET /v1/local-video-ocr/jobs`
- `GET /v1/local-video-ocr/jobs/{job_id}`
- `GET /v1/local-video-ocr/videos/{video_id}`
- `POST /v1/search-multimodal`
- `POST /v1/ask-multimodal`

## Assisted Labeling Workflow

Use `local_preview/review_agent_workflow.py` to batch live search results, render reviewer prompts, build adjudication inputs, and apply approved labels back through `/v1/feedback/search-review`.

Build a batch:

```bash
python local_preview/review_agent_workflow.py build-batch \
  --query-file local_preview/examples/review_queries.example.json \
  --include-existing-feedback
```

Render a reviewer prompt:

```bash
python local_preview/review_agent_workflow.py render-prompt \
  --kind reviewer \
  --input data/runtime/review_batches/<batch>.json \
  --shard-id shard-001
```

Build adjudication input:

```bash
python local_preview/review_agent_workflow.py build-adjudication \
  --input data/runtime/review_recommendations/<recommendations>.json
```

Apply approved recommendations:

```bash
python local_preview/review_agent_workflow.py apply \
  --input data/runtime/review_recommendations/<approved>.json
```

The checked-in example query file uses Japanese prompts aligned to the current local sample library. For other libraries, copy it and replace the queries with prompts that match your transcripts.

## Notes

- Ingestion runs synchronously in local mode.
- If embedding model download is unavailable, local preview can use hashing embeddings.
- Chunking reads stored `full_transcript` data and does not mutate the saved library or index during preview/search comparison.
