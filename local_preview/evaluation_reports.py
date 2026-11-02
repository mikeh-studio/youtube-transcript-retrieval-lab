"""Explicit, immutable local imports for evaluation report review.

Only the review contract is retained; unknown fields (including credentials and
local paths) never reach the browser. No private directories are discovered.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re

# Fields deliberately supported by the review contract, at any nesting level.
_FIELDS = frozenset(
    """schema_version run_id title name source source_type status model effort profile profiles id arms direct mcp workflow parent_run_id continuation_of corpus_hash queryset_hash criteria_hash dataset_hash stage cutoff created_at completed_at reviewed_at review_duration changes cases question query question_id case_id answer text evidence citations url timestamp timestamp_seconds video_id video_title passage_id rank score grade grades metrics ranking initial_retrieval final_evidence precision_at_1 precision_at_5 precision_at_10 recall_at_1 recall_at_5 recall_at_10 mrr_at_10 ndcg_at_10 auc auc_contract answer_quality supporting_passage_recall citation_relevance citation_completeness exact_quote_fidelity timestamp_authenticity unnecessary_clarification attribution_errors video_selection assumption_first_handling value numerator denominator graded total accepted failed unattempted attempted failures error message trace tool_calls tool_name summary usage input_tokens output_tokens reasoning_output_tokens cached_input_tokens latency_ms cost api_equivalent_usd billed_usd raw_total_usd missing_usage_jobs usage_complete cost_basis includes_failed reason provenance metadata versions description original_text target count direct_label mcp_label reviewer source_gate_pass underspec_gate_pass supporting_passage covered citation_support citation_complete quote_required_fulfilled blocking_clarification reviewer reviewed_at reviewer graded_status review_method notes quality citation_support caveats critical material_claims supported_claims checks exact_quote timestamp_url_matches declared semantic_support grading_note cost_note workflow_note gates source_selection underspecification minimum_passes pricing_kind generated_at started_at finished_at assessed relevant supported citation_edge_support validation_rejections validation_failure attempted_output citation_ids retrieval_payloads hits document_id version_id source_id canonical_url references reference timestamp_url source_title title quote contributors contributor_ids case_ids question_ids requests uncached_usd estimated_api_equivalent_usd completed not_attempted source_run duration elapsed_ms answer_status claims kind missing_parts refinement_hint reasoning_tokens good partial poor Good Partial Poor distribution""".split()
)
_METRIC_KEYS = {
    "Precision@1": "precision_at_1",
    "Precision@5": "precision_at_5",
    "Precision@10": "precision_at_10",
    "Recall@1": "recall_at_1",
    "Recall@5": "recall_at_5",
    "Recall@10": "recall_at_10",
    "MRR@10": "mrr_at_10",
    "nDCG@10": "ndcg_at_10",
    "gold_recall@1": "recall_at_1",
    "gold_recall@5": "recall_at_5",
    "gold_recall@10": "recall_at_10",
}
_SECRET = re.compile(
    r"(?:sk-|Bearer\s+)[A-Za-z0-9_\-]{12,}"
    r"|\bgh[pousr]_[A-Za-z0-9]{20,}"
    r"|\bgithub_pat_[A-Za-z0-9_]{20,}"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|(?s:-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----.*?"
    r"-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)",
    re.I,
)
_URL_CREDENTIAL = re.compile(r"(https?://)[^\s/@]+@", re.I)
_QUERY_CREDENTIAL = re.compile(
    r"([?&](?:api[_-]?key|access[_-]?token|token|secret|password|"
    r"authorization|signature|x-amz-signature|x-amz-credential|"
    r"x-amz-security-token)=)[^&#\s]*",
    re.I,
)
_LOCAL_PATH = re.compile(
    r"(?<![\w:/])/(?:Users|home|private|tmp|var)/[^\s]+"
    r"|(?<![\w])(?:[A-Za-z]:\\(?:Users|Documents and Settings)\\)[^\r\n\"<>|]+"
)


def _redact_text(value):
    """Redact common credentials and local paths inside allowed text fields."""
    value = _URL_CREDENTIAL.sub(r"\1[redacted]@", value)
    value = _QUERY_CREDENTIAL.sub(r"\1[redacted]", value)
    return _LOCAL_PATH.sub("[local path]", _SECRET.sub("[redacted]", value))


def _clean(value, depth=0):
    if depth > 16:
        raise ValueError("Report nesting is too deep")
    if isinstance(value, dict):
        return {
            _METRIC_KEYS.get(k, k): (
                {
                    name: _clean(arm, depth + 2)
                    for name, arm in v.items()
                    if isinstance(name, str)
                    and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name)
                    and _redact_text(name) == name
                }
                if k == "arms" and isinstance(v, dict)
                else _clean(v, depth + 1)
            )
            for k, v in value.items()
            if k in _FIELDS or k in _METRIC_KEYS
        }
    if isinstance(value, list):
        if len(value) > 10000:
            raise ValueError("Report contains too many entries")
        return [_clean(v, depth + 1) for v in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if len(value) > 100000:
            raise ValueError("Report field is too long")
        return _redact_text(value)
    raise ValueError("Report contains unsupported data")


_IDENTITY_FIELDS = (
    "model",
    "effort",
    "stage",
    "cutoff",
    "corpus_hash",
    "queryset_hash",
    "criteria_hash",
    "dataset_hash",
    "metrics",
    "ranking",
    "contributors",
    "contributor_ids",
    "case_ids",
    "question_ids",
)


def _identity(source):
    """Copy only explicitly supplied review provenance and attribution."""
    return {key: source[key] for key in _IDENTITY_FIELDS if key in source}


def _subscription_cost(source):
    """Preserve supplied cost details without inventing a cost basis."""
    cost = dict(source["cost"]) if isinstance(source.get("cost"), dict) else {}
    if "estimated_api_equivalent_usd" in source:
        cost.setdefault("api_equivalent_usd", source["estimated_api_equivalent_usd"])
    for key in ("cost_basis", "includes_failed", "usage_complete"):
        if key in source:
            cost.setdefault(key, source[key])
    return cost


def _adapt_subscription(raw):
    profiles = []
    top_grades = raw.get("grades") or {}
    for p in raw.get("profiles", []):
        profile_id = p.get("profile")
        arms = {}
        for key, a in (p.get("arms") or {}).items():
            usage = a.get("usage") or {}
            profile_grades = (
                top_grades.get(profile_id, {}) if isinstance(top_grades, dict) else {}
            )
            saved_grades = a.get("grades")
            if saved_grades is None and isinstance(profile_grades, dict):
                saved_grades = profile_grades.get(key)
            arms[key] = {
                **_identity(a),
                "accepted": a.get("completed"),
                "attempted": a.get("attempted"),
                "failed": a.get("failed"),
                "validation_rejections": a.get("validation_rejections"),
                "unattempted": a.get("not_attempted"),
                "total": (
                    a["attempted"] + a["not_attempted"]
                    if a.get("attempted") is not None
                    and a.get("not_attempted") is not None
                    else None
                ),
                "usage": {
                    "input_tokens": usage.get("input_tokens"),
                    "output_tokens": usage.get("output_tokens"),
                    "reasoning_output_tokens": usage.get("reasoning_tokens"),
                    "cached_input_tokens": usage.get("cached_input_tokens"),
                },
                "cost": _subscription_cost(a),
                **{
                    key: a[key]
                    for key in ("cost_basis", "includes_failed", "usage_complete")
                    if key in a
                },
                "grades": saved_grades,
                "graded_status": "Not aggregated"
                if any(
                    isinstance(grades, dict)
                    and grades.get(f"{profile_id}-{key}") is not None
                    for grades in (top_grades.get("cases") or {}).values()
                )
                else None,
                "metrics": a.get("metrics"),
                "ranking": a.get("ranking"),
            }
        cases = []
        for c in raw.get("cases", []):
            for workflow in ("direct", "mcp"):
                entry = (c.get("runs") or {}).get(f"{profile_id}-{workflow}")
                if not isinstance(entry, dict):
                    continue
                result = entry.get("result") or {}
                answer = result.get("answer") or {}
                citations = (
                    answer.get("citations") or [] if isinstance(answer, dict) else []
                )
                cases.append(
                    {
                        "question_id": c.get("question_id"),
                        "question": c.get("question"),
                        "workflow": workflow,
                        "status": entry.get("status"),
                        "answer": answer.get("answer")
                        if isinstance(answer, dict)
                        else answer,
                        "error": entry.get("failure")
                        or (
                            answer.get("validation_failure")
                            if isinstance(answer, dict)
                            else None
                        ),
                        "validation_failure": answer.get("validation_failure")
                        if isinstance(answer, dict)
                        else None,
                        "attempted_output": {
                            **answer["raw_selection"],
                            "status": "rejected",
                        }
                        if isinstance(answer, dict)
                        and answer.get("validation_failure")
                        and isinstance(answer.get("raw_selection"), dict)
                        else None,
                        "usage": entry.get("usage"),
                        "cost": _subscription_cost(entry),
                        **{
                            key: entry[key]
                            for key in (
                                "cost_basis",
                                "includes_failed",
                                "usage_complete",
                            )
                            if key in entry
                        },
                        "grades": (
                            (top_grades.get("cases") or {}).get(c.get("question_id"))
                            or {}
                        ).get(f"{profile_id}-{workflow}")
                        or entry.get("grades"),
                        "evidence": [
                            {
                                "text": x.get("text") or x.get("quote"),
                                "url": x.get("timestamp_url") or x.get("url"),
                                "video_title": x.get("source_title") or x.get("title"),
                                "passage_id": x.get("reference"),
                                "source_id": x.get("source_id"),
                                "document_id": x.get("document_id"),
                                "version_id": x.get("version_id"),
                                "timestamp": x.get("timestamp"),
                                "timestamp_seconds": x.get("timestamp_seconds"),
                                "rank": x.get("rank"),
                            }
                            for x in (result.get("issued") or {}).values()
                            if isinstance(x, dict)
                        ]
                        if isinstance(result.get("issued"), dict)
                        else [],
                        "trace": {
                            "tool_calls": result.get("searches"),
                            "retrieval_payloads": result.get("retrieval_payloads"),
                        },
                        "citations": [
                            {
                                "text": x.get("quote") or x.get("text"),
                                "url": x.get("url") or x.get("timestamp_url"),
                                "video_title": x.get("title") or x.get("source_title"),
                                "passage_id": x.get("reference"),
                            }
                            for x in citations
                            if isinstance(x, dict)
                        ],
                    }
                )
        profiles.append(
            {
                "id": profile_id,
                **_identity(p),
                "model": p.get("model"),
                "effort": p.get("effort"),
                "status": p.get("status"),
                "started_at": p.get("started_at"),
                "finished_at": p.get("finished_at"),
                "arms": arms,
                "cases": cases,
            }
        )
    return {
        "run_id": raw.get("run_id"),
        **_identity(raw),
        "source": "Codex CLI subscription",
        "created_at": raw.get("generated_at"),
        "status": raw.get("status"),
        "corpus_hash": raw.get("corpus_hash"),
        "queryset_hash": raw.get("queryset_hash"),
        "criteria_hash": raw.get("criteria_hash"),
        "dataset_hash": raw.get("dataset_hash"),
        "parent_run_id": raw.get("parent_run_id"),
        "continuation_of": raw.get("continuation_of"),
        "gates": raw.get("gates"),
        "reviewer": (raw.get("grades") or {}).get("reviewer"),
        "reviewed_at": (raw.get("grades") or {}).get("reviewed_at"),
        "grading_note": raw.get("grading_note"),
        "profiles": profiles,
    }


def _adapt_api(raw):
    runs = raw.get("runs") or []
    questions = {q.get("id"): q.get("question") for q in raw.get("questions", [])}
    overall = (raw.get("groups") or {}).get("Overall") or {}
    arms, cases = {}, []
    for workflow in ("direct", "mcp"):
        rows = [r for r in runs if r.get("workflow") == workflow]
        requests = [request for row in rows for request in row.get("requests", [])]
        complete_usage = bool(requests) and all(
            isinstance(x.get("usage"), dict)
            and x["usage"].get("input_tokens") is not None
            and x["usage"].get("output_tokens") is not None
            for x in requests
        )
        usage = None
        if complete_usage:
            usage = {
                key: sum(x["usage"][key] for x in requests)
                for key in ("input_tokens", "output_tokens")
            }
        costs_known = bool(requests) and all(
            x.get("uncached_usd") is not None for x in requests
        )
        group = overall.get(workflow) or {}
        graded = group.get("graded")
        grades = None
        if graded is not None and graded > 0 and group.get("acceptable") is not None:
            grades = {
                "answer_quality": {
                    "numerator": group["acceptable"],
                    "denominator": graded,
                    "graded": graded,
                    "total": overall.get("expected_per_arm"),
                }
            }
        arms[workflow] = {
            **_identity(group),
            "attempted": sum(
                r.get("status") not in ("unattempted", "not_attempted") for r in rows
            ),
            "failed": sum(
                r.get("status")
                in (
                    "failed",
                    "answer_validation_failed",
                    "model_contract_failed",
                    "rejected",
                )
                for r in rows
            ),
            "unattempted": sum(
                r.get("status") in ("unattempted", "not_attempted") for r in rows
            ),
            "accepted": group.get("completed"),
            "total": overall.get("expected_per_arm"),
            "usage": usage,
            "missing_usage_jobs": sum(not x.get("usage") for x in requests)
            if requests
            else None,
            "cost": {
                "raw_total_usd": sum(float(x["uncached_usd"]) for x in requests)
                if costs_known
                else None,
                "includes_failed": True,
                "cost_basis": "Uncached API-equivalent estimate across recorded requests",
            },
            "grades": grades,
        }
        for r in rows:
            answer = r.get("answer") or {}
            citations = (
                answer.get("citations") or [] if isinstance(answer, dict) else []
            )
            assessment = r.get("assessment")
            cases.append(
                {
                    "question_id": r.get("id"),
                    **_identity(r),
                    "requests": r.get("requests"),
                    "question": questions.get(r.get("id")),
                    "workflow": workflow,
                    "status": r.get("status"),
                    "answer": answer.get("answer")
                    if isinstance(answer, dict)
                    else answer,
                    "grades": assessment,
                    "trace": r.get("citations"),
                    "citations": [
                        {
                            "text": x.get("quote") or x.get("text"),
                            "url": x.get("timestamp_url") or x.get("url"),
                            "passage_id": x.get("reference"),
                        }
                        for x in citations
                        if isinstance(x, dict)
                    ],
                    "error": r.get("error") or r.get("failure"),
                }
            )
    return {
        "run_id": raw.get("run_id") or "API continuation",
        **_identity(raw),
        "source": str(raw.get("client") or "API") + " heldout report",
        "status": raw.get("status"),
        "corpus_hash": raw.get("corpus_hash", raw.get("source_sha256")),
        "queryset_hash": raw.get("queryset_hash", raw.get("cases_sha256")),
        "criteria_hash": raw.get("criteria_hash"),
        "dataset_hash": raw.get("dataset_hash"),
        "continuation_of": raw.get("continuation_of"),
        "grading_note": raw.get("grading_note"),
        "cost_note": raw.get("cost_note"),
        "workflow_note": raw.get("workflow_note"),
        "cost": {
            "raw_total_usd": raw.get("uncached_usd")
            if raw.get("usage_unknown") == 0
            else None,
            "missing_usage_jobs": raw.get("usage_unknown"),
            "includes_failed": True,
        },
        "profiles": [
            {
                "id": raw.get("model") or "API",
                **_identity(raw),
                "model": raw.get("model"),
                "status": raw.get("status"),
                "arms": arms,
                "cases": cases,
            }
        ],
    }


def _adapt_benchmark(raw):
    arms = {}
    for run in raw["runs"]:
        if not isinstance(run, dict) or not isinstance(run.get("metrics"), dict):
            continue
        name = run.get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", name):
            continue
        metrics = run["metrics"]
        arms[name] = {
            **_identity(run),
            "total": raw.get("query_count"),
            "failed": metrics.get("failed_query_count"),
            "ranking": {"initial_retrieval": metrics},
        }
    description = f"{raw['query_count']} recorded queries"
    if "example" in str(raw.get("dataset") or "").lower():
        description = (
            f"Synthetic example fixture · {raw['query_count']} recorded queries"
        )
    return {
        "run_id": raw.get("run_id") or "Offline retrieval benchmark",
        **_identity(raw),
        "source": "Offline retrieval benchmark",
        "stage": raw.get("stage", "initial_retrieval"),
        "description": description,
        "profiles": [{"id": "Retrieval methods", "arms": arms}],
    }


def normalize_report(payload):
    """Validate a CLI or API review report and normalize its common envelope."""
    if not isinstance(payload, dict):
        raise ValueError("Report must be a JSON object")
    if isinstance(payload.get("report"), dict):
        payload = payload["report"]
    if payload.get("schema") == "subscription-review/v1":
        profiles = payload.get("profiles")
        cases = payload.get("cases", [])
        grades = payload.get("grades")
        if (
            not isinstance(profiles, list)
            or not isinstance(cases, list)
            or (grades is not None and not isinstance(grades, dict))
        ):
            raise ValueError(
                "Subscription report profiles, cases, or grades are invalid"
            )
        if any(
            not isinstance(p, dict)
            or not isinstance(p.get("arms"), dict)
            or any(not isinstance(a, dict) for a in p["arms"].values())
            for p in profiles
        ):
            raise ValueError("Subscription report profile arms must be objects")
        if any(
            not isinstance(c, dict)
            or not isinstance(c.get("runs", {}), dict)
            or any(not isinstance(r, dict) for r in c.get("runs", {}).values())
            for c in cases
        ):
            raise ValueError("Subscription report cases and runs must be objects")
        payload = _adapt_subscription(payload)
    elif (
        payload.get("mode") == "heldout" or payload.get("heldout") is True
    ) and isinstance(payload.get("runs"), list):
        if any(
            not isinstance(r, dict)
            or not isinstance(r.get("requests", []), list)
            or any(not isinstance(x, dict) for x in r.get("requests", []))
            for r in payload["runs"]
        ):
            raise ValueError("Heldout report runs and requests must be objects")
        payload = _adapt_api(payload)
    elif (
        isinstance(payload.get("runs"), list)
        and isinstance(payload.get("query_count"), int)
        and isinstance(payload.get("selected_run"), dict)
    ):
        payload = _adapt_benchmark(payload)
    report = _clean(payload)
    run_id = report.get("run_id") or report.get("id")
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("Report needs a run_id")
    profiles = report.get("profiles")
    if profiles is None and isinstance(report.get("arms"), dict):
        profiles = [
            {
                "id": report.get("profile") or report.get("model") or "Default",
                "model": report.get("model"),
                "effort": report.get("effort"),
                "status": report.get("status"),
                "arms": report["arms"],
            }
        ]
    if (
        not isinstance(profiles, list)
        or not profiles
        or any(
            not isinstance(p, dict)
            or not isinstance(p.get("arms"), dict)
            or any(not isinstance(a, dict) for a in p["arms"].values())
            or not isinstance(p.get("cases", []), list)
            or any(not isinstance(c, dict) for c in p.get("cases", []))
            for p in profiles
        )
    ):
        raise ValueError("Report needs profiles with workflow arms")
    report["run_id"] = run_id
    report["profiles"] = profiles
    report["schema_version"] = "evaluation-run-report/v1"
    return report


class EvaluationReportStore:
    """Store sanitized snapshots by content hash without overwriting history."""

    def __init__(self, directory):
        self.directory = Path(directory)

    def import_report(self, payload):
        report = normalize_report(payload)
        encoded = json.dumps(
            report, ensure_ascii=False, sort_keys=True, allow_nan=False
        ).encode("utf-8")
        if len(encoded) > 10_000_000:
            raise ValueError("Report exceeds the 10 MB limit")
        report_id = hashlib.sha256(encoded).hexdigest()
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.directory / (report_id + ".json")
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(encoded.decode("utf-8"))
        except FileExistsError:
            pass
        return {**report, "id": report_id}

    def get_report(self, report_id):
        if not re.fullmatch(r"[0-9a-f]{64}", str(report_id)):
            raise ValueError("Invalid report ID")
        path = self.directory / (report_id + ".json")
        if not path.is_file():
            return None
        return {**json.loads(path.read_text(encoding="utf-8")), "id": report_id}

    def list_reports(self):
        if not self.directory.exists():
            return []
        reports = []
        for path in sorted(self.directory.glob("*.json")):
            if re.fullmatch(r"[0-9a-f]{64}", path.stem):
                report = self.get_report(path.stem)
                if report:
                    reports.append(report)
        return reports
