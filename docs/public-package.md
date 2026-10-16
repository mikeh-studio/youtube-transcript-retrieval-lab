# Public package and integration boundary

The public repository owns the complete local workbench and reusable retrieval
helpers. A future private application can depend on a pinned release. The public
app never needs a private repository or catalog service to start. The optional
catalog remains a read-only external service.

This change prepares the public dependency. It does **not** integrate a private
application, migrate a corpus/database, change ranking defaults, or deploy a
hosted service. Private integration starts only after review of this handoff.

## Install and run

Python 3.11 or later:

```sh
python -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/retrieval-lab --data-dir /absolute/path/to/disposable-or-backed-up-data
```

The built HTML, JavaScript, CSS, icons, and generator schema are included. Node is
needed only to rebuild the UI from source. Optional local-video OCR dependencies
are available with `pip install '.[ocr]'`; ffmpeg/ffprobe are still separate system
dependencies. Existing `pip install -r requirements.txt` continues to install the
full historical dependency set including OCR.

The original `python local_preview/local_api.py` launcher and optional positional
port remain supported. Open the real app at **http://127.0.0.1:8000/index.html**.
The CLI also supports `--port`, `--workspace`, `--data-dir`, and `--env-dir`.
Keep the same hostname and port when returning to saved browser labels/history.

Packaging does not remove the default E5 model download. To deliberately test
offline plumbing, set `YT_RAG_FORCE_HASH_EMBEDDINGS=1`; this is not semantic-quality
validation. A hosted demo would address visitors' installation barrier separately.

## Storage and environment resolution

| Location | Source checkout default | Installed wheel default |
| --- | --- | --- |
| Workspace | Repository root | `$XDG_DATA_HOME/youtube-transcript-retrieval-lab`, or `~/.local/share/youtube-transcript-retrieval-lab` |
| Library/indexes/runtime/cache | `<workspace>/data` | `<workspace>/data` |
| `.env`, then `.env.local` | Workspace | Workspace |
| Static UI and schema | Public source resources | Installed package resources, read-only |

`YT_RAG_WORKSPACE`, `YT_RAG_DATA_DIR`, and `YT_RAG_ENV_DIR` override these paths.
Relative paths are resolved against the workspace. `.env` does not override
existing environment values; `.env.local` does, preserving historical behavior.
Explicit CLI path arguments take precedence over both. `YT_RAG_STATIC_DIR` is an
advanced static-resource override; it is never used for mutable data.

All application ingestion, indexes, OCR, feedback, ask history, summary/study
caches, and generator artifacts use the selected data directory. An isolated
data directory does not fall back to the original checkout's legacy history.
The default source checkout still reads its historical `local_preview/data`
fallback. Low-level APIs that already accept `data_dir` keep that contract; pass
it explicitly when composing an application. Export/review CLI commands retain
their documented explicit output and working-directory options.

A valid current Ask history takes precedence over legacy history. If the current
file cannot be read or parsed as an object, the legacy reader is attempted. Before
the next persistence, damaged current bytes are saved to a private-permission
`ask_history.corrupt-<id>.json` backup; a failed backup blocks that write.

Importing `local_preview.local_api` no longer initializes a model or library.
Construct `LocalRAGService(config=RuntimeConfig.resolve(...))` or use the launcher.
Do not rely on an initialized module-global `SERVICE` immediately after import.

## File ownership and compatibility

`retrieval_lab/{feedback_keys,grounded_answer,retrieval_tools,agentic_retrieval}.py`
is the single implementation of those public helpers. Old `local_preview` imports
alias the same module objects, preserving monkeypatches and private helper names.
The remaining `multilingual`, `pipelines`, `retrieval`, `evals`, and application
modules retain their layout. HTTP routes, browser security, UI pages, identifiers,
provider choices, feedback keys, and retrieval defaults are unchanged.

Private consumers should begin with the narrow helper namespace. Before importing
the full app, remove namespace ambiguity with old copied `local_preview` or
`multilingual` modules and assert the resolved module locations. Keep intentional
private source/citation/answer-policy behavior in the consumer. Do not merge an
entire private branch into public or introduce automatic route/plugin discovery.

`retrieval_lab.provenance.implementation_manifest()` returns a versioned identity
of the allowlisted public runtime code and resources without loading models or runtime data.
Consumers must separately record their policies, model revisions, datasets, and
private implementation. Preserve old fingerprints and results; a new identity
does not authorize relabeling results, resetting budgets, or replaying failed jobs.

## Build and verify a release

```sh
python -m pip install build
python -m build
python scripts/check_distribution.py dist/*
python -m venv /tmp/retrieval-install
/tmp/retrieval-install/bin/python -m pip install dist/*.whl
/tmp/retrieval-install/bin/python scripts/smoke_installed.py --output-dir /tmp/retrieval-smoke-new
```

Use a new output directory. The smoke check starts the real installed server in
an unrelated directory, seeds **synthetic** evidence, checks static resources and
authenticated search, imports every packaged Python module, reloads the store,
and rehearses snapshot restoration.
It does not establish live YouTube ingestion or real-model retrieval quality.

`retrieval_lab/public-files.txt` is the reviewed release allowlist. Wheel and source
archive checks reject unknown files, including accidentally bundled environment,
runtime, or private material. They also require the complete public file and
metadata sets, reject duplicate entries, and compare the embedded allowlist with
the reviewed checkout list. Provenance uses that same runtime classification.
Updating this list requires reviewing the new files;
do not automatically regenerate it during a release. This is a packaging boundary,
not a substitute for reviewing file contents or scanning for secrets.

Run `pytest tests/ multilingual/tests/ -q` and the frontend build/browser checks.
For live ingestion, use a disposable store with the real Python backend, submit a
real YouTube URL, and retain the POST response, completed job, and video/chunk count.
Network/model failures block that gate; fixtures and static hosting do not replace it.

## Rollback and next integration gate

Before an upgrade, stop writers and snapshot the selected data directory. Record
the current wheel SHA256, source revision, environment versions, origin/port, and
any uncommitted work separately. Never run parity tests against the only copy.
This release changes no data schemas; existing index metadata/fallback protections
remain. A model/configuration change can still rebuild derived indexes.

Rollback uses the previous pinned wheel **and a copied pre-upgrade snapshot** in
a disposable workspace first. Verify library counts, evidence IDs, saved feedback,
citations, and queries, then restore normal operation. Reverting code alone cannot
undo a mutated index. Keep the current release available until this is proven.

After human review, the next private integration milestone is one source search →
context → native citation workflow against the pinned public dependency. Then
migrate private providers, experiments, and human-review tools one group at a time.
Keep private routes/assets explicitly wired only in the private entrypoint. Delete
duplicated private code only after its own parity and rollback gates pass. Hosted
deployment and content-library migration remain separate work.
