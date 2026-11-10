"""Regression coverage for the local preview's annotation-based metrics."""

import shutil
import subprocess
from pathlib import Path

import pytest


def test_metrics_require_definitive_pool_judgments():
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to exercise the browser metric calculation")
    script = r"""
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const source = fs.readFileSync("local_preview/web/evaluation.js", "utf8");
const key = source.slice(source.indexOf("function getChunkKey("), source.indexOf("function defaultQuerySet("));
const metrics = source.slice(source.indexOf("function computeNdcgAtK("), source.indexOf("function renderMetrics("));
vm.runInThisContext(key + metrics);
const run = { k: 1, results: [{ query_id: "q", query_text: "test", result_count: 2,
  items: [{video_id: "v", chunk_index: 0}, {video_id: "v", chunk_index: 1}],
  labels: {"v:0": {label: "relevant"}, "v:1": {label: "unsure"}} }] };
recomputeRunMetrics(run);
assert.equal(run.metrics.annotation_status, "incomplete");
assert.equal(run.metrics.aggregate.precision_at_k, null);
assert.equal(run.metrics.per_query[0].unsure_count, 1);
run.results[0].labels["v:1"].label = "relevant";
recomputeRunMetrics(run);
assert.equal(run.metrics.annotation_status, "complete");
assert.equal(run.metrics.aggregate.precision_at_k, 1);
assert.equal(run.metrics.aggregate.recall_at_k, 0.5);
assert.match(run.metrics.metric_semantics.recall_at_k, /not corpus recall/);
run.results.push({ query_id: "q2", query_text: "failed", items: [], labels: {}, error: "failed" });
recomputeRunMetrics(run);
assert.equal(run.metrics.aggregate.precision_at_k, null);
assert.equal(run.metrics.per_query[1].annotation_status, "error");
"""
    subprocess.run(
        [node, "-e", script], cwd=Path(__file__).resolve().parents[1], check=True
    )
