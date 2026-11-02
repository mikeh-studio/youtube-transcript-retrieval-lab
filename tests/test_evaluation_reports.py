import pytest

from local_preview.evaluation_reports import EvaluationReportStore, normalize_report


@pytest.mark.parametrize(
    "private_text",
    [
        "gh" + "p_" + "A" * 36,
        "github_" + "pat_" + "A" * 40,
        "AK" + "IA" + "A" * 16,
        "https://reviewer:private-password@example.test/report",
        "https://example.test/report?api_key=private-value&view=summary",
        "https://example.test/report?X-Amz-Signature=private-value",
        "C:\\Users\\example\\private-report.json",
        "-----BEGIN "
        + "PRIVATE KEY-----\nfake key material\n-----END "
        + "PRIVATE KEY-----",
    ],
)
def test_allowed_report_fields_redact_embedded_credentials_and_paths(private_text):
    report = normalize_report(
        {"run_id": "privacy", "arms": {"direct": {}}, "notes": private_text}
    )
    assert private_text not in report["notes"]
    assert "private-value" not in report["notes"]
    assert "private-password" not in report["notes"]


def test_report_redaction_preserves_exact_quotes_and_timestamp_links():
    quote = "ヒンメルならそうした。"
    url = "https://www.youtube.com/watch?v=abcdefghijk&t=46s"
    report = normalize_report(
        {
            "run_id": "quotes",
            "arms": {"direct": {}},
            "citations": [{"quote": quote, "url": url}],
        }
    )
    assert report["citations"] == [{"quote": quote, "url": url}]


def test_dynamic_workflow_names_cannot_bypass_report_redaction():
    private_name = "gh" + "p_" + "A" * 36
    report = normalize_report(
        {"run_id": "privacy", "arms": {private_name: {}, "direct": {}}}
    )
    assert report["profiles"][0]["arms"] == {"direct": {}}


def test_import_is_immutable_sanitized_and_keeps_unknown(tmp_path):
    store = EvaluationReportStore(tmp_path)
    raw = {
        "run_id": "run-1",
        "source": "CLI",
        "credentials": "secret",
        "profiles": [
            {
                "id": "one",
                "arms": {
                    "direct": {"usage": None, "grades": None},
                    "mcp": {"failed": 2},
                },
            }
        ],
    }
    first = store.import_report(raw)
    assert first == store.import_report(raw)
    assert first["profiles"][0]["arms"]["direct"]["usage"] is None
    assert "credentials" not in first
    assert store.get_report(first["id"]) == first
    assert len(store.list_reports()) == 1
    raw["profiles"][0]["arms"]["mcp"]["failed"] = 3
    second = store.import_report(raw)
    assert second["id"] != first["id"] and len(store.list_reports()) == 2
    with pytest.raises(ValueError):
        store.get_report("../bad")


def test_subscription_preserves_ungraded_and_per_profile_cases():
    report = normalize_report(
        {
            "schema": "subscription-review/v1",
            "run_id": "r",
            "profiles": [
                {
                    "profile": "a",
                    "status": "partial",
                    "arms": {
                        "direct": {
                            "completed": 1,
                            "attempted": 1,
                            "failed": 0,
                            "not_attempted": 1,
                            "usage": None,
                            "grades": None,
                        }
                    },
                }
            ],
            "cases": [
                {
                    "question_id": "q",
                    "question": "Question?",
                    "runs": {
                        "a-direct": {
                            "status": "completed",
                            "result": {"answer": {"answer": "Answer", "citations": []}},
                        }
                    },
                }
            ],
        }
    )
    arm = report["profiles"][0]["arms"]["direct"]
    assert arm["accepted"] == 1 and arm["unattempted"] == 1
    assert arm["grades"] is None and arm["usage"]["input_tokens"] is None
    assert report["profiles"][0]["cases"][0]["answer"] == "Answer"


def test_reject_invalid_import():
    with pytest.raises(ValueError, match="profiles"):
        normalize_report({"run_id": "r"})


def test_subscription_recorded_grades_and_rankings_survive():
    report = normalize_report(
        {
            "schema": "subscription-review/v1",
            "run_id": "graded",
            "corpus_hash": "corpus",
            "criteria_hash": "criteria",
            "gates": {"source_selection": {"denominator": 40, "minimum_passes": 34}},
            "grades": {
                "profile-a": {
                    "direct": {
                        "video_selection": {
                            "numerator": 32,
                            "denominator": 40,
                            "reviewer": "human",
                        }
                    }
                }
            },
            "profiles": [
                {
                    "profile": "profile-a",
                    "model": "example",
                    "effort": "high",
                    "arms": {
                        "direct": {
                            "completed": 1,
                            "attempted": 1,
                            "failed": 0,
                            "not_attempted": 0,
                            "grades": None,
                            "ranking": {
                                "initial_retrieval": {
                                    "Precision@5": 0.4,
                                    "nDCG@10": 0.7,
                                }
                            },
                        },
                        "mcp": {"attempted": None, "not_attempted": None},
                    },
                }
            ],
            "cases": [],
        }
    )
    direct = report["profiles"][0]["arms"]["direct"]
    assert direct["grades"]["video_selection"] == {
        "numerator": 32,
        "denominator": 40,
        "reviewer": "human",
    }
    assert direct["ranking"]["initial_retrieval"] == {
        "precision_at_5": 0.4,
        "ndcg_at_10": 0.7,
    }
    assert report["gates"]["source_selection"]["denominator"] == 40
    assert report["profiles"][0]["arms"]["mcp"]["total"] is None


def test_published_api_preserves_grades_attempt_costs_and_unknown_usage():
    report = normalize_report(
        {
            "heldout": True,
            "run_id": "api",
            "model": "example",
            "usage_unknown": 1,
            "uncached_usd": 0.3,
            "grading_note": "Provisional assisted grading",
            "questions": [{"id": "q1", "question": "Example question?"}],
            "groups": {
                "Overall": {
                    "expected_per_arm": 30,
                    "direct": {"completed": 1, "acceptable": 1, "graded": 1},
                }
            },
            "runs": [
                {
                    "id": "q1",
                    "workflow": "direct",
                    "status": "completed",
                    "assessment": {"quality": "Good", "review_method": "assisted"},
                    "answer": {"answer": "Example answer", "citations": []},
                    "citations": {"status": "pass", "checks": [{"exact_quote": True}]},
                    "requests": [
                        {
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                            "uncached_usd": 0.1,
                        }
                    ],
                },
                {
                    "id": "q2",
                    "workflow": "direct",
                    "status": "failed",
                    "requests": [{"usage": None, "uncached_usd": None}],
                },
            ],
        }
    )
    profile = report["profiles"][0]
    direct = profile["arms"]["direct"]
    assert direct["grades"]["answer_quality"]["numerator"] == 1
    assert direct["grades"]["answer_quality"]["graded"] == 1
    assert direct["total"] == 30 and direct["failed"] == 1
    assert direct["usage"] is None and direct["cost"]["raw_total_usd"] is None
    assert report["cost"]["raw_total_usd"] is None
    assert profile["cases"][0]["grades"]["review_method"] == "assisted"
    assert profile["cases"][0]["trace"]["checks"][0]["exact_quote"] is True
    assert profile["cases"][0]["question"] == "Example question?"
    assert report["grading_note"] == "Provisional assisted grading"


def test_raw_api_includes_failed_request_cost_when_known():
    report = normalize_report(
        {
            "mode": "heldout",
            "run_id": "raw",
            "runs": [
                {
                    "id": "q",
                    "workflow": "direct",
                    "status": "failed",
                    "requests": [
                        {
                            "usage": {"input_tokens": 20, "output_tokens": 3},
                            "uncached_usd": 0.02,
                        }
                    ],
                }
            ],
        }
    )
    direct = report["profiles"][0]["arms"]["direct"]
    assert direct["cost"]["raw_total_usd"] == 0.02 and direct["cost"]["includes_failed"]
    assert direct["usage"]["output_tokens"] == 3
    assert direct["grades"] is None


def test_subscription_case_grading_and_canonical_citations():
    report = normalize_report(
        {
            "schema": "subscription-review/v1",
            "run_id": "r",
            "grades": {
                "reviewer": "Reviewer",
                "reviewed_at": "2026-01-01",
                "cases": {
                    "q": {
                        "p-direct": {
                            "source_gate_pass": True,
                            "supporting_passage": {"covered": 1, "total": 2},
                            "citation_support": {"relevant": 2, "total": 3},
                            "citation_edge_support": {"supported": 1, "total": 2},
                            "notes": "Reviewed",
                        }
                    }
                },
            },
            "profiles": [{"profile": "p", "arms": {"direct": {"grades": None}}}],
            "cases": [
                {
                    "question_id": "q",
                    "question": "Question?",
                    "runs": {
                        "p-direct": {
                            "status": "completed",
                            "result": {
                                "answer": {
                                    "answer": "Answer",
                                    "citations": [
                                        {
                                            "quote": "Exact quote",
                                            "url": "https://example.com/t",
                                            "title": "Source",
                                        }
                                    ],
                                },
                                "issued": {
                                    "S1": {
                                        "text": "Passage",
                                        "timestamp_url": "https://example.com/s",
                                    }
                                },
                            },
                        }
                    },
                }
            ],
        }
    )
    profile = report["profiles"][0]
    assert profile["arms"]["direct"]["graded_status"] == "Not aggregated"
    case = profile["cases"][0]
    assert case["grades"]["source_gate_pass"] is True
    assert case["grades"]["supporting_passage"]["covered"] == 1
    assert case["grades"]["citation_support"]["relevant"] == 2
    assert case["grades"]["citation_edge_support"]["supported"] == 1
    assert case["citations"][0]["text"] == "Exact quote"
    assert case["citations"][0]["url"] == "https://example.com/t"
    assert case["evidence"][0]["text"] == "Passage"
    assert report["reviewer"] == "Reviewer"


def test_public_benchmark_shows_only_recorded_metrics():
    report = normalize_report(
        {
            "query_count": 8,
            "selected_run": {"name": "hybrid"},
            "runs": [
                {
                    "name": "hybrid",
                    "metrics": {"gold_recall@5": 1.0, "MRR@10": 0.75, "nDCG@10": 0.8},
                }
            ],
        }
    )
    profile = report["profiles"][0]
    ranking = profile["arms"]["hybrid"]["ranking"]["initial_retrieval"]
    assert ranking == {"recall_at_5": 1.0, "mrr_at_10": 0.75, "ndcg_at_10": 0.8}
    assert "precision_at_5" not in ranking
    assert profile["arms"]["hybrid"]["total"] == 8
    assert "cutoff" not in report


def test_unattempted_api_case_is_not_counted_as_failed():
    report = normalize_report(
        {
            "mode": "heldout",
            "runs": [
                {
                    "id": "q",
                    "workflow": "direct",
                    "status": "not_attempted",
                    "requests": [],
                }
            ],
        }
    )
    direct = report["profiles"][0]["arms"]["direct"]
    assert (
        direct["failed"] == 0
        and direct["unattempted"] == 1
        and direct["attempted"] == 0
    )


def test_malformed_subscription_shapes_are_validation_errors():
    for payload in [
        {"schema": "subscription-review/v1", "profiles": [None], "cases": []},
        {
            "schema": "subscription-review/v1",
            "profiles": [{"arms": {"direct": None}}],
            "cases": [],
        },
        {
            "schema": "subscription-review/v1",
            "profiles": [{"arms": {}}],
            "cases": [None],
        },
    ]:
        with pytest.raises(ValueError):
            normalize_report(payload)


def test_rejected_attempt_ranked_evidence_and_explicit_metadata_are_preserved():
    report = normalize_report(
        {
            "schema": "subscription-review/v1",
            "run_id": "r",
            "stage": "final_evidence",
            "cutoff": 5,
            "profiles": [
                {
                    "profile": "p",
                    "model": "m",
                    "effort": "high",
                    "stage": "final_evidence",
                    "arms": {
                        "mcp": {
                            "cutoff": 5,
                            "ranking": {
                                "final_evidence": {
                                    "ndcg_at_10": {"value": 0.5, "contributors": ["q"]}
                                }
                            },
                        }
                    },
                }
            ],
            "cases": [
                {
                    "question_id": "q",
                    "runs": {
                        "p-mcp": {
                            "status": "answer_validation_failed",
                            "usage": {"input_tokens": 42},
                            "estimated_api_equivalent_usd": 0.2,
                            "result": {
                                "answer": {
                                    "validation_failure": "quote is not exact canonical text",
                                    "raw_selection": {
                                        "answer_status": "answered",
                                        "claims": [
                                            {
                                                "text": "attempt",
                                                "kind": "claim",
                                                "citation_ids": ["S1"],
                                                "secret": "hidden",
                                            }
                                        ],
                                        "credentials": "hidden",
                                    },
                                },
                                "issued": {
                                    "S1": {
                                        "reference": "ref",
                                        "text": "Original exact passage.",
                                        "source_title": "Title",
                                        "timestamp_url": "https://example.com?t=5",
                                        "document_id": "doc",
                                    }
                                },
                                "retrieval_payloads": [
                                    {
                                        "hits": [
                                            {
                                                "rank": 1,
                                                "score": 0.9,
                                                "document_id": "doc",
                                                "title": "Title",
                                                "canonical_url": "https://example.com",
                                                "references": ["ref"],
                                                "secret": "hidden",
                                            }
                                        ],
                                        "credentials": "hidden",
                                    }
                                ],
                            },
                        }
                    },
                }
            ],
        }
    )
    profile = report["profiles"][0]
    case = profile["cases"][0]
    assert (report["stage"], report["cutoff"], profile["effort"]) == (
        "final_evidence",
        5,
        "high",
    )
    assert profile["arms"]["mcp"]["ranking"]["final_evidence"]["ndcg_at_10"][
        "contributors"
    ] == ["q"]
    assert (
        case["validation_failure"]
        == case["error"]
        == "quote is not exact canonical text"
    )
    assert case["attempted_output"]["status"] == "rejected"
    assert case["attempted_output"]["claims"] == [
        {"text": "attempt", "kind": "claim", "citation_ids": ["S1"]}
    ]
    assert case["evidence"][0]["text"] == "Original exact passage."
    assert case["trace"]["retrieval_payloads"][0]["hits"][0]["rank"] == 1
    assert "credentials" not in case["trace"]["retrieval_payloads"][0]
    assert "secret" not in case["trace"]["retrieval_payloads"][0]["hits"][0]
    assert (
        case["usage"]["input_tokens"] == 42
        and case["cost"]["api_equivalent_usd"] == 0.2
    )


def test_api_preserves_explicit_identity_and_per_case_cost_without_inference():
    report = normalize_report(
        {
            "mode": "heldout",
            "run_id": "api",
            "model": "m",
            "effort": "high",
            "stage": "initial_retrieval",
            "cutoff": 10,
            "runs": [
                {
                    "id": "q",
                    "workflow": "direct",
                    "status": "failed",
                    "assessment": {"quality": "Poor"},
                    "requests": [
                        {
                            "usage": {"input_tokens": 10, "output_tokens": 2},
                            "uncached_usd": 0.1,
                            "credentials": "hidden",
                        }
                    ],
                }
            ],
        }
    )
    p = report["profiles"][0]
    assert (report["stage"], report["cutoff"], p["effort"]) == (
        "initial_retrieval",
        10,
        "high",
    )
    assert p["cases"][0]["requests"] == [
        {"usage": {"input_tokens": 10, "output_tokens": 2}, "uncached_usd": 0.1}
    ]
    assert p["cases"][0]["grades"]["quality"] == "Poor"
    assert (
        "contributors" not in p["arms"]["direct"] and report.get("dataset_hash") is None
    )


def test_subscription_preserves_explicit_cost_basis_and_completeness():
    report = normalize_report(
        {
            "schema": "subscription-review/v1",
            "run_id": "costs",
            "profiles": [
                {
                    "profile": "p",
                    "arms": {
                        "direct": {
                            "cost": {
                                "api_equivalent_usd": 0.4,
                                "cost_basis": "recorded requests",
                                "includes_failed": True,
                                "secret": "hidden",
                            },
                            "usage_complete": True,
                        },
                        "mcp": {"estimated_api_equivalent_usd": 0.3},
                    },
                }
            ],
            "cases": [
                {
                    "question_id": "q",
                    "runs": {
                        "p-direct": {
                            "status": "completed",
                            "cost_basis": "recorded requests",
                            "includes_failed": True,
                            "usage_complete": True,
                            "cost": {"api_equivalent_usd": 0.1, "secret": "hidden"},
                        }
                    },
                }
            ],
        }
    )
    profile = report["profiles"][0]
    direct = profile["arms"]["direct"]
    assert direct["cost"] == {
        "api_equivalent_usd": 0.4,
        "cost_basis": "recorded requests",
        "includes_failed": True,
        "usage_complete": True,
    }
    assert direct["usage_complete"] is True
    assert profile["arms"]["mcp"]["cost"] == {"api_equivalent_usd": 0.3}
    case = profile["cases"][0]
    assert case["cost"] == {
        "api_equivalent_usd": 0.1,
        "cost_basis": "recorded requests",
        "includes_failed": True,
        "usage_complete": True,
    }
    assert case["cost_basis"] == "recorded requests" and case["includes_failed"] is True


def test_quality_distribution_survives_normalization_and_unknown_fields_do_not():
    report = normalize_report(
        {
            "run_id": "r",
            "profiles": [
                {
                    "id": "p",
                    "arms": {
                        "direct": {
                            "grades": {
                                "answer_quality": {
                                    "good": 2,
                                    "partial": 1,
                                    "poor": 0,
                                    "secret": "hidden",
                                }
                            }
                        },
                        "mcp": {
                            "grades": {
                                "answer_quality": {
                                    "distribution": {
                                        "Good": 3,
                                        "Partial": 0,
                                        "Poor": 1,
                                        "secret": "hidden",
                                    }
                                }
                            }
                        },
                    },
                }
            ],
        }
    )
    arms = report["profiles"][0]["arms"]
    assert arms["direct"]["grades"]["answer_quality"] == {
        "good": 2,
        "partial": 1,
        "poor": 0,
    }
    assert arms["mcp"]["grades"]["answer_quality"] == {
        "distribution": {"Good": 3, "Partial": 0, "Poor": 1}
    }


def test_public_demo_preserves_explicit_mock_identity_and_metrics():
    import json
    from pathlib import Path

    fixture = Path(__file__).resolve().parents[1] / "docs/examples/evaluation-demo.json"
    report = normalize_report(json.loads(fixture.read_text()))
    assert report["source_type"] == "demo"
    assert "mocked" in report["title"]
    assert report["profiles"][0]["cases"] == []
    for arm in report["profiles"][0]["arms"].values():
        assert arm["ranking"]["initial_retrieval"]["auc_contract"]
        assert isinstance(arm["ranking"]["initial_retrieval"]["auc"], float)
        quality = arm["grades"]["answer_quality"]
        assert quality["good"] + quality["partial"] + quality["poor"] == arm["total"]
