# Bilingual retrieval development study

Aggregate results from a local study completed on **13 November 2026**. Cleaning source framing before cross-encoder scoring and translating the lexical query each improved their declared development comparisons. These are separate interventions on exposed material, with mostly model-generated relevance labels. They do not establish held-out accuracy, a general winner or a production improvement. **Retrieval defaults are unchanged.**

## Population and comparison

The corpus contains seven saved caption editions: three English and four Japanese, totaling 450 overlapping chunks. There are 22 semantic targets with two equivalent query-language forms each (44 forms). Ranking metrics cover **21 answerable targets / 42 forms**, split into 21 same-language and 21 cross-language forms. One transcript-scoped negative target contributes two additional diagnostic forms; it is excluded from ranking aggregates. Each search was restricted to its designated recording: this measures passage ranking within a known source, not finding the right video across the library.

The four controls are dense, BM25, hybrid, and hybrid with a cross-encoder (CE). Three additional arms isolate query inputs:

- **A: cleaned-query CE.** Remove one anchored source/program/episode framing span before CE scoring, preserving requested scope and every answer part. Eighteen hand-authored, source-specific rules for this exposed development set change 38 of 44 forms; six remain unchanged. Their generalization beyond this set is untested. Dense retrieval, BM25, passage text and the full pre-CE candidate list remain identical to the controls. Judgments use the original question.
- **B: translated BM25.** Use a prepared, equivalence-reviewed query in the source language for BM25; native-language forms are exact identity controls.
- **B secondary: translated hybrid.** Combine translated lexical retrieval with the original dense query. No CE is applied.

**A and B were never combined.** All four control top-ten lists reproduced the earlier saved rankings for every form. A retained the original pre-CE candidates. Both B arms retained native-language control rankings; only translated hybrid has a dense stage, which retains the original dense query. Translated BM25 is purely lexical. All seven arms are scored against one expanded common judgment pool.

## Overall results

Means below use all 42 answerable forms, including one fully judged zero-positive pool scored as zero for every metric. A cleaned-query CE has the highest observed nDCG, while translated hybrid has the highest observed Hit@10. A point leader does not establish general superiority.

| Arm | nDCG@10 | Hit@3 | Hit@10 | Pooled Recall@5 | Pooled Recall@10 | MRR@10 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Dense | 0.6262 | 0.7857 | 0.8333 | 0.5960 | 0.6992 | 0.6718 |
| BM25 | 0.4478 | 0.5000 | 0.5952 | 0.4532 | 0.5500 | 0.4686 |
| Hybrid | 0.5902 | 0.6429 | 0.8095 | 0.5452 | 0.7206 | 0.6317 |
| Hybrid + original-query CE | 0.3817 | 0.4048 | 0.5952 | 0.3556 | 0.4754 | 0.4149 |
| A: hybrid + cleaned-query CE | 0.7790 | 0.8095 | 0.8810 | 0.7421 | 0.8603 | 0.8135 |
| B: translated BM25 | 0.6122 | 0.6429 | 0.8571 | 0.6905 | 0.7722 | 0.6234 |
| B secondary: translated hybrid | 0.6927 | 0.7619 | 0.9048 | 0.6706 | 0.8214 | 0.6965 |

## Declared paired effects

Differences are **absolute changes on the 0–1 nDCG scale**, not relative percentage improvements. A uses all 42 answerable forms; each B comparison uses only the 21 cross-language answerable forms. Averaging B over all forms would dilute its effect with unchanged native controls.

| Comparison | Forms | Δ nDCG@10 | Target 95% interval | Family sensitivity 95% interval |
| --- | ---: | ---: | --- | --- |
| A cleaned CE − original-query CE | 42 | +0.3974 | [0.2339, 0.5558] | [0.2225, 0.5605] |
| B translated BM25 − BM25 | 21 | +0.3289 | [0.1724, 0.4833] | [0.1568, 0.4935] |
| B translated hybrid − hybrid (secondary) | 21 | +0.2049 | [0.1173, 0.2997] | [0.0909, 0.3279] |

Intervals use 10,000 paired semantic-target bootstrap resamples, seed 20261112. Both language forms and all arm outcomes stay together. The sensitivity analysis resamples whole episode families and divides by the actual sampled form count. These percentile intervals condition on fixed labels; they exclude label error and have no multiplicity adjustment. With only 21 targets and seven families, they support exploratory development conclusions. Individual forms can regress despite positive averages.

## Query language → source language

The table reports mean nDCG@10. EN-source and JA-source cohorts contain different topics; differences between them cannot isolate a source-language effect. Translations of the same target are paired observations, not independent questions.

| Arm | EN → EN (9) | JA → EN (9) | EN → JA (12) | JA → JA (12) |
| --- | ---: | ---: | ---: | ---: |
| Dense | 0.5615 | 0.5221 | 0.6238 | 0.7552 |
| BM25 | 0.5250 | 0.3063 | 0.1557 | 0.7880 |
| Hybrid | 0.5544 | 0.3769 | 0.5301 | 0.8372 |
| Hybrid + original-query CE | 0.3595 | 0.2780 | 0.3877 | 0.4700 |
| A: hybrid + cleaned-query CE | 0.7467 | 0.6514 | 0.8257 | 0.8522 |
| B: translated BM25 | 0.5250 | 0.4905 | 0.5930 | 0.7880 |
| B secondary: translated hybrid | 0.5544 | 0.5369 | 0.7686 | 0.8372 |

Small direction cells are descriptive. The overall matched JA-minus-EN nDCG difference for cleaned CE is −0.0257, with target 95% interval [−0.1519, 0.0768]; language parity is not established.

## Labels and metric definitions

The expanded pool contains **1,101 exact query-form/chunk pairs**: 915 labels carried forward unchanged and 186 newly pooled pairs with three independent model judgments each. Effective labels comprise **1,100 model-only items and one exact human-adjudicated item**. Model agreement or majority is not human gold. No grade-2 sufficient-passage label is human-confirmed. There are two separate audit cohorts: 53 earlier agreement flags and 24 newly judged uncertainty/low-confidence flags; a broad human audit remains pending. Labels remain specific to the original query form; a decision for one language form is not automatically transferred to its translation.

- Grades are **0: no answer support; 1: partial support; 2: sufficient standalone support for the full question**.
- **nDCG@10** discounts gains 0/1/3 by `1/log2(rank+1)`. Its ideal ranking uses the full common exact-form pool, truncated at ten.
- **Hit@3/10** requires at least one grade-2 passage within the cutoff.
- **Pooled Recall@5/10** counts retrieved grade-at-least-1 passages divided by all such positives in the common pool. It is not exhaustive corpus recall; overlapping chunks can represent related evidence.
- **MRR@10** uses the reciprocal rank of the first grade-at-least-1 passage, or zero if none. Partial support can therefore earn reciprocal-rank credit without a complete answer.

Baseline and every challenger use the expanded pool. Changing that pool changes IDCG and Recall denominators even when baseline ranks are unchanged, so cross-study score changes must not be attributed to a retrieval change. Both negative forms returned candidates but no grade-2 item in the top ten for any arm. This does not test answer refusal, false-answer rates or full-audio absence.

## Configuration and measured cost

The study uses `intfloat/multilingual-e5-large` at public model revision `3d7cfbdacd47fdda877c5cd8a79fbcc4f2a574f3` and `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` at revision `1427fd652930e4ba29e8149678df786c240d8825`. Caption windows target 60 seconds with 15-second overlap, retaining whole caption segments. Each dense/lexical stage requests up to 80 edition-scoped candidates, equal reciprocal-rank fusion uses constant 60, and CE scores the first up-to-50 fused candidates. Every arm applies the same diversity selection and returns up to ten passages. Feedback tuning was disabled.

One CPU process loaded each model and built the index once. A fixed shuffled order covered 44 forms × seven arms, each with one untimed warmup and two timed repeats: **924 calls, 308 warmups and 616 timed observations**. Model/index construction is excluded from warm timings. These repetitions check stability; they are not additional independent questions.

| Arm | Timed observations | Warm p50 ms | Warm p95 ms |
| --- | ---: | ---: | ---: |
| Dense | 88 | 195.47 | 295.24 |
| BM25 | 88 | 3.04 | 7.83 |
| Hybrid | 88 | 177.73 | 278.86 |
| Hybrid + original-query CE | 88 | 2991.11 | 3887.52 |
| A: hybrid + cleaned-query CE | 88 | 2775.00 | 3741.28 |
| B: translated BM25 | 88 | 5.54 | 7.64 |
| B secondary: translated hybrid | 88 | 179.10 | 293.41 |

Measurements used an eight-logical-CPU Apple Silicon machine, CPU embedding/CE, and PyTorch intra-op/inter-op threads 1/8. Instrumentation and retrieval/diversity costs are included. **Prepared query translation, equivalence review and their time/cost are excluded**, so B timings are not end-to-end translated-query serving latency. Both CE p95 values exceed the provisional two-second development budget. The figures establish neither a production SLA nor a historical speedup.

## Availability and remaining validation

This repository publishes aggregate findings only. The study-specific orchestration, corpus, exact queries, labels and individual results are not distributed, so **the exact study cannot be reproduced from this checkout**. The [public real-model runner](../evals/REAL_EVAL.md) is a general workflow for separately supplied private inputs; it does not reproduce this local experiment or install these query transformations. The [evaluation guide](evaluation.md) explains the public fixture and annotation boundaries.

There is no sealed holdout, representative source-admission claim, independent source/audio fidelity validation or broad human relevance audit. Further work requires separately admitted sources, family-separated unseen targets, human label checks, a declared answer/abstention mechanism and an end-to-end cost budget that includes translation. No retrieval default or deployment changes follow from these results.
