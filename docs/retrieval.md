# Retrieval

Every retrieval path starts from the same evidence: chunks derived from the
canonical timestamped transcript (`full_transcript.segments`). The default
pipeline is plain hybrid search; everything else on this page is opt-in.

## Retrieval modes

| Mode | How it ranks |
| --- | --- |
| `dense` | E5 embeddings in a FAISS index (`intfloat/multilingual-e5-large` by default) |
| `lexical` | BM25. Japanese text is tokenized with fugashi morphological analysis, falling back to character bigrams |
| `hybrid` | Fuses dense and lexical rankings. The default profile is `baseline_rrf` (reciprocal-rank fusion) |

`optimized_v1` is a benchmark candidate hybrid profile that combines normalized
dense and lexical scores plus a small bonus for chunks found by both. It is not
the default. Opt in per request with `"retrieval_profile": "optimized_v1"` or
globally with `YT_RAG_HYBRID_PROFILE=optimized_v1`. Its min-max normalization
depends on the candidate pool, so rankings can shift with `candidate_k` or corpus
scope; see the [benchmark caveats](../evals/README.md#fixture-result).

After fusion, results are adjusted by stored relevance feedback (disable with
`YT_RAG_FEEDBACK_TUNING=0`) and diversified so one passage does not fill the list.

## Search request options

`POST /v1/search` also accepts:

| Field | Effect |
| --- | --- |
| `video_ids` | List of indexed video IDs to search (up to 500). Omit it to search the whole library; an empty list returns no results. Unknown IDs return `404`. |
| `feedback_tuning` | `false` skips the relevance-feedback adjustment for this request; omit it to use the server default. The Search workbench always sends `false`, so labels never reshape the rankings being compared. |

Both apply to plain and agentic search.

## Cross-encoder reranking

Rescores fused candidates with a multilingual cross-encoder
(`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` by default) before feedback tuning
and diversity selection.

- Per request: `"reranker": "cross_encoder"` on `/v1/search` or `/v1/ask`
- Globally: `YT_RAG_RERANKER=1`; change the model with `YT_RAG_RERANKER_MODEL`

If the model cannot be downloaded, reranking is skipped and the load error is
reported in `retrieval_details.reranker`.

## Agentic search

Agentic search is an orchestration strategy, not a fourth retrieval mode. Its
policy is deterministic and needs no provider key:

1. Choose Japanese BM25 or E5 + FAISS for the query.
2. Assess whether the results are sufficient evidence.
3. If evidence is weak, try the other search tool.
4. Call `read_context` around strong timestamp anchors in the canonical transcript.

Enable it per request with `"agentic": true` on `/v1/search` or `/v1/ask`, or
globally with `YT_RAG_AGENTIC_RETRIEVAL=1`. The full tool trace is returned in
`retrieval_details.agentic_retrieval`. If no path reaches sufficient evidence,
the strongest attempted result set is returned and the normal
insufficient-evidence answer behavior still applies.

## Video-first routing

A library-wide `/v1/ask` request with `video_routing: "multi_vector"` first
shortlists relevant videos, then retrieves chunks from them. The router builds identity
(title and channel), overview, and timestamped section vectors for each video and
ranks videos by dense aggregation plus lexical RRF. Router artifacts are stored
separately from the chunk index and rebuilt when video inputs change.
With `video_routing: "multi_vector"` and no `video_id`, Ask defaults to ten
chunks unless `k` or `top_k` is supplied.

For a multilingual query expansion, each language variant uses the same
relevance formula before shortlisting and during fusion. The score is the
aggregated embedding cosine plus 0.25 times normalized token overlap. Translated
variants use the same weights as the original. Each candidate's
`variant_hits` includes the raw signals and the combined `relevance_score`.

Embedding scores are compressed, so the best match in an unrelated language pool
can score almost as high as a real match. Before merging, each video is scored
by how far its relevance sits above the median of its own language pool
(`pool_score`), with raw relevance breaking ties. A pool of fewer than three
videos is scored against the median of every candidate instead. Set
`YT_RAG_ROUTING_POOL_NORMALIZATION=none` to merge raw relevance scores instead.

When the routed videos span several languages, chunks from each language search
are merged by dense similarity, so a weak language search does not take an
equal share of the evidence. Stored relevance feedback still adjusts each
chunk's score. If a language search has no dense scores (lexical mode, or a
hybrid search that fell back to lexical) or was reranked by the cross-encoder,
chunks are merged by rank. Set `YT_RAG_MULTILINGUAL_CHUNK_MERGE=rrf` to always
merge by rank. `retrieval_details.video_routing.fusion` and
`multilingual_retrieval.fusion` report which methods ran.

```json
{
  "question": "How does fermentation timing affect the starter?",
  "video_routing": "multi_vector",
  "video_top_k": 3,
  "agentic": true
}
```

Routing details, selected video IDs, fallbacks, and latency are returned under
`retrieval_details.video_routing`. Router quality is measured separately by the
[video-routing evaluator](../evals/video_routing/README.md).

## Grounded answers

`/v1/ask` sends the retrieved evidence to the selected provider and requires
strict JSON with numbered citations. The evidence-sufficiency gate classifies
each chunk against the language the query was tokenized in. For cross-lingual
matches it uses the normalized dense score instead of lexical overlap. When a
question lists several parts (split at commas, semicolons, "and" and "or") and
no excerpt matches the whole question, an excerpt that matches one part still
passes with reason `partial_question_support`, a warning and confidence capped
at `medium`. The
response status is `answered`, `insufficient_evidence`, or `error`, with a
`high`, `medium`, or `low` confidence level. Sampling temperature is kept at
`0.2` for models that accept it.

When the evidence covers only some parts of a question, the model answers those
parts with citations, says which parts the excerpts do not cover, adds a warning
and caps confidence at `medium`. It returns `insufficient_evidence` only when
the excerpts answer no part of the question or contradict its central premise.
Every sentence that states a fact carries its own citation markers.

The sufficiency thresholds are hand-tuned and not yet measured by answer-level
evaluation.

## Caching and failure diagnostics

Lexical search reuses bounded document-token and scope-statistics caches. Keys
include ordered original text, language and tokenizer version, so edits, deletion,
rechunking and scope changes invalidate the relevant statistics. Citation metadata
is always taken from the current library rows. BM25 scoring and tie order remain
unchanged.

Scoped dense search on `IndexFlatIP` selects only the requested videos' vectors,
then ranks the entire selection before truncation to preserve full-search tie
order. Other index types retain the existing full-search path.

Retrieval `details` includes `status`, total `duration_ms`, and per-stage `dense`
and `lexical` status/timing. A failed stage reports its exception type without its
message; hybrid search can return surviving results with status `degraded`. An
empty successful search reports `no_matches`. The evaluation runner counts
`error` and `degraded` responses as failed queries while retaining their diagnostics.
These stage fields describe dense/lexical retrieval, not provider-answer success.

Synthetic parity/timing checks can be run with:

```sh
python -m evals.benchmark_lexical --help
python -m evals.benchmark_scoped_vectors --help
```

The benchmarks check ranking parity on synthetic data. Timing depends on the local
environment and does not establish real-corpus relevance or answer quality.

Agentic traces retain status, stage details and timing for every search attempt.
A successful fallback keeps an earlier failure visible as `degraded`; evaluations
inspect the complete attempt trace rather than only the selected result. Multi-language
retrieval preserves each language's diagnostics under
`multilingual_retrieval.language_details` and combines stage statuses and durations.
The cache uses the active morphological/bigram tokenizer version, including backend
changes within the same service process.
