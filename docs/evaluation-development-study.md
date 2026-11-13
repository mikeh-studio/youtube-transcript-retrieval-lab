# Bilingual retrieval development study

This **13 November 2026** study measures passage ranking within a known recording, not video discovery. It covers **21 answerable targets / 42 EN+JA forms**. **1,100/1,101 labels are model-only; no sufficient-passage label is human-confirmed.** These are exposed development results.

## Findings and declared effects

1. **Original-query cross-encoder reranking performed poorly.** Its nDCG@10 was **0.3817**, the poorest control, versus **0.6262 dense** and **0.5902 hybrid**.
2. **Arm A demonstrates sensitivity to query framing.** Cleaning source framing only before cross-encoder (CE) scoring raised observed nDCG to **0.7790**. The **+0.3974** effect is against that weak CE comparator, not dense or hybrid. Its hand-authored, source-specific rules do not establish superiority on unseen questions.
3. **Arm B improves cross-language lexical retrieval.** Original cross-language questions were translated offline into the recording’s language and independently checked for equivalence; same-language questions passed through verbatim. Secondary translated hybrid adds the original dense query, without CE. Pure translated BM25 has no dense stage. **A and B were never combined.**

| Declared comparison | Answerable forms | Absolute Δ nDCG@10 | Paired target 95% interval |
| --- | ---: | ---: | --- |
| A cleaned CE − original-query CE | 42 | +0.3974 | [0.2339, 0.5558] |
| B translated BM25 − BM25 | 21 cross-language | +0.3289 | [0.1724, 0.4833] |
| B translated hybrid − hybrid (secondary) | 21 cross-language | +0.2049 | [0.1173, 0.2997] |

Changes are absolute on the 0–1 nDCG scale, not relative percentages. B excludes unchanged native controls. Intervals condition on fixed rules/labels, excluding rule-selection and generalization uncertainty. Full aggregates and family sensitivity follow in the appendix.

## Decision and tradeoff

**Defaults remain unchanged.** Cleaned CE has the highest observed nDCG, but warm p50 is **2775.00 ms**, versus **195.47 ms dense** and **177.73 ms hybrid**. Its p95 is **3741.28 ms**, versus **278.86 ms hybrid**, exceeding the provisional two-second development budget. A cleaned-CE-versus-hybrid paired comparison was not predeclared; no new interval is presented here.

Next, translated hybrid warrants fresh evaluation with measured automatic translation and human support checks. Published timings exclude prepared translation/review; live translation quality and end-to-end cost remain unmeasured.

## Limits

- No sealed holdout, representative source admission or source/audio fidelity validation. Source cohorts contain different topics, confounding source-language effects.
- Broad human audit remains pending. Model agreement is not human correctness; positive averages conceal individual regressions.
- Exploratory intervals have no multiplicity adjustment. Translations, overlapping chunks and timing repetitions are dependent; pooled recall is not exhaustive corpus recall.
- Negative diagnostics do not test refusal, false-answer rates or full-audio absence; no answer/abstention mechanism was evaluated.
- **The exact study is not reproducible from this checkout:** its private questions, labels and corpus, and local experiment-specific adapters, are unavailable here. The [public general-purpose runner](../evals/REAL_EVAL.md) accepts separately supplied inputs; it does not reproduce this experiment or install its transformations. [Evaluation guide](evaluation.md).

## Appendix: population, methods and full aggregates

### Population and controls

Seven caption editions (three EN, four JA) contain 450 chunks and **seven episode families, one per edition**; families group related sources for resampling. The 22 targets / 44 forms include 21 answerable targets, split into 21 native and 21 cross-language forms, plus one negative target / two diagnostic forms.

All four controls reproduced saved top-ten rankings for every form. A changes only the CE query, retaining dense/BM25 retrieval, passage text and pre-CE candidates. Eighteen source-specific rules change 38/44 forms; six are identities, with scope and all answer parts preserved. B retains native control rankings. Every arm is judged against the original question in one expanded exact-form pool.

### Overall ranking metrics

All means retain 42 answerable forms, including one fully judged zero-positive pool scored zero for every metric. Translated hybrid has the highest observed Hit@10; point leaders depend on the metric.

| Arm | nDCG@10 | Hit@3 | Hit@10 | Pooled Recall@5 | Pooled Recall@10 | MRR@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 0.6262 | 0.7857 | 0.8333 | 0.5960 | 0.6992 | 0.6718 |
| BM25 | 0.4478 | 0.5000 | 0.5952 | 0.4532 | 0.5500 | 0.4686 |
| Hybrid | 0.5902 | 0.6429 | 0.8095 | 0.5452 | 0.7206 | 0.6317 |
| Hybrid + original-query CE | 0.3817 | 0.4048 | 0.5952 | 0.3556 | 0.4754 | 0.4149 |
| A: hybrid + cleaned-query CE | 0.7790 | 0.8095 | 0.8810 | 0.7421 | 0.8603 | 0.8135 |
| B: translated BM25 | 0.6122 | 0.6429 | 0.8571 | 0.6905 | 0.7722 | 0.6234 |
| B secondary: translated hybrid | 0.6927 | 0.7619 | 0.9048 | 0.6706 | 0.8214 | 0.6965 |

### Query language → source language

Each cell is mean nDCG@10; counts in headers are forms. Small cells are descriptive. Both language forms of a target remain paired.

| Arm | EN → EN (9) | JA → EN (9) | EN → JA (12) | JA → JA (12) |
| --- | ---: | ---: | ---: | ---: |
| Dense | 0.5615 | 0.5221 | 0.6238 | 0.7552 |
| BM25 | 0.5250 | 0.3063 | 0.1557 | 0.7880 |
| Hybrid | 0.5544 | 0.3769 | 0.5301 | 0.8372 |
| Hybrid + original-query CE | 0.3595 | 0.2780 | 0.3877 | 0.4700 |
| A: hybrid + cleaned-query CE | 0.7467 | 0.6514 | 0.8257 | 0.8522 |
| B: translated BM25 | 0.5250 | 0.4905 | 0.5930 | 0.7880 |
| B secondary: translated hybrid | 0.5544 | 0.5369 | 0.7686 | 0.8372 |

The cleaned-CE **JA-query minus EN-query, matched by target**, nDCG difference is −0.0257, target 95% interval [−0.1519, 0.0768]; language parity is not established.

### Labels, metrics and uncertainty

The 1,101 exact form/chunk pairs comprise 915 unchanged labels and 186 new pairs with three independent model judgments each; one historical human decision is retained. Separate audit cohorts contain 53 earlier agreement flags and 24 new uncertainty/low-confidence flags. Labels and human decisions are form-specific, not automatically transferred to translations.

- **Grades:** 0 = no answer support; 1 = partial support; 2 = sufficient standalone support for the full question.
- **nDCG@10:** gains 0/1/3 discounted by `1/log2(rank+1)`; ideal ranking from the full common exact-form pool, truncated at ten.
- **Hit@3/10:** at least one grade-2 passage within the cutoff.
- **Pooled Recall@5/10:** retrieved grade-at-least-1 passages divided by all such positives in the common pool. Overlapping chunks can represent related evidence.
- **MRR@10:** reciprocal rank of the first grade-at-least-1 passage, or zero if none; partial support earns credit without a complete answer.

Baseline and challengers share the expanded pool. Changing its IDCG/Recall denominators shifts scores without changing ranks. Both negative forms returned candidates but no grade-2 top-ten item in any arm.

Intervals use 10,000 paired semantic-target bootstrap resamples, seed 20261112, keeping both translations and all arm outcomes together. Sensitivity resamples whole episode families using the actual sampled form denominator. The three declared effects have these family intervals:

| Declared comparison | Family sensitivity 95% interval |
| --- | --- |
| A cleaned CE − original-query CE | [0.2225, 0.5605] |
| B translated BM25 − BM25 | [0.1568, 0.4935] |
| B translated hybrid − hybrid (secondary) | [0.0909, 0.3279] |

### Configuration and latency

Models are `intfloat/multilingual-e5-large` at public revision `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` and `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` at revision `1427fd652930e4ba29e8149678df786c240d8825`. Caption windows target 60 seconds with 15-second overlap, retaining whole segments. Each dense/lexical stage requests up to 80 edition-scoped candidates. Equal reciprocal-rank fusion uses constant 60; CE scores the first up-to-50 fused candidates. Every arm applies the same diversity selection and returns up to ten passages. Feedback tuning was disabled.

One CPU process loaded each model and built one index. Fixed shuffled tasks cover 44 forms × seven arms × one untimed warmup plus two timed repeats: **924 calls, 308 warmups, 616 timed observations**. Warm timings exclude construction.

| Arm | Timed observations | Warm p50 ms | Warm p95 ms |
| --- | ---: | ---: | ---: |
| Dense | 88 | 195.47 | 295.24 |
| BM25 | 88 | 3.04 | 7.83 |
| Hybrid | 88 | 177.73 | 278.86 |
| Hybrid + original-query CE | 88 | 2991.11 | 3887.52 |
| A: hybrid + cleaned-query CE | 88 | 2775.00 | 3741.28 |
| B: translated BM25 | 88 | 5.54 | 7.64 |
| B secondary: translated hybrid | 88 | 179.10 | 293.41 |

The machine has eight logical CPUs (Apple Silicon); embedding/CE used CPU, with PyTorch intra-op/inter-op threads 1/8. Instrumentation, retrieval and diversity costs are included. Prepared query translation, equivalence review and their time/cost are excluded. These are descriptive measurements for this workload; differences between arm medians do not identify a hardware cause, historical speedup or production SLA.
