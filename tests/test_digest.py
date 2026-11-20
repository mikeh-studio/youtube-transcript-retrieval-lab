"""Offline digest integration with synthetic transcripts and a real hybrid index."""

import http.client
import json
import socket
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from evals.digest.runner import main, run_sanity
from evals.digest.scoring import score_digest
from local_preview.label_drafts import LabelDraftService
from local_preview.novelty import (
    NoveltyError,
    check_claims,
    extract_claims,
    prior_scope,
)
from local_preview.transcript_lines import fingerprint_lines, numbered_lines
from multilingual.video_library import VideoLibrary
from tests.test_eval_generator_api import _request, local_api
from tests.test_label_drafts import wait_for_job
from tests.test_local_api_summary_and_retrieval import _make_service


REPEATED = [
    "The finchbell counter resets each dawn.",
    "The mosswheel gate opens at noon.",
    "The quartzwhisk screen catches pebbles.",
]
NEW = ["The fernspindle tray holds paper stars.", "The brackenlatch knob folds flat."]
PRIOR_CONTRADICTION = "The ambercoil tank holds exactly seven cups."
CONTRADICTION = "The ambercoil tank holds exactly nine cups."
RELEASE = "invented_latest"
OLD = ["invented_first", "invented_second", "invented_third"]


def _point(text, index=0, importance=1):
    return {
        "text": text,
        "importance": importance,
        "evidence_lines": [index + 1],
        "start": float(index * 80),
        "end": float(index * 80 + 8),
    }


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setenv("YT_RAG_FORCE_HASH_EMBEDDINGS", "1")
    monkeypatch.delenv("YT_RAG_RERANKER", raising=False)
    library = VideoLibrary(data_dir=tmp_path / "library-data")

    episodes = {
        OLD[0]: [
            REPEATED[0],
            "The spindle moves under a felt cover.",
            PRIOR_CONTRADICTION,
        ],
        OLD[1]: [
            "The rehearsal opens under a blue canopy.",
            REPEATED[1],
            "Plywood fills the rack.",
        ],
        OLD[2]: [
            "A canvas strap steadies the cart.",
            "An old lantern has a loose handle.",
            REPEATED[2],
        ],
        RELEASE: [*REPEATED, *NEW, CONTRADICTION],
    }
    for index, (source_id, texts) in enumerate(episodes.items(), 1):
        source = {
            "platform": "podcast_rss",
            "show": {"name": "Workshop sketches"},
            "episode": {"published_at": f"2026-03-0{index}T09:00:00Z"},
        }
        library.add_transcribed_source(
            source_id,
            [
                dict(text=text, start=i * 80, end=i * 80 + 8)
                for i, text in enumerate(texts)
            ],
            language="en",
            title=source_id,
            source=source,
        )
    library.save()
    library = VideoLibrary(data_dir=library.data_dir, processor=library.processor)
    assert all(record["ingested_at"] for record in library.videos.values())

    service = _make_service()
    service.lock = threading.Lock()
    service.engine = SimpleNamespace(library=library)
    service.runtime_data_dir = tmp_path / "runtime"
    service.ingest_log_path = service.runtime_data_dir / "ingest_jobs.log"
    service.legacy_ingest_log_path = tmp_path / "ingest_jobs.log"
    calls = {"classifier": [], "retrieval": []}
    original_retrieve = service.retrieve

    def retrieve(query, **options):
        result = original_retrieve(query, **options)
        calls["retrieval"].append((query, options, result))
        return result

    def key_areas(source_id, **_options):
        segments = library.videos[source_id]["full_transcript"]["segments"]
        return {
            "source_fingerprint": fingerprint_lines(numbered_lines(segments)),
            "sections": [
                {
                    "key_points": [
                        _point(
                            segment["text"], i, 3 if segment["text"] == NEW[1] else 2
                        )
                        for i, segment in enumerate(segments)
                    ]
                }
            ],
        }

    def classify(**request):
        claims = json.loads(request["user_message"])["claims"]
        calls["classifier"].append(claims)
        decisions = []
        for claim in claims:
            text = claim["text"]
            relation, match = "new", None
            if text in REPEATED:
                relation, match = "covered", text
            if text == CONTRADICTION:
                relation, match = "contradicts", PRIOR_CONTRADICTION
            refs = [
                p["passage_id"]
                for p in claim["prior_passages"]
                if match and match in p["text"]
            ]
            if relation == "contradicts":
                refs.append("P999")
            decisions.append(
                {
                    "claim_id": claim["claim_id"],
                    "relation": relation,
                    "prior": refs,
                    "reason": "Fixture comparison.",
                }
            )
        return {
            "text": json.dumps({"claims": decisions}),
            "usage": {"input_tokens": 120, "output_tokens": 60},
        }

    service.retrieve = retrieve
    service.generate_key_areas = key_areas
    service._llm_text_response = classify
    service.label_drafts = LabelDraftService(
        tmp_path,
        service.runtime_data_dir,
        tmp_path / "evaluation",
        library.videos.get,
        lambda **_: pytest.fail("Unexpected label drafter call"),
        lambda: "2026-03-10T00:00:00Z",
        service._digest_service().get_digest,
    )
    return service, calls


def test_digest_retrieves_planted_passages_and_aggregates(app):
    service, calls = app
    digest = service.build_digest(
        {
            "window": {"start": "2026-03-04T09:00:00Z", "end": "2026-03-05T09:00:00Z"},
        }
    )
    (release,) = digest["releases"]
    assert release["source_id"] == RELEASE and release["show"] == "Workshop sketches"
    assert release["prior_scope_ids"] == OLD
    assert len(calls["classifier"]) == 1
    assert len(calls["classifier"][0]) == len(calls["retrieval"]) == 6
    for query, options, response in calls["retrieval"]:
        assert options == {
            "k": 5,
            "language": "en",
            "retrieval_mode": "hybrid",
            "video_ids": OLD,
            "feedback_tuning": False,
        }
        assert response["details"]["stages"]["dense"]["status"] == "ok"
        assert {row["video_id"] for row in response["results"]} <= set(OLD)
        if query in REPEATED:
            planted = OLD[REPEATED.index(query)]
            assert response["details"]["stages"]["lexical"]["status"] == "ok"
            assert len(response["results"]) == 5
            assert any(
                row["video_id"] == planted and query in row["text"]
                for row in response["results"]
            )
    assert release["covered_count"] == 3
    assert [item["text"] for item in release["highlights"]] == [
        NEW[1],
        NEW[0],
        CONTRADICTION,
    ]
    assert [item["score"] for item in release["highlights"]] == [3.0, 2.0, 2.0]
    (disagreement,) = release["disagreements"]
    assert disagreement["relation"] == "contradicts" and not disagreement["downgraded"]
    passages = {row["passage_id"]: row for row in disagreement["prior_passages"]}
    assert all(
        PRIOR_CONTRADICTION in passages[ref]["text"] for ref in disagreement["prior"]
    )
    assert len(release["claims"]) == 6
    valid = sum(row["valid_prior_count"] for row in release["claims"])
    assert digest["quality"] == {
        "claims_checked": 6,
        "citation_validity": valid / (valid + 1),
        "downgraded_count": 0,
    }
    assert digest["usage"] == {"input_tokens": 120, "output_tokens": 60}


def test_dates_exclude_self_ties_future_treat_undated_as_prior_and_use_ingest_fallback():
    records = {
        "undated": {},
        "stt": {"source": {"stt": {"created_at": "2026-03-03T12:00:00Z"}}},
        "release": {"source": {"published_at": "2026-03-04T09:00:00+09:00"}},
        "earlier": {"ingested_at": "2026-03-03T23:59:59Z"},
        "tied": {"ingested_at": "2026-03-04T00:00:00Z"},
        "later": {
            "source": {"published_at": "2026-03-05"},
            "ingested_at": "2026-03-01",
        },
    }
    assert prior_scope(records, "release") == ["undated", "stt", "earlier"]
    assert prior_scope(records, "undated") == []


def test_digest_recovers_legacy_ingest_time_from_completed_jobs(app):
    service, _ = app
    record = service.engine.library.videos[OLD[0]]
    record.pop("ingested_at")
    record["source"]["episode"]["published_at"] = None
    log = service.legacy_ingest_log_path
    events = [
        {"event": "job.completed", "video_id": OLD[0], "ts": "2026-03-01T09:00:00Z"},
        {"event": "job.created", "video_id": OLD[0], "ts": "2026-03-06T09:00:00Z"},
    ]
    log.write_text("\n".join([*(json.dumps(row) for row in events), "{truncated"]))
    (release,) = service.build_digest({"source_ids": [RELEASE]})["releases"]
    assert release["prior_scope_ids"] == OLD
    assert release["covered_count"] == 3
    assert "ingested_at" not in record


def test_digest_ranks_extension_and_counts_downgrade(app):
    service, _ = app
    store = service._digest_service()
    original = store.llm

    def classify(**request):
        response = original(**request)
        output = json.loads(response["text"])
        claim_ids = {
            claim["text"]: claim["claim_id"]
            for claim in json.loads(request["user_message"])["claims"]
        }
        for decision in output["claims"]:
            if decision["claim_id"] == claim_ids[REPEATED[0]]:
                decision["relation"] = "extends"
            elif decision["claim_id"] == claim_ids[REPEATED[1]]:
                decision["prior"] = ["P999"]
        return {**response, "text": json.dumps(output)}

    store.llm = classify
    digest = service.build_digest({"source_ids": [RELEASE]})
    (release,) = digest["releases"]
    assert release["covered_count"] == 1
    assert digest["quality"]["downgraded_count"] == 1
    assert release["highlights"][-1]["text"] == REPEATED[0]
    assert release["highlights"][-1]["score"] == 1.2
    (downgraded,) = [row for row in release["highlights"] if row["downgraded"]]
    assert (downgraded["text"], downgraded["relation"]) == (REPEATED[1], "new")


def test_extract_claims_prefers_importance_and_falls_back_to_injected_builder():
    duplicate = "The felt cover fits."
    key_areas = {
        "sections": [
            {
                "key_points": [
                    _point(duplicate),
                    _point("The handle is brass.", 1, 2),
                    _point(duplicate, 2, 3),
                ]
            }
        ]
    }
    seen = []
    claims = extract_claims(
        "release", None, 1, key_areas_fn=lambda sid: seen.append(sid) or key_areas
    )
    assert seen == ["release"]
    assert [
        (row["claim_id"], row["text"], row["evidence_lines"]) for row in claims
    ] == [
        ("release:1:3", duplicate, [3]),
    ]
    assert len(extract_claims("release", key_areas, 10)) == 2


def test_translated_variants_stay_scoped_and_citations_are_claim_local():
    claims = extract_claims(
        "release",
        {
            "sections": [
                {
                    "key_points": [
                        _point("The door swings outward."),
                        _point("The hatch uses a brass hinge.", 1),
                    ]
                }
            ]
        },
        2,
    )
    requests = []

    def translate(queries, language, targets):
        assert list(queries) == [row["claim_id"] for row in claims]
        assert language == "en" and targets == ["ja"]
        return {claim_id: {"ja": "扉は外側に開く。"} for claim_id in queries}

    def retrieve(query, **options):
        requests.append((query, options))
        rows = [
            {
                "video_id": "english",
                "text": "The door swings outward.",
                "start": 0,
                "end": 4,
            },
            {"video_id": "japanese", "text": "扉は外側に開く。", "start": 7, "end": 9},
            {"video_id": "future", "text": "Out of scope.", "start": 0, "end": 2},
        ]
        if options["language"] == "ja":
            return {"results": rows}
        return {"results": rows if "door" in query else rows[-1:]}

    def classify(**request):
        first, second = json.loads(request["user_message"])["claims"]
        assert [p["passage_id"] for p in first["prior_passages"]] == ["P1", "P2"]
        assert [p["passage_id"] for p in second["prior_passages"]] == ["P1"]
        return {
            "text": json.dumps(
                {
                    "claims": [
                        {
                            **first,
                            "relation": "extends",
                            "prior": ["P2"],
                            "reason": "It adds detail.",
                        },
                        {
                            **second,
                            "relation": "contradicts",
                            "prior": ["P2"],
                            "reason": "Unresolved.",
                        },
                    ]
                }
            )
        }

    result = check_claims(
        claims,
        ["english", "japanese"],
        retrieve,
        translate,
        classify,
        scope_languages={"english": "en", "japanese": "ja", "future": "fr"},
    )
    first, second = result["claims"]
    assert first["relation"] == "extends" and first["prior"] == ["P2"]
    assert first["prior_passages"][1]["retrieval_matches"] == [
        {"language": "en", "rank": 2},
        {"language": "ja", "rank": 2},
    ]
    assert (
        second["relation"] == "new" and second["downgraded"] and second["prior"] == []
    )
    assert result["citation_count"] == 2 and result["valid_citation_count"] == 1
    assert [options["video_ids"] for _, options in requests] == [
        ["english", "japanese"],
        ["japanese"],
    ] * 2


@pytest.mark.parametrize("relation", ["covered", "extends", "contradicts"])
def test_evidence_required_for_every_prior_relation(relation):
    claim = {"claim_id": "test", "text": "The hinge is brass."}
    response = {
        "claims": [
            {"claim_id": "test", "relation": relation, "prior": ["P31"], "reason": ""}
        ]
    }
    result = check_claims(
        [claim],
        [],
        lambda *a, **k: pytest.fail("empty retrieval scope"),
        None,
        lambda **_: {"text": json.dumps(response)},
    )["claims"][0]
    assert result["relation"] == "new" and result["proposed_relation"] == relation
    assert result["downgraded"] and not result["prior_passages"]


def test_partial_classifier_result_does_not_publish(app):
    service, _ = app
    service._digest_service().llm = lambda **_: {"text": '{"claims":[]}'}
    with pytest.raises(NoveltyError, match="did not decide"):
        service.build_digest({"source_ids": [RELEASE]})
    assert service._digest_service().list_digests() == []


def _json_request(method, path, body=None, **options):
    headers = ["Host: 127.0.0.1:8000", "Connection: close"]
    if method == "POST" and options.get("authenticated", True):
        status, session, payload = _request("GET", "/browser-session")
        assert status == 200
        cookie = session["Set-Cookie"].split(";", 1)[0]
        token = json.loads(payload)["csrf_token"]
        headers.extend(
            [
                "Origin: http://127.0.0.1:8000",
                f"Cookie: {cookie}",
                f"X-CSRF-Token: {token}",
            ]
        )
    encoded = json.dumps(body).encode() if body is not None else b""
    if encoded:
        headers.extend(
            ["Content-Type: application/json", f"Content-Length: {len(encoded)}"]
        )
    raw = (
        "\r\n".join([f"{method} {path} HTTP/1.1", *headers, "", ""]).encode() + encoded
    )
    client, server = socket.socketpair()

    def handle():
        with server:
            local_api.Handler(
                server,
                ("127.0.0.1", 50000),
                SimpleNamespace(
                    server_address=("127.0.0.1", 8000),
                ),
            )

    # Drain larger digest responses while the real handler writes to the socket.
    with client, ThreadPoolExecutor(1) as worker:
        client.settimeout(5)
        handled = worker.submit(handle)
        client.sendall(raw)
        client.shutdown(socket.SHUT_WR)
        with http.client.HTTPResponse(client) as response:
            response.begin()
            reply = response.status, json.load(response)
        handled.result()
        return reply


def test_digest_routes_feedback_and_owner_labels(app, monkeypatch):
    service, calls = app
    monkeypatch.setattr(local_api, "SERVICE", service)
    build = "/v1/digest/whats-new"
    body = {"source_ids": [RELEASE], "language": "en", "provider": "chatgpt"}
    assert _json_request("POST", build, body, authenticated=False)[0] == 403
    assert not calls["classifier"]
    for invalid in (
        {},
        {"source_ids": []},
        {**body, "language": []},
        {**body, "window": {}},
    ):
        assert _json_request("POST", build, invalid)[0] == 400
    assert _json_request("POST", build, {"source_ids": ["missing"]})[0] == 404
    status, data = _json_request("POST", build, body)
    assert status == 201
    digest = data["digest"]
    digest_id = digest["digest_id"]
    path = f"/v1/digest/{digest_id}"
    assert _json_request("GET", path) == (200, {"ok": True, "digest": digest})
    assert _json_request("GET", "/v1/digest/%2e%2e%2fother")[0] == 404
    assert _json_request("GET", "/v1/digests")[1]["digests"][0]["reviewable_count"] == 3
    directory = service._digest_service().directory
    assert ((directory / f"{digest_id}.json").stat().st_mode & 0o777) == 0o600

    feedback = {
        "item_id": digest["releases"][0]["highlights"][0]["claim_id"],
        "rating": "up",
        "note": "Useful comparison.",
    }
    assert (
        _json_request("POST", f"{path}/feedback", feedback, authenticated=False)[0]
        == 403
    )
    assert (
        _json_request("POST", f"{path}/feedback", {**feedback, "item_id": "missing"})[0]
        == 400
    )
    assert (
        _json_request("POST", f"{path}/feedback", {**feedback, "rating": "maybe"})[0]
        == 400
    )
    assert _json_request("POST", f"{path}/feedback", feedback)[0] == 201

    with ThreadPoolExecutor(4) as workers:
        list(
            workers.map(
                lambda i: service._digest_service().save_feedback(
                    digest_id, {**feedback, "note": str(i)}
                ),
                range(8),
            )
        )
    feedback_path = service._digest_service().feedback_path
    assert (feedback_path.stat().st_mode & 0o777) == 0o600
    records = [json.loads(line) for line in feedback_path.read_text().splitlines()]
    assert {row["note"] for row in records} == {
        feedback["note"],
        *(str(i) for i in range(8)),
    }

    tasks = _json_request("GET", "/v1/labels/tasks")[1]["tasks"]
    assert (
        next(task for task in tasks if task["task"] == "digest_novelty")["input"]
        == "digest"
    )
    status, data = _json_request(
        "POST", "/v1/labels/jobs", {"task": "digest_novelty", "digest_id": digest_id}
    )
    assert status == 202
    completed = wait_for_job(service.label_drafts, data["job"]["job_id"])
    assert completed["status"] == "completed", completed
    draft_path = f"/v1/labels/drafts/{completed['draft_id']}"
    draft = _json_request("GET", draft_path)[1]["draft"]
    assert {item["text"] for item in draft["items"]} == {*NEW, CONTRADICTION}
    contradiction = next(
        item for item in draft["items"] if item["relation"] == "contradicts"
    )
    assert any(
        PRIOR_CONTRADICTION in row["text"] for row in contradiction["prior_passages"]
    )
    context = _json_request("GET", f"{draft_path}/context?item={contradiction['id']}")[
        1
    ]["context"]
    assert context["lines"][-1]["text"] == CONTRADICTION
    assert (
        _json_request(
            "POST",
            f"{draft_path}/review",
            {
                "decisions": [
                    {"id": contradiction["id"], "decision": "edited", "edits": {}},
                ]
            },
        )[0]
        == 400
    )
    assert (
        _json_request(
            "POST",
            f"{draft_path}/review",
            {
                "decisions": [
                    {"id": item["id"], "decision": "rejected"}
                    for item in draft["items"]
                ]
            },
        )[0]
        == 200
    )
    status, data = _json_request("POST", f"{draft_path}/finalize", {})
    assert status == 200
    dataset = data["dataset"]
    assert dataset["row_count"] == dataset["review_counts"]["rejected"] == 3
    assert len(calls["classifier"]) == 1
    rows = (
        service.label_drafts.labels_dir
        / "digest_novelty"
        / f"{dataset['dataset_id']}.jsonl"
    )
    labels = [json.loads(line) for line in rows.read_text().splitlines()]
    scores = score_digest(
        digest,
        labels,
        [{"choice": "digest"}, {"choice": "baseline"}, {"choice": "tie"}],
    )
    assert scores["novelty_precision"] == scores["contradiction_precision"] == 0.0
    assert scores["prior_evidence_validity"] == digest["quality"]["citation_validity"]
    assert scores["preference_win_rate"] == 0.5
    labels[0]["review_decision"] = "approved"
    assert score_digest(judgments=iter(labels))["novelty_precision"] == 0.5
    assert score_digest()["preference_win_rate"] is None
    with pytest.raises(ValueError, match="Pairwise choice"):
        score_digest(preferences=[{"choice": "unknown"}])


@pytest.mark.parametrize(
    "mode, expected", [("self_novelty", "covered"), ("empty_library", "new")]
)
def test_digest_runner_sanity(mode, expected):
    assert run_sanity(mode)["relation"] == expected


def test_digest_runner_loads_pairwise_jsonl(tmp_path, capsys):
    choices = tmp_path / "choices.jsonl"
    choices.write_text('{"choice":"digest"}\n\n{"choice":"tie"}\n')
    main(["--preferences", str(choices)])
    result = json.loads(capsys.readouterr().out)
    assert result["preference_win_rate"] == 0.75
    assert result["counts"]["preference_votes"] == 2
