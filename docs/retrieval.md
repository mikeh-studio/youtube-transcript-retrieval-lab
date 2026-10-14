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

When Search runs over **All videos**, `/v1/ask` first shortlists relevant
videos, then retrieves chunks only from them. The router builds identity
(title and channel), overview, and timestamped section vectors for each video and
ranks videos by dense aggregation plus lexical RRF. Router artifacts are stored
separately from the chunk index and rebuilt when video inputs change.

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
matches it uses the normalized dense score instead of lexical overlap. The
response status is `answered`, `insufficient_evidence`, or `error`, with a
`high`, `medium`, or `low` confidence level. Sampling temperature is kept at
`0.2` for models that accept it.

The sufficiency thresholds are hand-tuned and not yet measured by answer-level
evaluation; see [`TODOS.md`](../TODOS.md).
