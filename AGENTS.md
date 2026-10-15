# Repository Guidelines

Public guidance for contributors and AI coding assistants working in this repo.

## Project Structure & Module Organization
- `local_preview/`: the local app. `local_api.py` (HTTP API + static files), `catalog_client.py` (read-only catalog proxy), retrieval tools, agentic search, grounded answers, and the evaluation dataset generator. UI pages and bundle in `local_preview/web/`; React source in `local_preview/react_shell/`.
- `local_preview/react_shell/src/`: `library.jsx` (Library), `workbench.jsx` (Search A/B workbench), `lab.jsx` (shared helpers), and `App.jsx` (routing, Answer flow, Study, Local video). Rebuild the bundle with `npm run build:web` after changes.
- `local_preview/web/lab-nav.js`: the single navigation definition (Catalog, Library, Search, Evaluate, Tools). Change nav items here, not in individual pages.
- `browser_boundary.py`: same-origin request boundary (see `BROWSER_SECURITY.md`).
- `multilingual/`: core engine (`video_library.py`, `text_processing.py`, `rag_engine.py`, `reranker.py`, `video_routing.py`) and language-specific tests in `multilingual/tests/`.
- `evals/`: offline retrieval benchmark and `evals/video_routing/` evaluator.
- `pipelines/`: evidence curation, local-video OCR, and library export scripts.
- `retrieval/`: OCR and multimodal search helpers.
- `tests/`: top-level regression tests; `e2e/`: Playwright browser checks.
- `data/`: local indexed artifacts, runtime data, and cache files (do not commit generated files).
- `docs/`: architecture, guides (`retrieval.md`, `evaluation.md`, `configuration.md`, `catalog-browser.md`), data contracts in `docs/contracts/`, design mocks in `docs/design/`, and screenshots in `docs/media/`.

## Build, Test, and Development Commands
- `pip install -r requirements.txt`: install Python dependencies from the repository root.
- `cp .env.example .env.local`: create a local environment file before adding provider keys.
- `python local_preview/local_api.py`: run local UI + API at `http://127.0.0.1:8000/`.
- `pytest tests/`: run repository-level tests.
- `pytest multilingual/tests/`: run multilingual module tests.
- `pytest tests/ multilingual/tests/ -q`: quick full test pass before opening a PR.

## Coding Style & Naming Conventions
- Use Python with 4-space indentation and PEP 8-style naming.
- Modules/files: `snake_case.py`; classes: `PascalCase`; functions/variables: `snake_case`; constants: `UPPER_SNAKE_CASE`.
- Keep functions focused; prefer explicit return shapes (`dict` keys stable across code paths).
- Add short docstrings for non-trivial public functions and API handlers.

## Testing Guidelines
- Framework: `pytest` with test files named `test_*.py`.
- Co-locate specialized tests (`multilingual/tests/`) and keep cross-module behavior in top-level `tests/`.
- Mock external APIs (`openai`, `anthropic`, network fetches) for deterministic runs.
- No strict coverage gate is configured; add tests for all bug fixes and behavior changes.

## Critical Validation Rules (Local Preview)
- Ingestion is a core feature. For any UI/API ingestion verification, run the real backend: `python local_preview/local_api.py`.
- Do not treat static hosting (`python -m http.server ...`) or mock `/v1/*` responses as a valid ingest verification path.
- When asked to confirm ingest works, validate end-to-end with a real YouTube URL and report:
  - `POST /v1/ingest/videos` response
  - job status from `GET /v1/ingest/jobs` (must reach `completed`)
  - video presence/chunk count in `GET /v1/videos`
- If network/model download blocks startup (e.g., Hugging Face model fetch), explicitly report that as a blocker instead of claiming ingestion works.
- When sharing a manual test URL for core features, always provide the real app URL: `http://127.0.0.1:8000/index.html`.

## Commit & Pull Request Guidelines
- Follow existing commit style: conventional prefixes like `feat:` and `docs:` with concise summaries.
- Keep commits scoped (one logical change per commit) and include tests/docs with code changes.
- PRs should include: purpose, key changes, test commands run, and screenshots for UI updates (`local_preview/web/*`).
- For docs-only or README-only work, state that explicitly in the PR summary.
- Keep the README a short landing page; put operator detail in `docs/` or `local_preview/README.md`; the history lives in pull requests. Avoid repeating the same feature details across files.
- Link related issues/tasks and list any required environment variables (for example `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`).

## Security & Configuration Tips
- Never commit secrets or local runtime artifacts.
- Use environment variables for provider keys and keep `.gitignore` entries for generated data/logs up to date.
- Keep OCR workflows limited to local video files that you own or have permission to process; do not add public YouTube video downloading, scraping, or blocking-bypass logic.
