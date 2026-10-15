import React, { useEffect, useMemo, useState } from "react";
import {
  apiRequest,
  errorInfo,
  evidenceProvenanceLabel,
  formatRange,
  markFeedbackRevision,
  optionalNumber,
  safeExternalUrl,
} from "./lab.jsx";
import { SEARCH_SCOPE_HANDOFF_KEY, loadCatalogItems } from "./library.jsx";

const EVAL_STORAGE_KEY = "youtube-rag-eval-v1";
const EVAL_SCHEMA_VERSION = 1;
const SAVED_QUERY_SET_ID = "qs-search-workbench";
const SAVED_QUERY_SET_NAME = "Saved from Search";

const DEFAULT_RUNS = {
  A: { strategy: "retrieval", mode: "hybrid", profile: "baseline_rrf", reranker: "none", k: 5 },
  B: { strategy: "agentic", mode: "hybrid", profile: "baseline_rrf", reranker: "cross_encoder", k: 5 },
};

const RUN_ROLES = { A: "baseline", B: "candidate" };

const STEP_LABELS = {
  initial: "Initial retrieval",
  rewrite_query: "Rewrote the query",
  switch_mode: "Switched search tool",
  broaden_top_k: "Searched more broadly",
  read_context: "Read nearby transcript",
  multilingual_fusion: "Fused language results",
};

const TOOL_LABELS = {
  semantic_search: "E5 + FAISS",
  keyword_search: "BM25 (Japanese)",
  read_context: "read_context",
  multilingual_fusion: "multilingual fusion",
};

const REASON_LABELS = {
  multi_chunk_support: "multiple supporting chunks",
  strong_single_chunk: "one strong chunk",
  no_results: "no matching evidence",
  thin_support: "evidence too thin",
  single_weak_chunk: "one weak chunk",
  mixed_signals: "signals disagree",
  multilingual_fusion: "combined language results",
};

// Matches feedback_chunk_key() in local_preview/feedback_keys.py.
export function chunkKey(row) {
  const videoId = String(row?.video_id || "").trim();
  if (!videoId) {
    return "";
  }
  const chunkIndex = row?.chunk_index;
  if (chunkIndex !== undefined && chunkIndex !== null && chunkIndex !== "" && Number.isFinite(Number(chunkIndex))) {
    return `${videoId}:${Math.trunc(Number(chunkIndex))}`;
  }
  const start = Math.trunc(Math.max(0, Number(row?.start_seconds ?? row?.start ?? 0)) * 1000);
  const end = Math.trunc(Math.max(0, Number(row?.end_seconds ?? row?.end ?? 0)) * 1000);
  return `${videoId}:${start}:${end}`;
}

// Binary-relevance Precision@K and nDCG@K from the labels known for this query.
export function rankingMetrics(rows, labels, k) {
  const relevant = new Set(
    Object.entries(labels).filter(([, entry]) => entry.label === "relevant").map(([key]) => key),
  );
  const labeledAny = Object.values(labels).some((entry) => entry.label);
  if (!labeledAny) {
    return { precision: null, ndcg: null };
  }
  const top = rows.slice(0, k);
  let hits = 0;
  let dcg = 0;
  top.forEach((row, index) => {
    if (relevant.has(chunkKey(row))) {
      hits += 1;
      dcg += 1 / Math.log2(index + 2);
    }
  });
  let idcg = 0;
  for (let index = 0; index < Math.min(relevant.size, k); index += 1) {
    idcg += 1 / Math.log2(index + 2);
  }
  return { precision: hits / k, ndcg: idcg > 0 ? dcg / idcg : 0 };
}

function runRequestBody(run, query, language, videoIds) {
  return {
    query,
    k: Number(run.k) || 5,
    retrieval_mode: run.strategy === "agentic" ? undefined : run.mode,
    retrieval_profile: run.strategy !== "agentic" && run.mode === "hybrid" ? run.profile : undefined,
    agentic: run.strategy === "agentic",
    reranker: run.reranker,
    language: language || undefined,
    video_ids: videoIds || undefined,
    // Labels must not reshape the rankings being compared.
    feedback_tuning: false,
  };
}

function describeRun(run) {
  const parts = [run.strategy === "agentic" ? "Agentic" : run.mode[0].toUpperCase() + run.mode.slice(1)];
  if (run.strategy === "agentic") {
    parts.push("automatic tools");
  }
  if (run.strategy !== "agentic" && run.mode === "hybrid") {
    parts.push(run.profile);
  }
  if (run.reranker === "cross_encoder") {
    parts.push("cross-encoder");
  }
  return parts.join(" · ");
}

function rowSignals(row) {
  const signals = [];
  if (optionalNumber(row?.dense_score) !== null) signals.push("dense");
  if (optionalNumber(row?.lexical_score) !== null) signals.push("bm25");
  if (optionalNumber(row?.rerank_score) !== null) signals.push("rerank");
  if (Number(row?.context_segment_count) > 0) signals.push("context");
  return signals;
}

function rowScore(row) {
  const score = optionalNumber(row?.rerank_score ?? row?.score);
  return score === null ? "" : score.toFixed(score < 1 ? 4 : 2);
}

function readEvalState() {
  try {
    const parsed = JSON.parse(localStorage.getItem(EVAL_STORAGE_KEY) || "null");
    if (parsed && typeof parsed === "object" && Array.isArray(parsed.query_sets)) {
      return { version: EVAL_SCHEMA_VERSION, runs: [], ...parsed };
    }
  } catch (_) {
    // fall through to a fresh state
  }
  return { version: EVAL_SCHEMA_VERSION, query_sets: [], runs: [] };
}

// Appends the query to Evaluate's "Saved from Search" query set.
export function saveQueryToEvaluate(query, language, relevantCount) {
  const state = readEvalState();
  let querySet = state.query_sets.find((row) => row.id === SAVED_QUERY_SET_ID);
  if (!querySet) {
    querySet = {
      id: SAVED_QUERY_SET_ID,
      name: SAVED_QUERY_SET_NAME,
      language: language || "mixed",
      created_at: new Date().toISOString(),
      queries: [],
    };
    state.query_sets.push(querySet);
  }
  if (querySet.language !== "mixed" && language && querySet.language !== language) {
    querySet.language = "mixed";
  }
  const normalized = query.trim().toLowerCase().replace(/\s+/g, " ");
  if (querySet.queries.some((row) => String(row.text || "").trim().toLowerCase().replace(/\s+/g, " ") === normalized)) {
    return "exists";
  }
  querySet.queries.push({
    id: `q-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`,
    text: query.trim(),
    type: "factual",
    expected_relevant_min: Math.max(1, relevantCount),
    notes: "Saved from Search",
  });
  localStorage.setItem(EVAL_STORAGE_KEY, JSON.stringify(state));
  return "saved";
}

function RunSetup({ id, run, onChange }) {
  function update(field, value) {
    onChange({ ...run, [field]: value });
  }
  return (
    <div className="lab-run" data-run={id}>
      <div className="lab-run-head">
        <span className={`lab-run-tag run-${id.toLowerCase()}`}>Run {id}</span>
        <span className="lab-muted">{RUN_ROLES[id]}</span>
      </div>
      <label>
        <span>Strategy</span>
        <select value={run.strategy} onChange={(event) => update("strategy", event.target.value)} aria-label={`Run ${id} strategy`}>
          <option value="retrieval">Single pass</option>
          <option value="agentic">Agentic</option>
        </select>
      </label>
      {run.strategy === "agentic" ? (
        <p className="lab-hint">Tools are chosen automatically from the query: dense or BM25 search, then nearby context.</p>
      ) : <label>
        <span>Mode</span>
        <select value={run.mode} onChange={(event) => update("mode", event.target.value)} aria-label={`Run ${id} mode`}>
          <option value="hybrid">Hybrid</option>
          <option value="dense">Dense (E5)</option>
          <option value="lexical">Lexical (BM25)</option>
        </select>
      </label>}
      {run.strategy !== "agentic" && run.mode === "hybrid" ? (
        <label>
          <span>Hybrid profile</span>
          <select value={run.profile} onChange={(event) => update("profile", event.target.value)} aria-label={`Run ${id} hybrid profile`}>
            <option value="baseline_rrf">baseline_rrf</option>
            <option value="optimized_v1">optimized_v1</option>
          </select>
        </label>
      ) : null}
      <label>
        <span>Reranker</span>
        <select value={run.reranker} onChange={(event) => update("reranker", event.target.value)} aria-label={`Run ${id} reranker`}>
          <option value="none">Off</option>
          <option value="cross_encoder">Cross-encoder</option>
        </select>
      </label>
      <label>
        <span>Top K</span>
        <input
          type="number"
          min="1"
          max="12"
          value={run.k}
          onChange={(event) => update("k", Math.max(1, Math.min(12, Number(event.target.value) || 1)))}
          aria-label={`Run ${id} top K`}
        />
      </label>
    </div>
  );
}

function ResultCard({ row, rank, delta, label, onLabel, disabled }) {
  const url = safeExternalUrl(row?.url);
  const provenance = evidenceProvenanceLabel(row);
  const text = String(row?.text || "").replace(/\s+/g, " ").trim();
  return (
    <article className={`lab-hit ${label?.label === "relevant" ? "relevant" : ""} ${label?.label === "not_relevant" ? "not-relevant" : ""}`}>
      <div className="lab-hit-top">
        <span className="lab-rank">{rank}</span>
        <span className="lab-hit-title">{row?.video_title || row?.video_id}</span>
        {delta ? <span className={`lab-delta ${delta.tone}`} title={delta.title}>{delta.text}</span> : null}
        <span className="lab-score">{rowScore(row)}</span>
      </div>
      <div className="lab-hit-meta">
        {url ? (
          <a href={url} target="_blank" rel="noopener noreferrer">{formatRange(row?.start_seconds ?? row?.start, row?.end_seconds ?? row?.end)}</a>
        ) : (
          <span>{formatRange(row?.start_seconds ?? row?.start, row?.end_seconds ?? row?.end)}</span>
        )}
        {provenance ? <span>{provenance}</span> : null}
        {rowSignals(row).map((signal) => <span className="lab-signal" key={signal}>{signal}</span>)}
      </div>
      <p className="lab-snippet">{text.length > 220 ? `${text.slice(0, 220)}…` : text}</p>
      <div className="lab-label-row" role="group" aria-label={`Label result ${rank}`}>
        <button
          type="button"
          className={label?.label === "relevant" ? "on-relevant" : ""}
          aria-pressed={label?.label === "relevant"}
          disabled={disabled || label?.pending}
          onClick={() => onLabel(row, "relevant")}
        >
          Relevant
        </button>
        <button
          type="button"
          className={label?.label === "not_relevant" ? "on-not-relevant" : ""}
          aria-pressed={label?.label === "not_relevant"}
          disabled={disabled || label?.pending}
          onClick={() => onLabel(row, "not_relevant")}
        >
          Not relevant
        </button>
        {label?.error ? <span className="lab-label-error" role="alert">{label.error}</span> : null}
      </div>
    </article>
  );
}

function RunTrace({ id, run, outcome }) {
  if (!outcome?.response) {
    return null;
  }
  const details = outcome.response.retrieval_details || {};
  const trace = details.agentic_retrieval;
  const attempts = Array.isArray(trace?.attempts) ? trace.attempts : [];
  const reranker = details.reranker;
  return (
    <div className="lab-trace" data-run={id}>
      <div className="lab-trace-head">
        <span className={`lab-run-tag run-${id.toLowerCase()}`}>Run {id}</span>
        <span>{describeRun(run)}</span>
        <span className="lab-muted">{Math.round(outcome.ms)} ms round trip</span>
      </div>
      <ol className="lab-steps">
        {attempts.length ? attempts.map((attempt, index) => (
          <li key={`${attempt.attempt}-${index}`}>
            <div>
              <strong>{STEP_LABELS[attempt.strategy] || String(attempt.strategy || "Step").replaceAll("_", " ")}</strong>{" "}
              <span className="lab-signal">{TOOL_LABELS[attempt.tool] || attempt.tool || attempt.retrieval_mode}</span>{" "}
              <span className={attempt.sufficient ? "lab-ok" : "lab-weak"}>
                {attempt.sufficient ? "sufficient" : REASON_LABELS[attempt.reason_code] || "insufficient"}
              </span>
              <div className="lab-muted">
                “{attempt.query}” · {attempt.result_count} result{Number(attempt.result_count) === 1 ? "" : "s"}
                {attempt.tool !== "read_context" && attempt.k ? ` · top ${attempt.k}` : ""}
              </div>
            </div>
          </li>
        )) : (
          <li>
            <div>
              <strong>Single pass</strong> <span className="lab-signal">{outcome.response.retrieval_mode}</span>
              <div className="lab-muted">{outcome.response.result_count} results</div>
            </div>
          </li>
        )}
        {run.reranker === "cross_encoder" && reranker && typeof reranker === "object" ? (
          <li>
            <div>
              <strong>Cross-encoder rerank</strong>{" "}
              <span className={reranker.applied ? "lab-ok" : "lab-weak"}>{reranker.applied ? "applied" : "skipped"}</span>
              <div className="lab-muted">{reranker.error || reranker.model || ""}</div>
            </div>
          </li>
        ) : null}
      </ol>
      {trace ? <p className="lab-muted">Stopped: {String(trace.stopped_reason || "completed").replaceAll("_", " ")}</p> : null}
    </div>
  );
}

const HANDOFF_MAX_AGE_MS = 60_000;

// The Library hands a video scope to Search through sessionStorage. It is read
// on every mount (the route scene can remount) and cleared once a search runs.
function readScopeHandoff() {
  try {
    const handoff = JSON.parse(sessionStorage.getItem(SEARCH_SCOPE_HANDOFF_KEY) || "null");
    if (
      handoff?.mode === "videos"
      && Array.isArray(handoff.videoIds)
      && handoff.videoIds.length
      && Date.now() - Number(handoff.at || 0) < HANDOFF_MAX_AGE_MS
    ) {
      return handoff.videoIds.map(String);
    }
  } catch (_) {
    // ignore malformed handoff
  }
  return null;
}

function clearScopeHandoff() {
  try {
    sessionStorage.removeItem(SEARCH_SCOPE_HANDOFF_KEY);
  } catch (_) {
    // best effort only
  }
}

export function SearchWorkbench({ renderAnswer }) {
  const [handoffVideoIds] = useState(readScopeHandoff);
  const [videos, setVideos] = useState([]);
  const [query, setQuery] = useState("");
  const [language, setLanguage] = useState("");
  const [scopeMode, setScopeMode] = useState(handoffVideoIds ? "videos" : "all");
  const [selectedVideoIds, setSelectedVideoIds] = useState(handoffVideoIds || []);
  const [catalogTerms, setCatalogTerms] = useState(null);
  const [catalogTerm, setCatalogTerm] = useState("");
  const [catalogQualification, setCatalogQualification] = useState("eligible");
  const [runs, setRuns] = useState(DEFAULT_RUNS);
  const [view, setView] = useState("compare");
  const [outcomes, setOutcomes] = useState({ A: null, B: null });
  const [searchedQuery, setSearchedQuery] = useState("");
  const [scopeSummary, setScopeSummary] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [labels, setLabels] = useState({});
  const [saveMessage, setSaveMessage] = useState("");
  const labelSavePending = Object.values(labels).some((entry) => entry.pending);

  useEffect(() => {
    apiRequest("/v1/videos")
      .then((payload) => setVideos(Array.isArray(payload?.videos) ? payload.videos : []))
      .catch(() => setVideos([]));
  }, []);

  useEffect(() => {
    if (scopeMode !== "catalog" || catalogTerms !== null) {
      return;
    }
    loadCatalogItems().then((result) => {
      if (!result) {
        setCatalogTerms("unavailable");
        return;
      }
      const terms = new Map();
      result.items.forEach((item) => (item.labels || []).forEach((label) => {
        if (label.key && !terms.has(label.key)) {
          terms.set(label.key, label.en || label.key);
        }
      }));
      setCatalogTerms(Array.from(terms, ([key, name]) => ({ key, name })).sort((a, b) => a.name.localeCompare(b.name)));
    });
  }, [scopeMode, catalogTerms]);

  const indexedIds = useMemo(() => new Set(videos.map((video) => String(video.video_id))), [videos]);

  async function resolveScope() {
    if (scopeMode === "videos") {
      if (!selectedVideoIds.length) {
        throw new Error("Choose at least one video, or search all indexed videos.");
      }
      return { videoIds: selectedVideoIds, summary: `${selectedVideoIds.length} selected video${selectedVideoIds.length === 1 ? "" : "s"}` };
    }
    if (scopeMode === "catalog") {
      const result = await loadCatalogItems({ term: catalogTerm, qualification: catalogQualification });
      if (!result) {
        throw new Error("The catalog service is not connected. Search all indexed videos or choose videos instead.");
      }
      const matches = result.items.map((item) => item.external_id).filter((id) => indexedIds.has(id));
      const filterName = [catalogTerm && (Array.isArray(catalogTerms) ? catalogTerms.find((t) => t.key === catalogTerm)?.name : catalogTerm), catalogQualification]
        .filter(Boolean).join(" · ") || "all catalog items";
      if (!matches.length) {
        throw new Error(`No indexed videos match the catalog filter (${filterName}). ${result.items.length} catalog item${result.items.length === 1 ? "" : "s"} matched, but none are indexed.`);
      }
      return {
        videoIds: matches,
        summary: `${matches.length} indexed of ${result.total} catalog item${result.total === 1 ? "" : "s"} (${filterName})${result.truncated ? ", first 500 checked" : ""}`,
      };
    }
    return { videoIds: null, summary: `All ${videos.length} indexed videos` };
  }

  async function loadLabels(text) {
    try {
      const payload = await apiRequest(`/v1/feedback/search-review?${new URLSearchParams({ query: text, limit: "500" })}`);
      const next = {};
      (payload?.reviews || []).forEach((review) => {
        const key = review.chunk_key || chunkKey(review);
        if (key && !next[key]) {
          next[key] = { label: review.label };
        }
      });
      return next;
    } catch (_) {
      return {};
    }
  }

  async function runSearch(event) {
    event.preventDefault();
    const text = query.trim();
    if (!text || loading || labelSavePending) {
      return;
    }
    setLoading(true);
    setError("");
    setSaveMessage("");
    clearScopeHandoff();
    try {
      const scope = await resolveScope();
      const runOne = async (id) => {
        const started = performance.now();
        try {
          const response = await apiRequest("/v1/search", {
            method: "POST",
            body: runRequestBody(runs[id], text, language, scope.videoIds),
          });
          return { response, ms: performance.now() - started, run: runs[id] };
        } catch (runError) {
          return { error: errorInfo(runError, "Search failed.").userMessage, ms: performance.now() - started, run: runs[id] };
        }
      };
      const [a, b, nextLabels] = await Promise.all([runOne("A"), runOne("B"), loadLabels(text)]);
      setOutcomes({ A: a, B: b });
      setLabels(nextLabels);
      setSearchedQuery(text);
      setScopeSummary(scope.summary);
      if (view === "answer") {
        setView("compare");
      }
    } catch (scopeError) {
      setError(String(scopeError?.message || scopeError));
    } finally {
      setLoading(false);
    }
  }

  async function labelRow(row, label, runId) {
    const key = chunkKey(row);
    if (loading || !key || labels[key]?.pending || labels[key]?.label === label) {
      return;
    }
    const previous = labels[key];
    setLabels((prev) => ({ ...prev, [key]: { label, pending: true } }));
    const run = outcomes[runId]?.run || runs[runId];
    const retrievalMode = outcomes[runId]?.response?.retrieval_mode || run.mode;
    try {
      await apiRequest("/v1/feedback/search-review", {
        method: "POST",
        body: {
          query: searchedQuery,
          retrieval_mode: retrievalMode,
          label,
          video_id: row.video_id,
          chunk_index: row.chunk_index ?? null,
          start: Number((row.start_seconds ?? row.start) || 0),
          end: Number((row.end_seconds ?? row.end ?? row.start) || 0),
          url: row.url || "",
          video_title: row.video_title || "",
          language: row.language || "",
          score: optionalNumber(row.score),
          dense_score: optionalNumber(row.dense_score),
          lexical_score: optionalNumber(row.lexical_score),
          hybrid_score: optionalNumber(row.hybrid_score),
          rank: row.rank ?? null,
          model: retrievalMode,
        },
      });
      setLabels((prev) => ({ ...prev, [key]: { label } }));
      markFeedbackRevision();
    } catch (labelError) {
      setLabels((prev) => ({
        ...prev,
        [key]: { ...(previous || {}), error: `Not saved: ${errorInfo(labelError, "Save failed.").userMessage}` },
      }));
    }
  }

  function swapRuns() {
    setRuns((prev) => ({ A: prev.B, B: prev.A }));
  }

  function saveToEvaluate() {
    const relevantCount = Object.values(labels).filter((entry) => entry.label === "relevant").length;
    const result = saveQueryToEvaluate(searchedQuery, language, relevantCount);
    setSaveMessage(
      result === "exists"
        ? `“${searchedQuery}” is already in the ${SAVED_QUERY_SET_NAME} query set.`
        : `Saved to the ${SAVED_QUERY_SET_NAME} query set in Evaluate.`,
    );
  }

  const rowsA = outcomes.A?.response?.results || [];
  const rowsB = outcomes.B?.response?.results || [];
  const rankA = useMemo(() => new Map(rowsA.map((row, index) => [chunkKey(row), index + 1])), [rowsA]);
  const rankB = useMemo(() => new Map(rowsB.map((row, index) => [chunkKey(row), index + 1])), [rowsB]);
  const uniqueKeys = new Set([...rankA.keys(), ...rankB.keys()]);
  const labeledCount = [...uniqueKeys].filter((key) => labels[key]?.label).length;
  const shared = [...rankA.keys()].filter((key) => rankB.has(key)).length;
  const metricsA = rankingMetrics(rowsA, labels, Number(outcomes.A?.run?.k || runs.A.k));
  const metricsB = rankingMetrics(rowsB, labels, Number(outcomes.B?.run?.k || runs.B.k));
  const hasResults = Boolean(outcomes.A || outcomes.B);

  function deltaFor(key, rank, otherRanks, otherId) {
    const other = otherRanks.get(key);
    if (!other) {
      return otherId === "A"
        ? { text: "new", tone: "new", title: "Not in Run A" }
        : { text: "only A", tone: "dropped", title: "Not in Run B" };
    }
    if (otherId === "B") {
      return null;
    }
    const moved = other - rank;
    if (moved === 0) return { text: "=", tone: "same", title: "Same rank as Run A" };
    return moved > 0
      ? { text: `▲${moved}`, tone: "up", title: `${moved} place${moved === 1 ? "" : "s"} higher than Run A` }
      : { text: `▼${-moved}`, tone: "down", title: `${-moved} place${moved === -1 ? "" : "s"} lower than Run A` };
  }

  function renderColumn(id, rows, otherRanks, otherId) {
    const outcome = outcomes[id];
    return (
      <div className="lab-column" data-run={id}>
        <div className="lab-column-head">
          <span className={`lab-run-tag run-${id.toLowerCase()}`}>{id}</span>
          <span>{describeRun(outcome?.run || runs[id])}</span>
        </div>
        {outcome?.error ? <div className="friendly-error" role="alert"><span>{outcome.error}</span></div> : null}
        {outcome && !outcome.error && !rows.length ? <div className="search-empty">No results in this scope.</div> : null}
        {rows.map((row, index) => {
          const key = chunkKey(row);
          return (
            <ResultCard
              key={`${id}-${key || index}`}
              row={row}
              rank={index + 1}
              delta={otherRanks ? deltaFor(key, index + 1, otherRanks, otherId) : null}
              label={labels[key]}
              disabled={loading}
              onLabel={(target, value) => labelRow(target, value, id)}
            />
          );
        })}
      </div>
    );
  }

  function formatMetric(value) {
    return value === null ? "—" : value.toFixed(2);
  }

  const scopeVideos = scopeMode === "videos"
    ? videos.filter((video) => selectedVideoIds.includes(String(video.video_id)))
    : videos;
  const scopeChunks = scopeVideos.reduce((sum, video) => sum + Number(video.num_chunks || 0), 0);

  return (
    <section className="lab-workbench">
      <header className="hero">
        <h1>Search</h1>
        <p className="subtitle">Run a query against the library, compare two retrieval setups, and label what comes back.</p>
      </header>

      {view !== "answer" ? (
        <form className="panel lab-query-panel" onSubmit={runSearch}>
          <div className="lab-query-row">
            <label className="lab-query-input">
              <span className="sr-only">Query</span>
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Ask about the transcripts, e.g. アニメ制作で作画監督は何をチェックしている？"
                aria-label="Query"
              />
            </label>
            <button className="btn" type="submit" disabled={loading || labelSavePending || !query.trim()}>
              {loading ? "Running…" : "Run A and B"}
            </button>
          </div>
          <div className="lab-scope-row">
            <label>
              <span>Scope</span>
              <select value={scopeMode} onChange={(event) => setScopeMode(event.target.value)} aria-label="Scope">
                <option value="all">All indexed videos</option>
                <option value="videos">Chosen videos</option>
                <option value="catalog">Catalog filter</option>
              </select>
            </label>
            <label>
              <span>Language</span>
              <select value={language} onChange={(event) => setLanguage(event.target.value)} aria-label="Language">
                <option value="">Any</option>
                <option value="ja">ja</option>
                <option value="en">en</option>
              </select>
            </label>
            {scopeMode === "catalog" ? (
              <>
                <label>
                  <span>Catalog topic</span>
                  <select
                    value={catalogTerm}
                    onChange={(event) => setCatalogTerm(event.target.value)}
                    aria-label="Catalog topic"
                    disabled={!Array.isArray(catalogTerms)}
                  >
                    <option value="">Any topic</option>
                    {Array.isArray(catalogTerms)
                      ? catalogTerms.map((term) => <option key={term.key} value={term.key}>{term.name}</option>)
                      : null}
                  </select>
                </label>
                <label>
                  <span>Review status</span>
                  <select
                    value={catalogQualification}
                    onChange={(event) => setCatalogQualification(event.target.value)}
                    aria-label="Review status"
                  >
                    <option value="eligible">Eligible</option>
                    <option value="">Any status</option>
                    <option value="not_reviewed">Not reviewed</option>
                    <option value="hold">On hold</option>
                  </select>
                </label>
              </>
            ) : null}
          </div>
          {scopeMode === "catalog" && catalogTerms === "unavailable" ? (
            <p className="lab-hint">The catalog service is not connected, so catalog filters cannot be applied.</p>
          ) : null}
          {scopeMode === "catalog" ? (
            <p className="lab-hint">Only indexed videos linked to matching catalog items are searched. Discovered-only items are never searched.</p>
          ) : null}
          {scopeMode === "videos" ? (
            <div className="lab-video-picker" role="group" aria-label="Videos to search">
              {videos.map((video) => {
                const id = String(video.video_id);
                const checked = selectedVideoIds.includes(id);
                return (
                  <label key={id} className={`lab-pick ${checked ? "on" : ""}`}>
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => setSelectedVideoIds((prev) => (checked ? prev.filter((value) => value !== id) : [...prev, id]))}
                    />
                    <span>{video.title || id}</span>
                  </label>
                );
              })}
            </div>
          ) : null}
          {error ? <div className="friendly-error" role="alert"><span>{error}</span></div> : null}
        </form>
      ) : null}

      <div className="lab-tabs lab-view-tabs" role="tablist" aria-label="Result views">
        {[
          ["compare", "Compare A vs B"],
          ["a", "Run A"],
          ["b", "Run B"],
          ["answer", "Answer"],
        ].map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={view === value}
            className={view === value ? "active" : ""}
            onClick={() => setView(value)}
          >
            {label}
          </button>
        ))}
      </div>

      {view === "answer" ? (
        <section className="panel studio-mode-content lab-answer">
          <p className="lab-hint">Generate a cited answer. The answer flow has its own retrieval settings; it does not use Run A or Run B.</p>
          {renderAnswer(searchedQuery || query)}
        </section>
      ) : null}

      {view !== "answer" ? (
        <div className="lab-workbench-grid">
          <aside className="lab-setup">
            <h2 className="lab-eyebrow">Retrieval setup</h2>
            <RunSetup id="A" run={runs.A} onChange={(run) => setRuns((prev) => ({ ...prev, A: run }))} />
            <RunSetup id="B" run={runs.B} onChange={(run) => setRuns((prev) => ({ ...prev, B: run }))} />
            <button className="btn secondary lab-wide" type="button" onClick={swapRuns}>Swap A and B</button>
            <h2 className="lab-eyebrow">In scope</h2>
            <div className="lab-corpus">
              <div><span>Videos</span><strong>{scopeVideos.length}</strong></div>
              <div><span>Chunks</span><strong>{scopeChunks.toLocaleString()}</strong></div>
            </div>
          </aside>

          <section className="panel lab-results">


            {!hasResults ? (
              <div className="search-empty">Run a query to compare Run A ({describeRun(runs.A)}) with Run B ({describeRun(runs.B)}).</div>
            ) : null}

            {hasResults ? (
              <>
                <div className="lab-result-head">
                  <p className="lab-muted">“{searchedQuery}” · {scopeSummary}</p>
                  <div className="lab-result-actions">
                    <button className="btn secondary" type="button" onClick={saveToEvaluate} disabled={loading || labelSavePending}>Save to query set</button>
                    <a className="btn secondary" href="/evaluation.html">Open Evaluate</a>
                  </div>
                </div>
                {saveMessage ? <p className="lab-hint" role="status">{saveMessage}</p> : null}
                <div className="lab-metrics">
                  <div><span>Labeled</span><strong>{labeledCount} of {uniqueKeys.size} unique hits</strong></div>
                  <div><span>Precision@K</span><strong><b className="a">A {formatMetric(metricsA.precision)}</b> · <b className="b">B {formatMetric(metricsB.precision)}</b></strong></div>
                  <div><span>nDCG@K</span><strong><b className="a">A {formatMetric(metricsA.ndcg)}</b> · <b className="b">B {formatMetric(metricsB.ndcg)}</b></strong></div>
                  <div><span>Overlap</span><strong>{shared} shared</strong></div>
                </div>
                <p className="lab-hint">
                  Metrics use your labels for this query. Both runs ignore feedback tuning, so labeling never changes the rankings being compared.
                </p>

                {view === "compare" ? (
                  <div className="lab-compare">
                    {renderColumn("A", rowsA, rankB, "B")}
                    {renderColumn("B", rowsB, rankA, "A")}
                  </div>
                ) : null}
                {view === "a" ? renderColumn("A", rowsA, null, null) : null}
                {view === "b" ? renderColumn("B", rowsB, null, null) : null}

                <section className="lab-trace-section" aria-label="How results were found">
                  <h2 className="lab-eyebrow">How results were found</h2>
                  <div className="lab-trace-grid">
                    {view !== "b" ? <RunTrace id="A" run={outcomes.A?.run || runs.A} outcome={outcomes.A} /> : null}
                    {view !== "a" ? <RunTrace id="B" run={outcomes.B?.run || runs.B} outcome={outcomes.B} /> : null}
                  </div>
                </section>
              </>
            ) : null}
          </section>
        </div>
      ) : null}
    </section>
  );
}
