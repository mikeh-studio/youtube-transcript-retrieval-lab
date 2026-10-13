# Stored library export/import contract v1

Status: implemented transport, strict validation, dry-run report, and ingestion-core
compatibility tests. The content service now also provides offline retrieval-view
reconciliation and migration 0003; see `retrieval-view-reconciliation.md` in that
repository for the implementation boundary. No production import command, applied
production migration, catalog API, app-backend change, or ontology is included.

## Ownership

- YouTube app: export its existing split v2 JSON layout, preserving exact bytes,
  list order, stored transcript segments, chunks, embedding inputs, and legacy IDs.
- Content library: validate and parse the interchange contract; own future durable
  canonical identities and persistence. Its existing ingestion service is exercised
  by contract tests, not wired into the dry-run command.
- The repositories share versioned schemas and a synthetic golden fixture, not
  Python imports, database credentials, runtime records, or direct table access.
- The service's Pydantic contract is the schema authority. Changes require the
  mirrored JSON schemas and golden-fixture tests to pass in both repositories.

## Wire envelope

`schema_version` is exactly `youtube-library-export/v1`. The envelope contains
`snapshot_id`, `manifest_json`, `manifest_sha256`, and ordered `videos` entries.
Each entry has `video_id`, `record_json`, and `record_sha256`.

The manifest and record fields contain the original UTF-8 text, including whitespace.
Their SHA-256 digests are calculated over their exact UTF-8 bytes. They are not
re-serialized before hashing. Unknown fields, invalid IDs, duplicate JSON keys,
non-finite timestamps, malformed bounds, duplicate records, incomplete transcripts,
and mismatched record URLs are rejected by the receiving contract.

The snapshot ID is SHA-256 of compact UTF-8 JSON (sorted keys, no extra whitespace,
non-ASCII characters unescaped) of:

```json
{"schema_version":"youtube-library-export/v1","manifest_sha256":"<hash>","videos":[{"video_id":"<id>","record_sha256":"<hash>"}]}
```

Video order must exactly match the manifest. Hashes detect corruption; they do not
prove source permission or authenticity. No clock field enters snapshot identity.
The v1 reader limits a bundle to 64 MiB. Schema files describe the envelope and
inner record separately; runtime validators additionally enforce hashes, manifest
membership, source identity, transcript counts, and exact evidence mappings.

## Evidence and identity

- Source identity: `(youtube_transcript, YouTube video ID)`.
- Evidence origin: `legacy_processed_transcript`. Do not relabel these processed
  records as original caption downloads.
- Original acquisition time and permission basis remain unknown. A metadata-fetch
  timestamp, if present in the preserved record, is not a transcript acquisition date.
- Segment order and text are preserved without normalization. Full text must equal
  the stored segments joined with `\n`; counts must agree.
- Timestamp seconds convert once to integer milliseconds using decimal half-up
  rounding (maximum conversion error 0.5 ms); original seconds remain in the artifact.
- Character offsets use Unicode code points, half-open, against the exact stored
  full transcript. These are not UTF-8 byte offsets or JavaScript UTF-16 offsets.
- Every chunk must have one unique contiguous segment range whose joined text and
  first/last timestamp match. Endpoint tolerance is 1 microsecond for floating-point
  arithmetic. Overlapping timestamps alone are not sufficient. Ambiguity fails the
  whole validation; no fuzzy repair or silent skipping.
- Preserve `video_id:chunk_index` mappings and chunk order. UUIDs are assigned by
  canonical persistence later; the dry-run does not invent or allocate them.
- The raw record artifact retains `raw_text`, `embed_text`, chunking settings and
  optional source metadata exactly. It is private runtime data.

## Canonical versions versus derived views

The report separates `canonical_revision_hash` (using the existing canonical
parser/hash contract) from `retrieval_view_sha256` (chunking and raw/embed inputs).
Snapshot ID and export/embedding metadata do not enter canonical transcript identity.
A changed transcript produces a new revision; a changed embedding input does not.

The current generic ingestion service returns an existing version immediately for
an identical canonical revision. It DOES NOT reconcile changed derived views or
additional source artifacts on that path. Therefore do not use it as a production
bundle importer without a derived-view reconciliation design and validation gate.
The CLI still exposes only validation/preview. The separate
`LibraryExportReconciler` handles changed views, artifact attachments, and snapshot
observations with explicit outcomes. Its import path never activates search. Do not
use the generic ingestion service as a replacement for that reconciliation path.

## Commands

In the YouTube repository (pause library writes during capture):

```bash
python pipelines/export_library.py --data-dir data --output /private/tmp/library-export.json
```

The destination parent must exist. Output is created with mode 0600 and refuses
replacement, including symlinks. Files are checked again to detect concurrent
changes; this is not a transaction across a concurrently written filesystem.

In the content-library repository:

```bash
.venv/bin/python -m multisource_content_library.cli.validate_library_export \
  --bundle /private/tmp/library-export.json > /private/tmp/library-import-preview.json
```

A successful report says `validated_not_imported`, with video/segment/chunk totals,
languages, hashes, and per-video legacy mappings. It does not contact a database,
fetch sources, call models, or store content. There is intentionally no `--apply`.
Keep bundles/reports outside Git; the committed fixture contains synthetic text only.

## Validation gates

1. Byte-preserving deterministic round trip against the shared bilingual fixture.
2. Reject corruption, unknown schema/fields, mismatched identities, incomplete
   evidence, invalid bounds, duplicate identities, and ambiguous segment mappings.
3. In-memory ingestion compatibility: identical input reuses all canonical IDs;
   transcript changes preserve old evidence; parser/artifact/transaction failure
   cannot publish partial canonical state. This is not durable PostgreSQL proof.
4. Dedicated PostgreSQL test verifies persistence across connection disposal and
   repeat import. It is gated by `CONTENT_LIBRARY_TEST_DATABASE_URL` and explicitly
   skips when unavailable. Never point it at an existing content database: it truncates
   test data. A skip does not pass the durable persistence gate.
5. Real stored pilot passes dry-run fidelity with every record and mapping accounted
   for. No claim about live YouTube acquisition follows from this check.

Production import remains gated on review of reconciliation, migration, and real
PostgreSQL validation results. Existing notes, reviews, browser-local evaluations, and application
history are not migrated by this bundle and must remain preserved separately.
