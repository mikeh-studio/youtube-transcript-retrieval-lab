PYTHON ?= python
MANIFEST ?= data/evaluation/manifest.json
CONFIG ?= evals/configs/real_baseline.json
OUT ?= evals/reports/real-$(SPLIT)
SPLIT ?= dev
HOLDOUT_FLAG ?=

.PHONY: eval eval-smoke experiment

eval:
	$(PYTHON) -m evals.real_runner --manifest "$(MANIFEST)" --config "$(CONFIG)" --out "$(OUT)" --split "$(SPLIT)" $(HOLDOUT_FLAG)

eval-smoke:
	$(PYTHON) -m evals.runner --dataset evals/datasets/jp_core_v1.example.jsonl --config evals/configs/baseline.yaml --out evals/reports/smoke

experiment:
	$(PYTHON) -m evals.experiments compare $(ARGS)
