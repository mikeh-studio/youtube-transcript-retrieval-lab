import React, { useEffect, useRef, useState } from "react";
import { apiRequest, formatRange, safeExternalUrl } from "./lab.jsx";
import { SEARCH_SCOPE_HANDOFF_KEY } from "./library.jsx";
import { readPreferences } from "./settings.jsx";

function initialScope() {
  try {
    const value = JSON.parse(
      sessionStorage.getItem(SEARCH_SCOPE_HANDOFF_KEY) || "null",
    );
    return value?.mode === "videos" && Array.isArray(value.videoIds)
      ? value.videoIds.map(String)
      : [];
  } catch (_) {
    return [];
  }
}
const QUERY_STORAGE_KEY = "yt_rag_workspace_find_query";
function initialQuery() {
  try {
    return sessionStorage.getItem(QUERY_STORAGE_KEY) || "";
  } catch (_) {
    return "";
  }
}
function key(row) {
  return `${row.video_id}:${row.chunk_index}`;
}
function ref(row) {
  return { video_id: row.video_id, chunk_index: row.chunk_index };
}

export function FindWorkspace() {
  const [videos, setVideos] = useState([]);
  const [scope, setScope] = useState(initialScope);
  const [query, setQuery] = useState(initialQuery);
  const [picker, setPicker] = useState(false);
  const [filter, setFilter] = useState("");
  const [run, setRun] = useState({
    mode: "hybrid",
    profile: "baseline_rrf",
    reranker: "none",
    k: 5,
  });
  const [rows, setRows] = useState([]);
  const [selected, setSelected] = useState([]);
  const [snapshot, setSnapshot] = useState(null);
  const [contexts, setContexts] = useState({});
  const [expandedContexts, setExpandedContexts] = useState({});
  const [estimate, setEstimate] = useState(null);
  const [answer, setAnswer] = useState(null);
  const [ack, setAck] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [active, setActive] = useState("");
  const revision = useRef(0);
  const estimateRef = useRef(null);
  const answerRef = useRef(null);
  const prefs = readPreferences();
  useEffect(() => {
    (answer
      ? answerRef.current
      : estimate
        ? estimateRef.current
        : null
    )?.scrollIntoView({ block: "nearest" });
  }, [estimate, answer]);
  useEffect(() => {
    let live = true;
    apiRequest("/v1/videos")
      .then((p) => {
        if (live) setVideos(Array.isArray(p?.videos) ? p.videos : []);
      })
      .catch(() => {});
    return () => {
      live = false;
      revision.current += 1;
    };
  }, []);
  function invalidate(clearRows = true) {
    revision.current += 1;
    setSnapshot(null);
    setEstimate(null);
    setAnswer(null);
    setAck(false);
    setError("");
    setLoading(false);
    if (clearRows) {
      setRows([]);
      setSelected([]);
      setContexts({});
      setExpandedContexts({});
    }
  }
  useEffect(() => {
    function preferencesChanged(event) {
      if (event.key === "youtube-rag-workspace-preferences-v1") {
        revision.current += 1;
        setLoading(false);
        setEstimate(null);
        setAnswer(null);
        setAck(false);
      }
    }
    window.addEventListener("storage", preferencesChanged);
    return () => window.removeEventListener("storage", preferencesChanged);
  }, []);
  function changeScope(next) {
    invalidate();
    setScope(next);
    try {
      sessionStorage.setItem(
        SEARCH_SCOPE_HANDOFF_KEY,
        JSON.stringify({ mode: "videos", videoIds: next, at: Date.now() }),
      );
    } catch (_) {}
  }
  async function search(e) {
    e.preventDefault();
    if (!query.trim()) return;
    invalidate();
    const rev = revision.current;
    setLoading(true);
    try {
      const body = {
        query: query.trim(),
        video_ids: scope.length ? scope : undefined,
        retrieval_mode: run.mode,
        retrieval_profile: run.mode === "hybrid" ? run.profile : undefined,
        reranker: run.reranker,
        k: Number(run.k),
        feedback_tuning: false,
      };
      const response = await apiRequest("/v1/search", { method: "POST", body });
      if (rev !== revision.current) return;
      const found = Array.isArray(response?.results) ? response.results : [];
      if (response?.retrieval_details?.reranker?.error)
        setError(
          `Search degraded: ${response.retrieval_details.reranker.error}`,
        );
      if (found.length) {
        const snap = await apiRequest("/v1/evidence/snapshots", {
          method: "POST",
          body: {
            query: body.query,
            video_ids: scope.length
              ? scope
              : videos.map((v) => String(v.video_id)),
            config: body,
            passages: found.map(ref),
          },
        });
        if (rev !== revision.current) return;
        setSnapshot(snap);
        setRows(snap.passages || []);
        setSelected((snap.passages || []).map(key));
        setActive(snap.passages?.length ? key(snap.passages[0]) : "");
      }
    } catch (err) {
      if (rev === revision.current) setError(err?.message || String(err));
    } finally {
      if (rev === revision.current) setLoading(false);
    }
  }
  function toggle(row) {
    revision.current += 1;
    setLoading(false);
    setEstimate(null);
    setAnswer(null);
    setAck(false);
    setSelected((prev) =>
      prev.includes(key(row))
        ? prev.filter((x) => x !== key(row))
        : [...prev, key(row)],
    );
  }
  async function context(row) {
    const id = key(row);
    if (expandedContexts[id]) {
      setExpandedContexts((prev) => ({ ...prev, [id]: false }));
      return;
    }
    if (contexts[id]) {
      setExpandedContexts((prev) => ({ ...prev, [id]: true }));
      return;
    }
    const passage = snapshot?.passages?.find((p) => key(p) === id);
    if (!passage) {
      setError("Refresh search to read surrounding transcript.");
      return;
    }
    const rev = revision.current;
    try {
      const data = await apiRequest("/v1/evidence/context", {
        method: "POST",
        body: { snapshot_id: snapshot.snapshot_id, passage_id: passage.id },
      });
      if (rev === revision.current) {
        setContexts((prev) => ({
          ...prev,
          [id]: (data.passages || []).filter((p) => key(p) !== id),
        }));
        setExpandedContexts((prev) => ({ ...prev, [id]: true }));
      }
    } catch (err) {
      if (rev === revision.current) setError(err?.message || String(err));
    }
  }
  async function reviewEstimate() {
    const rev = ++revision.current;
    const prefsVersion = JSON.stringify(readPreferences());
    setLoading(true);
    setError("");
    setEstimate(null);
    setAnswer(null);
    setAck(false);
    try {
      const all = [...rows, ...Object.values(contexts).flatMap((x) => x || [])];
      const chosen = all.filter(
        (r, i) =>
          selected.includes(key(r)) &&
          all.findIndex((v) => key(v) === key(r)) === i,
      );
      const snap = await apiRequest("/v1/evidence/snapshots", {
        method: "POST",
        body: {
          query: query.trim(),
          video_ids: scope.length
            ? scope
            : videos.map((v) => String(v.video_id)),
          config: {
            retrieval_mode: run.mode,
            retrieval_profile: run.profile,
            reranker: run.reranker,
            k: Number(run.k),
          },
          passages: chosen.map(ref),
        },
      });
      if (rev !== revision.current) return;
      const ids = (snap.passages || []).map((p) => p.id);
      const data = await apiRequest("/v1/evidence/estimate", {
        method: "POST",
        body: {
          snapshot_id: snap.snapshot_id,
          passage_ids: ids,
          provider: prefs.provider,
          model: prefs.model || undefined,
          max_output_tokens: Number(prefs.max_output_tokens),
          answer_language:
            prefs.language === "match" ? undefined : prefs.language,
        },
      });
      if (
        rev !== revision.current ||
        prefsVersion !== JSON.stringify(readPreferences())
      )
        return;
      setEstimate({ ...data, snapshot: snap, ids, prefsVersion });
    } catch (err) {
      if (rev === revision.current) setError(err?.message || String(err));
    } finally {
      if (rev === revision.current) setLoading(false);
    }
  }
  async function generate() {
    if (!estimate || (estimate.estimated_cost == null && !ack)) return;
    if (estimate.prefsVersion !== JSON.stringify(readPreferences())) {
      setEstimate(null);
      setAck(false);
      setError("Answer defaults changed. Review the estimate again.");
      return;
    }
    const rev = ++revision.current;
    const prefsVersion = JSON.stringify(readPreferences());
    setLoading(true);
    setError("");
    try {
      const result = await apiRequest("/v1/evidence/answer", {
        method: "POST",
        body: {
          snapshot_id: estimate.snapshot.snapshot_id,
          passage_ids: estimate.ids,
          estimate_id: estimate.estimate_id,
          cost_acknowledged: ack,
          provider: prefs.provider,
          model: prefs.model || undefined,
          max_output_tokens: Number(prefs.max_output_tokens),
          answer_language:
            prefs.language === "match" ? undefined : prefs.language,
        },
      });
      if (
        rev === revision.current &&
        prefsVersion === JSON.stringify(readPreferences())
      )
        setAnswer(result);
    } catch (err) {
      if (rev === revision.current) setError(err?.message || String(err));
    } finally {
      if (rev === revision.current) setLoading(false);
    }
  }
  const title = (row) =>
    row.video_title ||
    videos.find((v) => String(v.video_id) === String(row.video_id))?.title ||
    row.video_id;
  function passage(row, index, neighbor = false) {
    const id = key(row);
    const url = safeExternalUrl(row.url);
    return (
      <article
        className="workspace-passage"
        key={`${neighbor ? "neighbor-" : ""}${id}`}
        id={`passage-${id.replace(/[^\w-]/g, "-")}`}
      >
        <div className="workspace-passage-head">
          <strong>{neighbor ? "Context" : index + 1}</strong>
          <span>
            {url ? (
              <a href={url} target="_blank" rel="noreferrer">
                {formatRange(
                  row.start_seconds ?? row.start,
                  row.end_seconds ?? row.end,
                )}{" "}
                ↗
              </a>
            ) : (
              formatRange(
                row.start_seconds ?? row.start,
                row.end_seconds ?? row.end,
              )
            )}
          </span>
          <label>
            <input
              type="checkbox"
              checked={selected.includes(id)}
              onChange={() => toggle(row)}
            />{" "}
            Include in answer
          </label>
          {url ? (
            <a
              className="workspace-open-video"
              href={url}
              target="_blank"
              rel="noreferrer"
              aria-label={`Open ${title(row)} at ${formatRange(
                row.start_seconds ?? row.start,
                row.end_seconds ?? row.end,
              )}`}
            >
              ▷ Open video at timestamp ↗
            </a>
          ) : null}
        </div>
        <p className="workspace-speaker">
          Speaker not identified · Original transcript
        </p>
        <p className="workspace-transcript">{row.text}</p>
        {!neighbor ? (
          <div>
            <button
              type="button"
              className="workspace-link"
              onClick={() => context(row)}
              aria-expanded={Boolean(expandedContexts[id])}
            >
              ⌄ Read surrounding transcript
            </button>{" "}
            <span className="lab-muted">
              Context is not included until selected.
            </span>
            {expandedContexts[id] ? (
              <div className="workspace-context">
                {contexts[id].length ? (
                  contexts[id].map((r, i) => passage(r, i, true))
                ) : (
                  <p>No surrounding transcript available.</p>
                )}
              </div>
            ) : null}
          </div>
        ) : null}
      </article>
    );
  }
  return (
    <div className="workspace-find">
      <form onSubmit={search} className="workspace-query">
        <div>
          <input
            aria-label="Query"
            placeholder="Search your transcripts"
            value={query}
            onChange={(e) => {
              invalidate();
              setQuery(e.target.value);
              try {
                sessionStorage.setItem(QUERY_STORAGE_KEY, e.target.value);
              } catch (_) {}
            }}
          />
          <button className="btn" disabled={loading || !query.trim()}>
            {loading ? "Searching…" : "Find passages"}
          </button>
        </div>
        <div className="workspace-scope">
          {scope.length ? (
            scope.map((id) => (
              <span className="workspace-chip" key={id}>
                {videos.find((v) => String(v.video_id) === id)?.title || id}
                <button
                  type="button"
                  aria-label={`Remove ${id}`}
                  onClick={() => changeScope(scope.filter((v) => v !== id))}
                >
                  ×
                </button>
              </span>
            ))
          ) : (
            <span className="workspace-chip">All indexed videos</span>
          )}
          <button
            type="button"
            className="workspace-link"
            onClick={() => setPicker(!picker)}
            aria-expanded={picker}
          >
            Change sources
          </button>
          <details>
            <summary>Advanced</summary>
            <label>
              Mode{" "}
              <select
                value={run.mode}
                onChange={(e) => {
                  invalidate();
                  setRun({ ...run, mode: e.target.value });
                }}
              >
                <option value="hybrid">Hybrid</option>
                <option value="dense">Dense</option>
                <option value="lexical">Lexical</option>
              </select>
            </label>
            <label>
              Reranker{" "}
              <select
                value={run.reranker}
                onChange={(e) => {
                  invalidate();
                  setRun({ ...run, reranker: e.target.value });
                }}
              >
                <option value="none">Off</option>
                <option value="cross_encoder">Cross-encoder</option>
              </select>
            </label>
            <label>
              Top K{" "}
              <input
                type="number"
                min="1"
                max="12"
                value={run.k}
                onChange={(e) => {
                  invalidate();
                  setRun({ ...run, k: e.target.value });
                }}
              />
            </label>
            <a href="/chunking.html">Index experiments</a>
          </details>
        </div>
        {picker ? (
          <div className="workspace-picker">
            <input
              aria-label="Filter sources"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Search sources"
            />
            <button type="button" onClick={() => changeScope([])}>
              Search all indexed videos
            </button>
            {videos
              .filter((v) =>
                `${v.title || ""} ${v.video_id}`
                  .toLowerCase()
                  .includes(filter.toLowerCase()),
              )
              .map((v) => (
                <label key={v.video_id}>
                  <input
                    type="checkbox"
                    checked={scope.includes(String(v.video_id))}
                    onChange={() =>
                      changeScope(
                        scope.includes(String(v.video_id))
                          ? scope.filter((x) => x !== String(v.video_id))
                          : [...scope, String(v.video_id)],
                      )
                    }
                  />{" "}
                  {v.title || v.video_id}
                </label>
              ))}
          </div>
        ) : null}
      </form>
      {error ? (
        <div className="friendly-error" role="alert">
          {error}
        </div>
      ) : null}
      {rows.length ? (
        <div className="workspace-reader">
          <aside>
            <h2>
              Passages <span>{rows.length}</span>
            </h2>
            {rows.map((r, i) => (
              <React.Fragment key={key(r)}>
                {(i === 0 ||
                  String(rows[i - 1].video_id) !== String(r.video_id)) && (
                  <h3 className="workspace-sidebar-source" title={title(r)}>
                    {title(r)}
                  </h3>
                )}
                <a
                  className={active === key(r) ? "active" : ""}
                  href={`#passage-${key(r).replace(/[^\w-]/g, "-")}`}
                  aria-label={`Passage ${i + 1}, ${title(r)}, ${formatRange(
                    r.start_seconds ?? r.start,
                    r.end_seconds ?? r.end,
                  )}: ${String(r.text || "").slice(0, 100)}`}
                  onClick={(e) => {
                    e.preventDefault();
                    setActive(key(r));
                    document
                      .getElementById(
                        `passage-${key(r).replace(/[^\w-]/g, "-")}`,
                      )
                      ?.scrollIntoView({ behavior: "smooth", block: "start" });
                  }}
                >
                  <strong>
                    {i + 1} ·{" "}
                    {formatRange(
                      r.start_seconds ?? r.start,
                      r.end_seconds ?? r.end,
                    )}
                  </strong>
                  <span>{String(r.text || "").slice(0, 100)}…</span>
                </a>
              </React.Fragment>
            ))}
          </aside>
          <div className="workspace-reading">
            <div
              className="workspace-reading-scroll"
              tabIndex={0}
              aria-label="Original passages"
            >
              <h2>Original passages</h2>
              {rows.map((r, i) => (
                <React.Fragment key={key(r)}>
                  {(i === 0 ||
                    String(rows[i - 1].video_id) !== String(r.video_id)) && (
                    <h3 className="workspace-reading-source">{title(r)}</h3>
                  )}
                  {passage(r, i)}
                </React.Fragment>
              ))}
              {estimate ? (
                <section className="workspace-estimate" ref={estimateRef}>
                  <h2>Review answer</h2>
                  <p>
                    {estimate.ids.length} selected passages · {prefs.provider}{" "}
                    {prefs.model || "configured default"} · up to{" "}
                    {prefs.max_output_tokens} output tokens
                  </p>
                  <p>
                    Estimated input tokens:{" "}
                    {estimate.input_tokens_estimate ?? "unavailable"}
                  </p>
                  {estimate.estimate_basis ? (
                    <p>{estimate.estimate_basis}</p>
                  ) : null}
                  <p>
                    {estimate.estimated_cost == null
                      ? "Estimated cost unavailable."
                      : `Estimated cost: ${estimate.estimated_cost}`}
                  </p>
                  {estimate.estimated_cost == null ? (
                    <label>
                      <input
                        type="checkbox"
                        checked={ack}
                        onChange={(e) => setAck(e.target.checked)}
                      />{" "}
                      I understand the cost is unavailable and generation may
                      incur charges.
                    </label>
                  ) : null}
                  <p>
                    <button
                      className="btn"
                      type="button"
                      disabled={
                        loading || (estimate.estimated_cost == null && !ack)
                      }
                      onClick={generate}
                    >
                      Generate answer
                    </button>
                  </p>
                </section>
              ) : null}
              {answer ? (
                <section className="workspace-answer" ref={answerRef}>
                  <h2>Answer from selected passages</h2>
                  <p data-testid="answer-status" role="status">
                    {answer.status === "answered"
                      ? "Answered from evidence"
                      : answer.status === "insufficient_evidence"
                        ? "Insufficient evidence"
                        : answer.status === "error"
                          ? "Answer unavailable"
                          : "Answer status unavailable"}
                    {answer.confidence
                      ? ` · Confidence: ${answer.confidence}`
                      : ""}
                  </p>
                  <p>
                    {typeof answer.answer === "string"
                      ? answer.answer
                      : JSON.stringify(answer.answer || answer)}
                  </p>
                  {Array.isArray(answer.warnings) && answer.warnings.length ? (
                    <ul>
                      {answer.warnings.map((warning, i) => (
                        <li key={i}>{String(warning)}</li>
                      ))}
                    </ul>
                  ) : null}
                  {answer.status === "error" ? (
                    <button
                      className="btn secondary"
                      type="button"
                      disabled={loading}
                      onClick={generate}
                    >
                      Retry generation
                    </button>
                  ) : null}
                  {Array.isArray(answer.citations)
                    ? answer.citations.map((c, i) => (
                        <p key={i}>
                          {c.url && safeExternalUrl(c.url) ? (
                            <a href={c.url} target="_blank" rel="noreferrer">
                              {c.video_title ||
                                c.video_id ||
                                `Citation ${i + 1}`}{" "}
                              {c.timestamp_range_label || ""}
                            </a>
                          ) : (
                            c.video_title || c.video_id || `Citation ${i + 1}`
                          )}{" "}
                          {c.text || ""}
                        </p>
                      ))
                    : null}
                </section>
              ) : null}
            </div>
            <footer>
              <span>{selected.length} passages selected</span>
              <button
                className="btn"
                disabled={!selected.length || loading}
                type="button"
                onClick={reviewEstimate}
              >
                Review cost and answer →
              </button>
            </footer>
          </div>
        </div>
      ) : (
        <div className="search-empty">
          {loading
            ? "Finding passages…"
            : "Search your indexed transcripts to read original passages."}
        </div>
      )}
    </div>
  );
}
