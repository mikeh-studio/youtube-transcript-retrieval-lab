import React, { useEffect, useMemo, useState } from "react";
import "./workspace.css";
import {
  LAB_BRAND,
  LAB_NAV_PRIMARY,
  labNavLabel,
} from "../../web/lab-nav.js";
import {
  FriendlyError,
  LAST_VIDEO_KEY,
  apiRequest,
  errorInfo,
  formatRange,
  formatSeconds,
  navigate,
  readLocalStorage,
  safeExternalUrl,
  writeLocalStorage,
} from "./lab.jsx";
import { LibraryPage } from "./library.jsx";
import { SearchWorkbench } from "./workbench.jsx";
import { SettingsPage } from "./settings.jsx";
import { WhatsNewPage } from "./whats-new.jsx";
import { CollectionPage, KeyAreasPage } from "./summaries.jsx";
import { ModelSelectLabel, PlayerPanel, useLlmProviderOptions } from "./studio-controls.jsx";

const LOCK_KEY = "yt_rag_ingest_unlocked";
const LOCALE_STORAGE_KEY = "youtube-rag-ui-locale";
const INTRO_SEEN_SESSION_KEY = "yt_rag_intro_seen";
const STUDY_HISTORY_SESSION_KEY = "yt_rag_study_history_v2";
const STUDY_HISTORY_LIMIT = 12;
const ROUTES = {
  INGEST: "/ingest",
  LOCAL_VIDEO: "/local-video",
  TLDR: "/tldr",
  STUDY: "/study",
  KEY_AREAS: "/key-areas",
  COLLECTION: "/collection",
  QA: "/qa",
  WHATS_NEW: "/whats-new",
  SETTINGS: "/settings",
};
const STUDY_FOCUS_PRESETS = [
  { id: "main_ideas", label: "Main ideas" },
  { id: "characters", label: "People / characters" },
  { id: "vocabulary", label: "Vocabulary" },
  { id: "timeline", label: "Timeline / sequence" },
  { id: "quotes", label: "Quotes / context" },
  { id: "exam", label: "Exam prep" },
  { id: "language", label: "Language learning" },
  { id: "discussion", label: "Discussion questions" },
];
const STUDY_SCOPES = [
  { id: "whole_video", label: "Whole video" },
  { id: "focused_sections", label: "Strongest sections" },
];
const STUDY_MODEL_PROFILES = [
  { id: "economy", label: "Economy" },
  { id: "balanced", label: "Balanced" },
  { id: "quality", label: "Quality" },
];
const STUDY_TOPIC_DETAIL_LEVELS = [
  { id: "brief", label: "Brief Map" },
  { id: "explain", label: "Explain Topic" },
];
const QA_ANSWER_I18N = {
  "en-US": {
    answerStatusAnswered: "Answered from evidence",
    answerStatusInsufficient: "Insufficient evidence",
    answerStatusError: "Answer unavailable",
    answerStatusDefault: "Answer",
    answerConfidenceHigh: "high",
    answerConfidenceMedium: "medium",
    answerConfidenceLow: "low",
    localVideoTitle: "Local Video Analysis",
    localVideoSubtitle: "Extract visual evidence from a local video, monitor OCR indexing, and ask grounded questions.",
    localVideoProcessTitle: "Process Local Video",
    localVideoProcessDescription: "Use only local video files you own or have permission to process. Public YouTube downloading is not supported.",
    localVideoVideoId: "Video ID",
    localVideoPath: "Local video path",
    localVideoFrameInterval: "Frame interval",
    localVideoQueueing: "Queueing...",
    localVideoRunOcr: "Run OCR",
    localVideoInitialStatus: "Process a local .mp4 you own or have permission to analyze.",
    localVideoStartingStatus: "Starting local video OCR job...",
    localVideoQueuedStatus: "OCR job {jobId} queued. Status updates below.",
    localVideoFailedStatus: "OCR job failed to start. {message}",
    localVideoFrames: "frames {count}",
    localVideoOcrCount: "ocr {count}",
    localVideoVectors: "vectors {count}",
    localVideoInterval: "interval {count}s",
    localVideoUseForAnalysis: "Use for analysis",
    localVideoNoJobs: "No local OCR jobs yet.",
    localVideoJobsError: "Could not load local OCR jobs.",
    localVideoOcrRawJson: "OCR Raw JSON",
    localVideoSearchTitle: "Search Local Video Evidence",
    localVideoSearchDescription: "Inspect matching OCR frames or combine them with transcript chunks for this video ID.",
    localVideoSearchQuery: "Search query",
    localVideoSearchPlaceholder: "scoreboard, slide title, jersey number...",
    localVideoEvidence: "Evidence",
    localVideoOcrOnly: "OCR only",
    localVideoTranscriptAndOcr: "Transcript + OCR",
    localVideoTopK: "Top K",
    localVideoSearching: "Searching...",
    localVideoSearchAction: "Search Local Video",
    localVideoSearchFailed: "Search failed: {message}",
    localVideoSearchResultCount: "{count} evidence item(s)",
    localVideoNoSearchResults: "No matching local video evidence was found.",
    localVideoSearchRawJson: "Search Raw JSON",
    localVideoAskTitle: "Ask About Local Video",
    localVideoAskDescription: "OCR only uses indexed frames. Transcript + OCR also uses transcript chunks when the same video ID exists in the transcript library.",
    localVideoQuestion: "Question",
    localVideoQuestionPlaceholder: "What text or visual information appears in this video?",
    localVideoProvider: "Provider",
    localVideoModel: "Model",
    localVideoGenerating: "Generating...",
    localVideoAskAction: "Ask Local Video",
    localVideoAskFailed: "Ask failed: {message}",
    localVideoEvidenceCount: "{count} evidence item(s)",
    localVideoConfidence: "confidence: {value}",
    localVideoSource: "source: {value}",
    localVideoNoEvidence: "No local video evidence was retrieved.",
    localVideoAskRawJson: "Ask Raw JSON",
  },
  "ja-JP": {
    answerStatusAnswered: "根拠付きで回答",
    answerStatusInsufficient: "根拠が不十分",
    answerStatusError: "回答を生成できません",
    answerStatusDefault: "回答",
    answerConfidenceHigh: "高",
    answerConfidenceMedium: "中",
    answerConfidenceLow: "低",
    localVideoTitle: "ローカル動画分析",
    localVideoSubtitle: "ローカル動画から視覚的な根拠を抽出し、OCR インデックスを確認して、根拠に基づく質問ができます。",
    localVideoProcessTitle: "ローカル動画を処理",
    localVideoProcessDescription: "所有している、または処理する許可を得たローカル動画のみ使用してください。公開 YouTube 動画のダウンロードには対応していません。",
    localVideoVideoId: "動画 ID",
    localVideoPath: "ローカル動画のパス",
    localVideoFrameInterval: "フレーム間隔",
    localVideoQueueing: "登録中...",
    localVideoRunOcr: "OCR を実行",
    localVideoInitialStatus: "所有または処理許可のあるローカル .mp4 を処理します。",
    localVideoStartingStatus: "ローカル動画の OCR ジョブを開始しています...",
    localVideoQueuedStatus: "OCR ジョブ {jobId} を登録しました。進行状況は下に表示されます。",
    localVideoFailedStatus: "OCR ジョブを開始できませんでした。{message}",
    localVideoFrames: "フレーム {count}",
    localVideoOcrCount: "OCR {count}",
    localVideoVectors: "ベクトル {count}",
    localVideoInterval: "間隔 {count}秒",
    localVideoUseForAnalysis: "分析に使用",
    localVideoNoJobs: "ローカル OCR ジョブはまだありません。",
    localVideoJobsError: "ローカル OCR ジョブを読み込めませんでした。",
    localVideoOcrRawJson: "OCR の生 JSON",
    localVideoSearchTitle: "ローカル動画の根拠を検索",
    localVideoSearchDescription: "この動画 ID の OCR フレームを検索し、必要に応じて文字起こしチャンクと組み合わせます。",
    localVideoSearchQuery: "検索クエリ",
    localVideoSearchPlaceholder: "スコアボード、スライドのタイトル、背番号...",
    localVideoEvidence: "根拠",
    localVideoOcrOnly: "OCR のみ",
    localVideoTranscriptAndOcr: "文字起こし + OCR",
    localVideoTopK: "上位 K 件",
    localVideoSearching: "検索中...",
    localVideoSearchAction: "ローカル動画を検索",
    localVideoSearchFailed: "検索に失敗しました: {message}",
    localVideoSearchResultCount: "根拠 {count} 件",
    localVideoNoSearchResults: "一致するローカル動画の根拠が見つかりませんでした。",
    localVideoSearchRawJson: "検索の生 JSON",
    localVideoAskTitle: "ローカル動画について質問",
    localVideoAskDescription: "「OCR のみ」はインデックス済みフレームを使用します。「文字起こし + OCR」は、同じ動画 ID が文字起こしライブラリにある場合、そのチャンクも使用します。",
    localVideoQuestion: "質問",
    localVideoQuestionPlaceholder: "この動画にはどのような文字や視覚情報がありますか？",
    localVideoProvider: "プロバイダー",
    localVideoModel: "モデル",
    localVideoGenerating: "生成中...",
    localVideoAskAction: "ローカル動画に質問",
    localVideoAskFailed: "質問に失敗しました: {message}",
    localVideoEvidenceCount: "根拠 {count} 件",
    localVideoConfidence: "信頼度: {value}",
    localVideoSource: "ソース: {value}",
    localVideoNoEvidence: "ローカル動画の根拠を取得できませんでした。",
    localVideoAskRawJson: "質問の生 JSON",
  },
};

function readHashRoute() {
  const raw = String(window.location.hash || "").replace(/^#/, "").trim();
  if (!raw) {
    return ROUTES.INGEST;
  }
  if (
    raw === ROUTES.INGEST
    || raw === ROUTES.LOCAL_VIDEO
    || raw === ROUTES.TLDR
    || raw === ROUTES.STUDY
    || raw === ROUTES.KEY_AREAS
    || raw === ROUTES.COLLECTION
    || raw === ROUTES.SETTINGS
    || raw === ROUTES.QA
    || raw === ROUTES.WHATS_NEW
  ) {
    return raw;
  }
  return ROUTES.INGEST;
}

function readStudyHistory() {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(STUDY_HISTORY_SESSION_KEY) || "[]");
    if (!Array.isArray(parsed)) {
      return [];
    }
    return parsed
      .filter((run) => run && typeof run === "object" && run.id && run.mode && run.payload)
      .slice(0, STUDY_HISTORY_LIMIT);
  } catch (_) {
    return [];
  }
}

function writeStudyHistory(runs) {
  try {
    sessionStorage.setItem(
      STUDY_HISTORY_SESSION_KEY,
      JSON.stringify(runs.slice(0, STUDY_HISTORY_LIMIT)),
    );
  } catch (_) {
    // best effort only; history should not block fresh generations
  }
}

function makeStudyRunId() {
  const randomPart = typeof crypto !== "undefined" && crypto.randomUUID
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);
  return `study-${Date.now()}-${randomPart}`;
}

function evidenceTimestamp(row) {
  if (row?.timestamp_hhmmss) {
    return row.timestamp_hhmmss;
  }
  if (row?.source_type === "ocr") {
    return formatSeconds(row.timestamp_sec ?? row.start);
  }
  return formatRange(row?.start_sec ?? row?.start_seconds ?? row?.start, row?.end_sec ?? row?.end_seconds ?? row?.end);
}

function evidenceFramePath(row) {
  return row?.frame_path || row?.metadata?.frame_path || "";
}

function qaAnswerText(locale, key, vars = {}) {
  const dictionary = QA_ANSWER_I18N[locale] || QA_ANSWER_I18N["en-US"];
  const template = dictionary[key] || QA_ANSWER_I18N["en-US"][key] || key;
  return template.replace(/\{(\w+)\}/g, (_, token) => String(vars[token] ?? ""));
}

function answerStatusLabel(status, locale) {
  if (status === "answered") {
    return qaAnswerText(locale, "answerStatusAnswered");
  }
  if (status === "insufficient_evidence") {
    return qaAnswerText(locale, "answerStatusInsufficient");
  }
  if (status === "error") {
    return qaAnswerText(locale, "answerStatusError");
  }
  return qaAnswerText(locale, "answerStatusDefault");
}

function answerConfidenceLabel(confidence, locale) {
  const scopedConfidence = String(confidence || "low").trim().toLowerCase();
  if (scopedConfidence === "high") {
    return qaAnswerText(locale, "answerConfidenceHigh");
  }
  if (scopedConfidence === "medium") {
    return qaAnswerText(locale, "answerConfidenceMedium");
  }
  return qaAnswerText(locale, "answerConfidenceLow");
}

function answerStatusTone(status) {
  if (status === "answered") {
    return "ok";
  }
  if (status === "error") {
    return "error";
  }
  return "pending";
}

function prefersReducedMotion() {
  if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function LocalVideoAnalysisPage({ locale }) {
  const [ocrVideoId, setOcrVideoId] = useState("");
  const [ocrVideoPath, setOcrVideoPath] = useState("");
  const [ocrIntervalSec, setOcrIntervalSec] = useState(10);
  const [ocrSubmitting, setOcrSubmitting] = useState(false);
  const [ocrStatus, setOcrStatus] = useState({ key: "localVideoInitialStatus", vars: {} });
  const [ocrRawResponse, setOcrRawResponse] = useState("");
  const [ocrJobs, setOcrJobs] = useState([]);
  const [ocrJobsError, setOcrJobsError] = useState("");

  const [analysisVideoId, setAnalysisVideoId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [searchSourceMode, setSearchSourceMode] = useState("ocr");
  const [searchTopK, setSearchTopK] = useState(5);
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [searchResponse, setSearchResponse] = useState(null);
  const [question, setQuestion] = useState("");
  const [sourceMode, setSourceMode] = useState("ocr");
  const [topK, setTopK] = useState(5);
  const [provider, setProvider] = useState("chatgpt");
  const [model, setModel] = useState("");
  const [askLoading, setAskLoading] = useState(false);
  const [askError, setAskError] = useState("");
  const [askResponse, setAskResponse] = useState(null);
  const llmProviderOptions = useLlmProviderOptions();

  async function loadOcrJobs({ silent = false } = {}) {
    if (!silent) {
      setOcrJobsError("");
    }
    try {
      const payload = await apiRequest("/v1/local-video-ocr/jobs");
      const rows = Array.isArray(payload?.jobs) ? payload.jobs : [];
      setOcrJobs(rows);
      const readyJob = rows.find((job) => Number(job.vector_count || 0) > 0) || rows[0];
      if (readyJob?.video_id) {
        setAnalysisVideoId((current) => current || String(readyJob.video_id));
      }
    } catch (error) {
      if (!silent) {
        setOcrJobsError(error);
      }
    }
  }

  const hasActiveOcrJobs = ocrJobs.some((job) => ["queued", "running"].includes(String(job.status || "")));

  useEffect(() => {
    loadOcrJobs({ silent: true }).catch(() => {});
  }, []);

  useEffect(() => {
    if (!hasActiveOcrJobs) {
      return undefined;
    }
    const intervalId = window.setInterval(() => {
      loadOcrJobs({ silent: true }).catch(() => {});
    }, 3000);
    return () => {
      window.clearInterval(intervalId);
    };
  }, [hasActiveOcrJobs]);

  async function handleOcrSubmit(event) {
    event.preventDefault();
    const scopedVideoId = ocrVideoId.trim();
    const scopedPath = ocrVideoPath.trim();
    if (!scopedVideoId || !scopedPath) {
      return;
    }
    setOcrSubmitting(true);
    setOcrStatus({ key: "localVideoStartingStatus", vars: {} });
    setOcrRawResponse("");
    try {
      const payload = await apiRequest("/v1/local-video-ocr/jobs", {
        method: "POST",
        body: {
          video_id: scopedVideoId,
          video_path: scopedPath,
          interval_sec: Number(ocrIntervalSec || 10),
        },
      });
      setOcrRawResponse(JSON.stringify(payload, null, 2));
      setOcrStatus({
        key: "localVideoQueuedStatus",
        vars: { jobId: payload?.job?.job_id || "" },
      });
      setAnalysisVideoId(scopedVideoId);
      await loadOcrJobs();
    } catch (error) {
      const info = errorInfo(error, "OCR job failed to start.");
      setOcrStatus({
        key: "localVideoFailedStatus",
        vars: { message: info.userMessage },
      });
      setOcrRawResponse(info.debugMessage);
    } finally {
      setOcrSubmitting(false);
    }
  }

  async function runLocalSearch(event) {
    event.preventDefault();
    const scopedVideoId = analysisVideoId.trim();
    if (!scopedVideoId || !searchQuery.trim()) {
      return;
    }
    setSearchLoading(true);
    setSearchError("");
    setSearchResponse(null);
    try {
      const payload = await apiRequest("/v1/search-multimodal", {
        method: "POST",
        body: {
          query: searchQuery.trim(),
          video_id: scopedVideoId,
          source_mode: searchSourceMode,
          retrieval_mode: "hybrid",
          k: Number(searchTopK || 5),
        },
      });
      setSearchResponse(payload);
    } catch (error) {
      setSearchError(String(error?.message || error));
    } finally {
      setSearchLoading(false);
    }
  }

  async function runLocalAsk(event) {
    event.preventDefault();
    const scopedVideoId = analysisVideoId.trim();
    if (!scopedVideoId || !question.trim()) {
      return;
    }
    setAskLoading(true);
    setAskError("");
    setAskResponse(null);
    try {
      const payload = await apiRequest("/v1/ask-multimodal", {
        method: "POST",
        body: {
          question: question.trim(),
          video_id: scopedVideoId,
          source_mode: sourceMode,
          retrieval_mode: "hybrid",
          k: Number(topK || 5),
          provider,
          model: model || undefined,
        },
      });
      setAskResponse(payload);
    } catch (error) {
      setAskError(String(error?.message || error));
    } finally {
      setAskLoading(false);
    }
  }

  const answerStatus = String(askResponse?.status || "").trim();
  const searchEvidence = Array.isArray(searchResponse?.results)
    ? searchResponse.results
    : Array.isArray(searchResponse?.evidence)
      ? searchResponse.evidence
      : [];
  const answerEvidence = Array.isArray(askResponse?.citations) && askResponse.citations.length
    ? askResponse.citations
    : Array.isArray(askResponse?.retrieved_chunks)
      ? askResponse.retrieved_chunks
      : [];

  function localEvidenceKey(row, index) {
    const evidenceId = row.frame_id
      || row.chunk_id
      || (row.chunk_index !== undefined && row.chunk_index !== null ? `chunk-${row.chunk_index}` : "")
      || `${row.start_seconds ?? row.start ?? index}`;
    return `${row.video_id || analysisVideoId}-${evidenceId}`;
  }

  return (
    <section className="ingest-stage local-video-stage">
      <header className="hero">
        <h1>{qaAnswerText(locale, "localVideoTitle")}</h1>
        <p className="subtitle">{qaAnswerText(locale, "localVideoSubtitle")}</p>
      </header>

      <section className="panel local-ocr-panel">
        <h2 className="section-title">
          <img src="/icons/icon-upload.svg" alt="" aria-hidden="true" />
          <span>{qaAnswerText(locale, "localVideoProcessTitle")}</span>
        </h2>
        <p className="section-desc">{qaAnswerText(locale, "localVideoProcessDescription")}</p>
        <form className="grid local-ocr-grid" onSubmit={handleOcrSubmit}>
          <label>
            <span>{qaAnswerText(locale, "localVideoVideoId")}</span>
            <input
              required
              value={ocrVideoId}
              onChange={(event) => setOcrVideoId(event.target.value)}
              placeholder="demo_001"
            />
          </label>
          <label>
            <span>{qaAnswerText(locale, "localVideoPath")}</span>
            <input
              required
              value={ocrVideoPath}
              onChange={(event) => setOcrVideoPath(event.target.value)}
              placeholder="data/raw/demo_001.mp4"
            />
          </label>
          <label>
            <span>{qaAnswerText(locale, "localVideoFrameInterval")}</span>
            <input
              type="number"
              min="1"
              max="120"
              value={ocrIntervalSec}
              onChange={(event) => setOcrIntervalSec(event.target.value)}
            />
          </label>
          <button className="btn" type="submit" disabled={ocrSubmitting}>
            {ocrSubmitting
              ? qaAnswerText(locale, "localVideoQueueing")
              : qaAnswerText(locale, "localVideoRunOcr")}
          </button>
        </form>
        <div className="search-summary">{qaAnswerText(locale, ocrStatus.key, ocrStatus.vars)}</div>
        <div className="ocr-job-grid">
          {ocrJobs.map((job) => (
            <article className="ocr-job-card" key={job.job_id}>
              <div className="search-card-head">
                <div className="search-rank">{job.status}</div>
                <div className="search-title">{job.video_id}</div>
                <div className="search-lang">{job.step}</div>
              </div>
              <div className="search-meta">
                <span>{qaAnswerText(locale, "localVideoFrames", { count: Number(job.frame_count || 0) })}</span>
                <span>{qaAnswerText(locale, "localVideoOcrCount", { count: Number(job.ocr_count || 0) })}</span>
                <span>{qaAnswerText(locale, "localVideoVectors", { count: Number(job.vector_count || 0) })}</span>
                <span>{qaAnswerText(locale, "localVideoInterval", { count: Number(job.interval_sec || 0) })}</span>
              </div>
              {job.error_message ? <p className="search-snippet error-text">{job.error_message}</p> : null}
              <p className="frame-path">{job.video_path}</p>
              <button
                className="btn secondary"
                type="button"
                onClick={() => setAnalysisVideoId(String(job.video_id || ""))}
                disabled={!job.video_id || Number(job.vector_count || 0) <= 0}
              >
                {qaAnswerText(locale, "localVideoUseForAnalysis")}
              </button>
            </article>
          ))}
          {!ocrJobs.length ? (
            <div className="search-empty">{qaAnswerText(locale, "localVideoNoJobs")}</div>
          ) : null}
        </div>
        {ocrJobsError ? (
          <FriendlyError
            error={ocrJobsError}
            title={qaAnswerText(locale, "localVideoJobsError")}
            onRetry={() => loadOcrJobs().catch(() => {})}
          />
        ) : null}
        <details className="raw-json">
          <summary>{qaAnswerText(locale, "localVideoOcrRawJson")}</summary>
          <pre className="output">{ocrRawResponse}</pre>
        </details>
      </section>

      <section className="panel local-video-tool-panel">
        <h2 className="section-title">
          <img src="/icons/icon-search.svg" alt="" aria-hidden="true" />
          <span>{qaAnswerText(locale, "localVideoSearchTitle")}</span>
        </h2>
        <p className="section-desc">{qaAnswerText(locale, "localVideoSearchDescription")}</p>
        <form className="qa-ask-form local-video-search-form" onSubmit={runLocalSearch}>
          <label className="ask-question-field">
            <span>{qaAnswerText(locale, "localVideoSearchQuery")}</span>
            <input
              required
              value={searchQuery}
              onChange={(event) => setSearchQuery(event.target.value)}
              placeholder={qaAnswerText(locale, "localVideoSearchPlaceholder")}
            />
          </label>
          <div className="qa-controls-row ask-controls-row">
            <label>
              <span>{qaAnswerText(locale, "localVideoVideoId")}</span>
              <input
                required
                list="local-video-job-ids"
                value={analysisVideoId}
                onChange={(event) => setAnalysisVideoId(event.target.value)}
                placeholder="demo_001"
              />
            </label>
            <label>
              <span>{qaAnswerText(locale, "localVideoEvidence")}</span>
              <select
                value={searchSourceMode}
                onChange={(event) => setSearchSourceMode(event.target.value)}
              >
                <option value="ocr">{qaAnswerText(locale, "localVideoOcrOnly")}</option>
                <option value="both">{qaAnswerText(locale, "localVideoTranscriptAndOcr")}</option>
              </select>
            </label>
            <label>
              <span>{qaAnswerText(locale, "localVideoTopK")}</span>
              <input
                type="number"
                min="1"
                max="12"
                value={searchTopK}
                onChange={(event) => setSearchTopK(event.target.value)}
              />
            </label>
            <button className="btn qa-primary-action" type="submit" disabled={searchLoading}>
              {searchLoading
                ? qaAnswerText(locale, "localVideoSearching")
                : qaAnswerText(locale, "localVideoSearchAction")}
            </button>
          </div>
        </form>

        {searchError ? (
          <div className="search-summary">
            {qaAnswerText(locale, "localVideoSearchFailed", { message: searchError })}
          </div>
        ) : null}
        {searchResponse ? (
          <>
            <div className="search-summary">
              {qaAnswerText(locale, "localVideoSearchResultCount", { count: searchEvidence.length })}
            </div>
            <div className="search-cards">
              {searchEvidence.map((row, index) => (
                <article
                  className="search-card"
                  data-testid="local-video-search-result"
                  key={localEvidenceKey(row, index)}
                >
                  <div className="search-card-head">
                    <div className="search-rank">#{index + 1}</div>
                    <div className="search-title">{row.video_title || row.video_id || analysisVideoId}</div>
                    <div className={`search-lang source-badge ${row.source_type === "ocr" ? "ocr" : "transcript"}`}>
                      {row.source_type || "evidence"}
                    </div>
                  </div>
                  <div className="search-meta">
                    <span>{row.timestamp_range_label || row.timestamp_label || formatSeconds(row.start_seconds)}</span>
                    {row.frame_id ? <span>{row.frame_id}</span> : null}
                  </div>
                  <p className="search-snippet">{row.snippet || row.text || row.reason}</p>
                  {evidenceFramePath(row) ? <p className="frame-path">{evidenceFramePath(row)}</p> : null}
                </article>
              ))}
              {!searchEvidence.length ? (
                <div className="search-empty">{qaAnswerText(locale, "localVideoNoSearchResults")}</div>
              ) : null}
            </div>
            <details className="raw-json">
              <summary>{qaAnswerText(locale, "localVideoSearchRawJson")}</summary>
              <pre className="output">{JSON.stringify(searchResponse, null, 2)}</pre>
            </details>
          </>
        ) : null}
      </section>

      <section className="panel local-video-tool-panel local-video-ask-panel">
        <h2 className="section-title">
          <img src="/icons/icon-chat.svg" alt="" aria-hidden="true" />
          <span>{qaAnswerText(locale, "localVideoAskTitle")}</span>
        </h2>
        <p className="section-desc">{qaAnswerText(locale, "localVideoAskDescription")}</p>
        <form className="qa-ask-form" onSubmit={runLocalAsk}>
          <label className="ask-question-field">
            <span>{qaAnswerText(locale, "localVideoQuestion")}</span>
            <textarea
              required
              rows="3"
              value={question}
              onChange={(event) => setQuestion(event.target.value)}
              placeholder={qaAnswerText(locale, "localVideoQuestionPlaceholder")}
            />
          </label>
          <div className="qa-controls-row ask-controls-row">
            <label>
              <span>{qaAnswerText(locale, "localVideoVideoId")}</span>
              <input
                required
                list="local-video-job-ids"
                value={analysisVideoId}
                onChange={(event) => setAnalysisVideoId(event.target.value)}
                placeholder="demo_001"
              />
              <datalist id="local-video-job-ids">
                {ocrJobs.map((job) => (
                  <option key={job.job_id} value={job.video_id} />
                ))}
              </datalist>
            </label>
            <label>
              <span>{qaAnswerText(locale, "localVideoEvidence")}</span>
              <select value={sourceMode} onChange={(event) => setSourceMode(event.target.value)}>
                <option value="ocr">{qaAnswerText(locale, "localVideoOcrOnly")}</option>
                <option value="both">{qaAnswerText(locale, "localVideoTranscriptAndOcr")}</option>
              </select>
            </label>
            <label>
              <span>{qaAnswerText(locale, "localVideoTopK")}</span>
              <input
                type="number"
                min="1"
                max="12"
                value={topK}
                onChange={(event) => setTopK(event.target.value)}
              />
            </label>
            <label>
              <span>{qaAnswerText(locale, "localVideoProvider")}</span>
              <select
                value={provider}
                onChange={(event) => {
                  setProvider(event.target.value);
                  setModel("");
                }}
              >
                <option value="chatgpt">ChatGPT</option>
                <option value="claude">Claude</option>
                <option value="sakana">Sakana AI</option>
              </select>
            </label>
            <ModelSelectLabel
              providerOptions={llmProviderOptions}
              provider={provider}
              model={model}
              onChange={setModel}
              label={qaAnswerText(locale, "localVideoModel")}
            />
            <button className="btn qa-primary-action" type="submit" disabled={askLoading}>
              {askLoading
                ? qaAnswerText(locale, "localVideoGenerating")
                : qaAnswerText(locale, "localVideoAskAction")}
            </button>
          </div>
        </form>

        {askError ? (
          <div className="search-summary">
            {qaAnswerText(locale, "localVideoAskFailed", { message: askError })}
          </div>
        ) : null}
        {askResponse ? (
          <>
            <div className="search-summary ask-summary-row">
              <span className={`ask-status-pill ${answerStatusTone(answerStatus)}`}>
                {answerStatusLabel(answerStatus, locale)}
              </span>
              <span>{qaAnswerText(locale, "localVideoEvidenceCount", { count: answerEvidence.length })}</span>
              <span>
                {qaAnswerText(locale, "localVideoConfidence", {
                  value: answerConfidenceLabel(askResponse.confidence, locale),
                })}
              </span>
              <span>
                {qaAnswerText(locale, "localVideoSource", { value: askResponse.source_mode || sourceMode })}
              </span>
            </div>
            <article
              className={`ask-answer ask-answer-${answerStatus || "default"}`}
              data-testid="local-video-answer-panel"
            >
              <p>{askResponse.answer}</p>
              {(askResponse.warnings || []).map((warning) => (
                <p className="error-text" key={warning}>{warning}</p>
              ))}
            </article>
            <div className="search-cards">
              {answerEvidence.map((row, index) => (
                <article
                  className="search-card"
                  data-testid="local-video-evidence-card"
                  key={localEvidenceKey(row, index)}
                >
                  <div className="search-card-head">
                    <div className="search-rank">#{index + 1}</div>
                    <div className="search-title">{row.video_title || row.video_id || analysisVideoId}</div>
                    <div className={`search-lang source-badge ${row.source_type === "ocr" ? "ocr" : "transcript"}`}>
                      {row.source_type || "evidence"}
                    </div>
                  </div>
                  <div className="search-meta">
                    <span>{row.timestamp_range_label || row.timestamp_label || formatSeconds(row.start_seconds)}</span>
                    {row.frame_id ? <span>{row.frame_id}</span> : null}
                  </div>
                  <p className="search-snippet">{row.snippet || row.text || row.reason}</p>
                  {evidenceFramePath(row) ? <p className="frame-path">{evidenceFramePath(row)}</p> : null}
                </article>
              ))}
              {!answerEvidence.length ? (
                <div className="search-empty">{qaAnswerText(locale, "localVideoNoEvidence")}</div>
              ) : null}
            </div>
            <details className="raw-json">
              <summary>{qaAnswerText(locale, "localVideoAskRawJson")}</summary>
              <pre className="output">{JSON.stringify(askResponse, null, 2)}</pre>
            </details>
          </>
        ) : null}
      </section>
    </section>
  );
}

function TLDRStudioPage() {
  const [videos, setVideos] = useState([]);
  const [videoError, setVideoError] = useState("");
  const [isLoadingVideos, setIsLoadingVideos] = useState(true);
  const [selectedVideoId, setSelectedVideoId] = useState("");
  const [language, setLanguage] = useState("en");
  const [provider, setProvider] = useState("chatgpt");
  const [model, setModel] = useState("");
  const llmProviderOptions = useLlmProviderOptions();
  const [summaryResponse, setSummaryResponse] = useState(null);
  const [summaryError, setSummaryError] = useState("");
  const [isSummarizing, setIsSummarizing] = useState(false);

  const [playerState, setPlayerState] = useState({
    videoId: "",
    start: 0,
    title: "",
  });

  useEffect(() => {
    let cancelled = false;
    async function loadVideos() {
      setIsLoadingVideos(true);
      setVideoError("");
      try {
        const payload = await apiRequest("/v1/videos");
        if (cancelled) {
          return;
        }
        const rows = Array.isArray(payload?.videos) ? payload.videos : [];
        setVideos(rows);

        if (!rows.length) {
          setSelectedVideoId("");
          return;
        }

        const saved = String(readLocalStorage(LAST_VIDEO_KEY) || "").trim();
        const matched = rows.find((row) => String(row.video_id) === saved);
        setSelectedVideoId(matched ? matched.video_id : rows[0].video_id);
      } catch (error) {
        if (cancelled) {
          return;
        }
        setVideoError(String(error?.message || error));
      } finally {
        if (!cancelled) {
          setIsLoadingVideos(false);
        }
      }
    }

    loadVideos();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedVideo = useMemo(
    () => videos.find((row) => String(row.video_id) === String(selectedVideoId)) || null,
    [videos, selectedVideoId],
  );

  async function generateSummary(targetVideoId = selectedVideoId) {
    const scopedVideoId = String(targetVideoId || "").trim();
    if (!scopedVideoId) {
      return;
    }
    setSummaryError("");
    setIsSummarizing(true);
    try {
      const payload = await apiRequest("/v1/summaries/transcript", {
        method: "POST",
        body: {
          video_id: scopedVideoId,
          language,
          provider,
          model: model || undefined,
          max_points: 5,
        },
      });
      setSummaryResponse(payload);
      const first = payload?.summary?.[0];
      if (first) {
        setPlayerState({
          videoId: scopedVideoId,
          start: Number(first.start || 0),
          title: selectedVideo?.title || scopedVideoId,
        });
      }
      localStorage.setItem(LAST_VIDEO_KEY, scopedVideoId);
    } catch (error) {
      setSummaryError(String(error?.message || error));
    } finally {
      setIsSummarizing(false);
    }
  }

  const summaryItems = summaryResponse?.summary || [];

  function playSummaryItem(item) {
    const videoId = String(summaryResponse?.video_id || selectedVideoId || "").trim();
    if (!videoId) {
      return;
    }
    setPlayerState({
      videoId,
      start: Number(item.start || 0),
      title: selectedVideo?.title || videoId,
    });
  }

  return (
    <>
      <header className="hero">
        <h1>TLDR Studio</h1>
        <p className="subtitle">
          Generate the top 5 themes from the full transcript with richer, speaker-aware summaries when the transcript supports them.
        </p>
      </header>

      <section className="panel">
        <h2 className="section-title">
          <img src="/icons/icon-chat.svg" alt="" aria-hidden="true" />
          <span>Transcript TLDR</span>
        </h2>
        <form className="grid tldr-grid" onSubmit={(event) => { event.preventDefault(); generateSummary().catch(() => {}); }}>
          <label>
            <span>Video</span>
            <select
              value={selectedVideoId}
              onChange={(event) => {
                setSelectedVideoId(event.target.value);
                setSummaryResponse(null);
                setSummaryError("");
              }}
              disabled={isLoadingVideos || !videos.length}
            >
              {!videos.length ? <option value="">No videos</option> : null}
              {videos.map((row) => (
                <option key={row.video_id} value={row.video_id}>
                  {row.title} ({row.video_id})
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>Output Language</span>
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="en">English</option>
              <option value="ja">Japanese</option>
            </select>
          </label>
          <label>
            <span>Provider</span>
            <select
              value={provider}
              onChange={(event) => {
                setProvider(event.target.value);
                setModel("");
              }}
            >
              <option value="chatgpt">ChatGPT</option>
              <option value="claude">Claude</option>
              <option value="sakana">Sakana AI</option>
            </select>
          </label>
          <ModelSelectLabel
            providerOptions={llmProviderOptions}
            provider={provider}
            model={model}
            onChange={setModel}
          />
          <label>
            <span>Themes</span>
            <input value="5" disabled readOnly />
          </label>
          <button className="btn" type="submit" disabled={isSummarizing || !selectedVideoId}>
            {isSummarizing ? "Generating..." : "Generate TLDR"}
          </button>
        </form>
        {videoError ? <div className="search-summary">Video load failed: {videoError}</div> : null}
        {summaryError ? <div className="search-summary">TLDR failed: {summaryError}</div> : null}
      </section>

      <section className="panel">
        <h3 className="chart-heading">Top 5 Themes</h3>
        {!summaryItems.length ? (
          <div className="search-empty">Click Generate TLDR to create the top 5 themes.</div>
        ) : (
          <div className="search-cards">
            {summaryItems.map((item, index) => (
              <article
                key={`summary-${index}`}
                className="search-card summary-card"
              >
                <div className="search-card-head">
                  <div className="search-rank">#{item.rank ?? index + 1}</div>
                  <div className="search-title">{item.title}</div>
                  <div className="search-lang">{summaryResponse?.language || "-"}</div>
                </div>
                <div className="search-meta">
                  <span>Starts at {formatSeconds(item.start)}</span>
                </div>
                <p className="search-snippet">{item.tldr}</p>
                <div className="search-actions">
                  <button className="btn search-link-btn" type="button" onClick={() => playSummaryItem(item)}>
                    Play at timestamp
                  </button>
                </div>
              </article>
            ))}
          </div>
        )}
      </section>

      <PlayerPanel videoId={playerState.videoId} startSeconds={playerState.start} title={playerState.title}
        video={videos.find((video) => video.video_id === playerState.videoId)} />
    </>
  );
}

function StudyStudioPage() {
  const [videos, setVideos] = useState([]);
  const [videoError, setVideoError] = useState("");
  const [isLoadingVideos, setIsLoadingVideos] = useState(true);
  const [selectedVideoId, setSelectedVideoId] = useState("");
  const [studyMode, setStudyMode] = useState("flashcards");
  const [language, setLanguage] = useState("en");
  const [provider, setProvider] = useState("chatgpt");
  const [model, setModel] = useState("");
  const [difficulty, setDifficulty] = useState("balanced");
  const [cardCount, setCardCount] = useState(8);
  const [focus, setFocus] = useState("");
  const [focusPreset, setFocusPreset] = useState("main_ideas");
  const [scope, setScope] = useState("whole_video");
  const [modelProfile, setModelProfile] = useState("balanced");
  const [topicDetailLevel, setTopicDetailLevel] = useState("brief");
  const [topicRank, setTopicRank] = useState(1);
  const [studyRuns, setStudyRuns] = useState(readStudyHistory);
  const [activeStudyRunId, setActiveStudyRunId] = useState("");
  const [studyError, setStudyError] = useState("");
  const [isGenerating, setIsGenerating] = useState(false);
  const llmProviderOptions = useLlmProviderOptions();
  const [playerState, setPlayerState] = useState({
    videoId: "",
    start: 0,
    title: "",
  });

  useEffect(() => {
    let cancelled = false;
    async function loadVideos() {
      setIsLoadingVideos(true);
      setVideoError("");
      try {
        const payload = await apiRequest("/v1/videos");
        if (cancelled) {
          return;
        }
        const rows = Array.isArray(payload?.videos) ? payload.videos : [];
        setVideos(rows);
        if (!rows.length) {
          setSelectedVideoId("");
          return;
        }
        const saved = String(localStorage.getItem(LAST_VIDEO_KEY) || "").trim();
        const matched = rows.find((row) => String(row.video_id) === saved);
        setSelectedVideoId(matched ? matched.video_id : rows[0].video_id);
      } catch (error) {
        if (!cancelled) {
          setVideoError(String(error?.message || error));
        }
      } finally {
        if (!cancelled) {
          setIsLoadingVideos(false);
        }
      }
    }
    loadVideos();
    return () => {
      cancelled = true;
    };
  }, []);

  const selectedVideo = useMemo(
    () => videos.find((row) => String(row.video_id) === String(selectedVideoId)) || null,
    [videos, selectedVideoId],
  );

  const modeMeta = {
    flashcards: {
      label: "Flashcards",
      action: "Generate Flashcards",
      loading: "Generating flashcards...",
    },
    topics: {
      label: "Topic Map",
      action: "Generate Topic Map",
      loading: "Generating topic map...",
    },
    quality: {
      label: "Quality",
      action: "Run Quality Evaluation",
      loading: "Evaluating deck quality...",
    },
  };
  const studyModes = Object.keys(modeMeta);
  const topicUsesLlm = studyMode === "topics" && topicDetailLevel === "explain";
  const llmControlsDisabled = studyMode !== "flashcards" && !topicUsesLlm;
  const activeStudyRun = useMemo(() => {
    const explicitRun = studyRuns.find((run) => run.id === activeStudyRunId && run.mode === studyMode);
    if (explicitRun) {
      return explicitRun;
    }
    return studyRuns.find((run) => run.mode === studyMode) || null;
  }, [activeStudyRunId, studyMode, studyRuns]);
  const studyResponse = activeStudyRun?.payload || null;
  const latestFlashcardsForVideo = useMemo(() => {
    const matchingRun = studyRuns.find((run) => {
      const cards = run?.payload?.deck?.cards;
      return (
        run.mode === "flashcards"
        && String(run.videoId || run.payload?.video_id || "") === String(selectedVideoId || "")
        && Array.isArray(cards)
        && cards.length
      );
    });
    return matchingRun?.payload?.deck?.cards || [];
  }, [selectedVideoId, studyRuns]);

  function selectStudyMode(mode) {
    setStudyMode(mode);
    setStudyError("");
  }

  function handleStudyTabKey(event, currentMode) {
    if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const currentIndex = studyModes.indexOf(currentMode);
    let nextIndex = currentIndex;
    if (event.key === "ArrowRight") {
      nextIndex = (currentIndex + 1) % studyModes.length;
    } else if (event.key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + studyModes.length) % studyModes.length;
    } else if (event.key === "Home") {
      nextIndex = 0;
    } else if (event.key === "End") {
      nextIndex = studyModes.length - 1;
    }
    selectStudyMode(studyModes[nextIndex]);
  }

  async function generateStudy(event) {
    event.preventDefault();
    const scopedVideoId = String(selectedVideoId || "").trim();
    if (!scopedVideoId) {
      return;
    }
    setStudyError("");
    setIsGenerating(true);
    const safeCardCount = Math.min(20, Math.max(4, Number(cardCount) || 8));
    try {
      const payload = await apiRequest("/v1/study/generate", {
        method: "POST",
        body: {
          mode: studyMode,
          video_id: scopedVideoId,
          language,
          provider: studyMode === "flashcards" || topicUsesLlm ? provider : "local",
          model: studyMode === "flashcards" || topicUsesLlm ? model || undefined : undefined,
          difficulty,
          card_count: safeCardCount,
          focus,
          focus_preset: focusPreset,
          scope,
          model_profile: modelProfile,
          topic_detail_level: studyMode === "topics" ? topicDetailLevel : undefined,
          topic_rank: studyMode === "topics" && topicDetailLevel === "explain" ? Number(topicRank) : undefined,
          cards: studyMode === "quality" && latestFlashcardsForVideo.length ? latestFlashcardsForVideo : undefined,
        },
      });
      const nextRun = {
        id: makeStudyRunId(),
        createdAt: new Date().toISOString(),
        mode: payload?.mode || studyMode,
        videoId: payload?.video_id || scopedVideoId,
        videoTitle: payload?.video_title || selectedVideo?.title || scopedVideoId,
        payload,
      };
      setStudyRuns((previousRuns) => {
        const nextRuns = [
          nextRun,
          ...previousRuns.filter((run) => run.id !== nextRun.id),
        ].slice(0, STUDY_HISTORY_LIMIT);
        writeStudyHistory(nextRuns);
        return nextRuns;
      });
      setActiveStudyRunId(nextRun.id);
      const firstEvidence =
        payload?.deck?.cards?.[0]?.evidence
        || payload?.topics?.[0]?.evidence
        || null;
      if (firstEvidence) {
        setPlayerState({
          videoId: scopedVideoId,
          start: Number(firstEvidence.start || 0),
          title: selectedVideo?.title || scopedVideoId,
        });
      }
      writeLocalStorage(LAST_VIDEO_KEY, scopedVideoId);
    } catch (error) {
      setStudyError(String(error?.message || error));
    } finally {
      setIsGenerating(false);
    }
  }

  function playEvidence(evidence) {
    const scopedVideoId = String(evidence?.video_id || selectedVideoId || "").trim();
    if (!scopedVideoId) {
      return;
    }
    setPlayerState({
      videoId: scopedVideoId,
      start: Number(evidence?.start || 0),
      title: evidence?.video_title || selectedVideo?.title || scopedVideoId,
    });
  }

  function restoreStudyRun(run) {
    if (!run?.id) {
      return;
    }
    const nextMode = run.mode || "flashcards";
    const nextVideoId = String(run.videoId || run.payload?.video_id || "").trim();
    setStudyMode(nextMode);
    setActiveStudyRunId(run.id);
    setStudyError("");
    if (nextVideoId && videos.some((row) => String(row.video_id) === nextVideoId)) {
      setSelectedVideoId(nextVideoId);
      writeLocalStorage(LAST_VIDEO_KEY, nextVideoId);
    }
    const firstEvidence =
      run.payload?.deck?.cards?.[0]?.evidence
      || run.payload?.topics?.[0]?.evidence
      || null;
    if (firstEvidence) {
      setPlayerState({
        videoId: nextVideoId || String(firstEvidence.video_id || selectedVideoId || ""),
        start: Number(firstEvidence.start || 0),
        title: firstEvidence.video_title || run.videoTitle || nextVideoId,
      });
    }
  }

  function clearStudyHistory() {
    setStudyRuns([]);
    setActiveStudyRunId("");
    writeStudyHistory([]);
    setStudyError("");
  }

  function formatStudyRunTime(value) {
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) {
      return "";
    }
    return parsed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  function studyRunMeta(run) {
    const payload = run?.payload || {};
    if (payload.mode === "flashcards") {
      const count = Array.isArray(payload.deck?.cards) ? payload.deck.cards.length : 0;
      return `${count} cards - ${payload.provider || "local"} / ${payload.model || "-"}`;
    }
    if (payload.mode === "topics") {
      const count = Array.isArray(payload.topics) ? payload.topics.length : 0;
      const detail = payload.topic_detail_level === "explain"
        ? `Explain #${payload.topic_rank || 1}`
        : "Brief map";
      return `${detail} - ${count} topic${count === 1 ? "" : "s"}`;
    }
    if (payload.mode === "quality") {
      const score = Number(payload.quality?.score || 0);
      return `${Math.round(score * 100)}% score`;
    }
    return payload.mode || "Study run";
  }

  const cards = Array.isArray(studyResponse?.deck?.cards) ? studyResponse.deck.cards : [];
  const topics = Array.isArray(studyResponse?.topics) ? studyResponse.topics : [];
  const quality = studyResponse?.quality || null;
  const selectedSections = Array.isArray(studyResponse?.evidence_pack?.selected_sections)
    ? studyResponse.evidence_pack.selected_sections
    : [];
  const focusLabel = studyResponse?.focus?.query
    || studyResponse?.focus?.preset_label
    || "Whole video";
  const activeMeta = modeMeta[studyMode] || modeMeta.flashcards;

  return (
    <>
      <header className="hero">
        <h1>Study Studio</h1>
        <p className="subtitle">
          Turn timestamped transcript evidence into flashcards, topic maps, and quality checks for a processed video.
        </p>
      </header>

      <section className="panel study-workbench-panel">
        <div className="qa-workbench-head">
          <h2 className="section-title">
            <img src="/icons/icon-library.svg" alt="" aria-hidden="true" />
            <span>Study Generator</span>
          </h2>
          <div className="qa-tabs study-tabs" role="tablist" aria-label="Study mode">
            {Object.entries(modeMeta).map(([mode, meta]) => (
              <button
                key={mode}
                id={`study-tab-${mode}`}
                className={`qa-tab ${studyMode === mode ? "active" : ""}`}
                type="button"
                role="tab"
                aria-selected={studyMode === mode}
                aria-controls={`study-panel-${mode}`}
                tabIndex={studyMode === mode ? 0 : -1}
                onClick={() => selectStudyMode(mode)}
                onKeyDown={(event) => handleStudyTabKey(event, mode)}
              >
                {meta.label}
              </button>
            ))}
          </div>
        </div>

        <form className="study-form" onSubmit={generateStudy}>
          <label className="study-video-field">
            <span>Video</span>
            <select
              value={selectedVideoId}
              onChange={(event) => {
                setSelectedVideoId(event.target.value);
                setStudyError("");
              }}
              disabled={isLoadingVideos || !videos.length}
              aria-busy={isLoadingVideos}
              aria-describedby="study-video-status"
            >
              {!videos.length ? <option value="">No videos</option> : null}
              {videos.map((row) => (
                <option key={row.video_id} value={row.video_id}>
                  {row.title} ({row.video_id})
                </option>
              ))}
            </select>
            <span className="sr-only" id="study-video-status" aria-live="polite">
              {isLoadingVideos ? "Loading videos." : `${videos.length} videos available.`}
            </span>
          </label>

          <div className="study-focus-row">
            <label className="study-focus-field">
              <span>Focus</span>
              <input
                type="text"
                value={focus}
                onChange={(event) => {
                  setFocus(event.target.value);
                }}
                placeholder="e.g. Serie character interpretation, quiz terms, discussion prompts"
              />
            </label>
            <label>
              <span>Preset</span>
              <select
                value={focusPreset}
                onChange={(event) => {
                  setFocusPreset(event.target.value);
                }}
              >
                {STUDY_FOCUS_PRESETS.map((row) => (
                  <option key={row.id} value={row.id}>{row.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Scope</span>
              <select
                value={scope}
                onChange={(event) => {
                  setScope(event.target.value);
                }}
              >
                {STUDY_SCOPES.map((row) => (
                  <option key={row.id} value={row.id}>{row.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Model Profile</span>
              <select
                value={modelProfile}
                onChange={(event) => {
                  setModelProfile(event.target.value);
                  setModel("");
                }}
              >
                {STUDY_MODEL_PROFILES.map((row) => (
                  <option key={row.id} value={row.id}>{row.label}</option>
                ))}
              </select>
            </label>
          </div>

          <div className="study-controls-row">
            <label>
              <span>Output Language</span>
              <select value={language} onChange={(event) => setLanguage(event.target.value)}>
                <option value="en">English</option>
                <option value="ja">Japanese</option>
              </select>
            </label>
            <label>
              <span>Topic Action</span>
              <select
                value={topicDetailLevel}
                onChange={(event) => {
                  setTopicDetailLevel(event.target.value);
                }}
                disabled={studyMode !== "topics"}
              >
                {STUDY_TOPIC_DETAIL_LEVELS.map((row) => (
                  <option key={row.id} value={row.id}>{row.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Topic</span>
              <select
                value={topicRank}
                onChange={(event) => {
                  setTopicRank(event.target.value);
                }}
                disabled={studyMode !== "topics" || topicDetailLevel !== "explain"}
              >
                {[1, 2, 3, 4, 5].map((rank) => (
                  <option key={rank} value={rank}>#{rank}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Provider</span>
              <select
                value={provider}
                onChange={(event) => {
                  setProvider(event.target.value);
                  setModel("");
                }}
                disabled={llmControlsDisabled}
              >
                <option value="chatgpt">ChatGPT</option>
                <option value="claude">Claude</option>
                <option value="sakana">Sakana AI</option>
              </select>
            </label>
            <ModelSelectLabel
              providerOptions={llmProviderOptions}
              provider={provider}
              model={model}
              onChange={setModel}
              disabled={llmControlsDisabled}
            />
            <label>
              <span>Difficulty</span>
              <select value={difficulty} onChange={(event) => setDifficulty(event.target.value)}>
                <option value="introductory">introductory</option>
                <option value="balanced">balanced</option>
                <option value="exam prep">exam prep</option>
                <option value="advanced review">advanced review</option>
              </select>
            </label>
            <label>
              <span>Cards</span>
              <input
                type="number"
                min="4"
                max="20"
                value={cardCount}
                onChange={(event) => setCardCount(event.target.value)}
                disabled={studyMode !== "flashcards"}
              />
            </label>
            <button className="btn study-primary-action" type="submit" disabled={isGenerating || !selectedVideoId}>
              {isGenerating ? activeMeta.loading : activeMeta.action}
            </button>
          </div>
        </form>
        {videoError ? <div className="search-summary">Video load failed: {videoError}</div> : null}
        {studyError ? <div className="search-summary">Study generation failed: {studyError}</div> : null}
        {studyRuns.length ? (
          <div className="study-history-wrap" aria-label="Study run history">
            <div className="study-history-head">
              <strong>Run History</strong>
              <button className="study-history-clear" type="button" onClick={clearStudyHistory}>
                Clear
              </button>
            </div>
            <div className="study-history-list">
              {studyRuns.map((run) => {
                const runModeLabel = modeMeta[run.mode]?.label || run.mode || "Study";
                const isActiveRun = activeStudyRun?.id === run.id;
                return (
                  <button
                    className={`study-history-item ${isActiveRun ? "active" : ""}`}
                    key={run.id}
                    type="button"
                    onClick={() => restoreStudyRun(run)}
                    aria-pressed={isActiveRun}
                  >
                    <strong>{runModeLabel}</strong>
                    <span>{formatStudyRunTime(run.createdAt)} - {run.videoTitle || run.videoId || "Video"}</span>
                    <em>{studyRunMeta(run)}</em>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}
        {studyResponse ? (
          <div className="search-summary ask-summary-row">
            <span>mode: {studyResponse.mode}</span>
            <span>provider: {studyResponse.provider}</span>
            <span>model: {studyResponse.model}</span>
            <span>profile: {studyResponse.focus?.model_profile_label || "-"}</span>
            {studyResponse.mode === "topics" ? (
              <span>topic: {studyResponse.topic_detail_level || "brief"}</span>
            ) : null}
            <span>focus: {focusLabel}</span>
            <span>segments: {studyResponse.source?.segment_count ?? "-"}</span>
            <span>chunks: {studyResponse.source?.chunk_count ?? "-"}</span>
            <span>sections: {studyResponse.evidence_pack?.selected_section_count ?? "-"}/{studyResponse.evidence_pack?.section_count ?? "-"}</span>
          </div>
        ) : null}
        {selectedSections.length ? (
          <div className="study-evidence-strip" aria-label="Selected study sections">
            {selectedSections.map((section) => (
              <button
                className="study-section-pill"
                key={section.section_id || `${section.rank}-${section.title}`}
                type="button"
                onClick={() => playEvidence(section)}
              >
                <strong>{section.title}</strong>
                <span>{section.timestamp || formatSeconds(section.start)}</span>
              </button>
            ))}
          </div>
        ) : null}
      </section>

      <section
        className="panel"
        role="tabpanel"
        id={`study-panel-${studyMode}`}
        aria-labelledby={`study-tab-${studyMode}`}
      >
        <h3 className="chart-heading">{activeMeta.label} Output</h3>
        {!studyResponse ? (
          <div className="search-empty">Choose a mode and generate study material from an ingested video.</div>
        ) : null}

        {studyResponse?.mode === "flashcards" ? (
          <div className="search-cards study-card-grid">
            {cards.map((card, index) => (
              <article className="search-card study-flashcard" key={`study-card-${index}`}>
                <div className="search-card-head">
                  <div className="search-rank">#{index + 1}</div>
                  <div className="search-title">{card.question}</div>
                  <div className="search-lang">{card.card_type || "card"}</div>
                </div>
                <div className="study-card-meta">
                  {card.learning_objective ? <span>{card.learning_objective}</span> : null}
                </div>
                <p className="study-speaker">
                  <strong>
                    {card.speaker_confidence === "named_in_section" ? "Speaker:" : "Speaker context:"}
                  </strong> {card.speaker || "Unknown speaker"}
                  {card.speaker_role ? ` · ${card.speaker_role}` : ""}
                  {card.speaker_confidence === "named_in_section" ? (
                    <span>Supported by section context</span>
                  ) : card.speaker_confidence === "episode_context" ? (
                    <span>Participants named by the video; the exact turn is not labeled</span>
                  ) : (
                    <span>No reliable attribution in this section</span>
                  )}
                </p>
                <p className="study-answer">{card.answer}</p>
                <p className="search-snippet">{card.explanation}</p>
                {card.why_it_matters ? (
                  <p className="study-why">Why it matters: {card.why_it_matters}</p>
                ) : null}
                {card.language_note ? (
                  <p className="study-language-note">{card.language_note}</p>
                ) : null}
                <div className="chip-row">
                  {(card.tags || []).map((tag) => (
                    <span className="chip" key={`${index}-${tag}`}>{tag}</span>
                  ))}
                </div>
                <p className="citation-reason">Source cue: {card.source_cue}</p>
                <div className="search-actions">
                  <button className="btn search-link-btn" type="button" onClick={() => playEvidence(card.evidence)}>
                    Play source {card.evidence?.timestamp || ""}
                  </button>
                  {safeExternalUrl(card.evidence?.url) ? (
                    <a className="citation-link" href={card.evidence.url} target="_blank" rel="noreferrer">
                      Open source
                    </a>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        ) : null}

        {studyResponse?.mode === "topics" ? (
          <div className="search-cards">
            {topics.map((topic, index) => (
              <article className="search-card summary-card" key={`study-topic-${index}`}>
                <div className="search-card-head">
                  <div className="search-rank">#{topic.rank ?? index + 1}</div>
                  <div className="search-title">{topic.title}</div>
                  <div className="search-lang">{formatSeconds(topic.start)}</div>
                </div>
                <p className="search-snippet">{topic.tldr}</p>
                {Array.isArray(topic.who_is_speaking) && topic.who_is_speaking.length ? (
                  <div className="study-topic-speakers">
                    {topic.who_is_speaking.map((speaker) => (
                      <div className="study-topic-speaker" key={`${topic.rank}-${speaker.name}-${speaker.role}`}>
                        <strong>{speaker.name}</strong>
                        {speaker.role ? <span>{speaker.role}</span> : null}
                        {speaker.confidence ? <em>{speaker.confidence}</em> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {topic.what_they_talked_about ? (
                  <p className="study-topic-detail">{topic.what_they_talked_about}</p>
                ) : null}
                {Array.isArray(topic.source_moments) && topic.source_moments.length ? (
                  <div className="study-source-moments">
                    {topic.source_moments.map((moment, momentIndex) => (
                      <div className="study-source-moment" key={`${topic.rank}-source-${momentIndex}`}>
                        <span>{moment.timestamp || formatSeconds(topic.start)}</span>
                        {moment.speaker ? <strong>{moment.speaker}</strong> : null}
                        <p>{moment.translation || moment.quote}</p>
                        {moment.quote && moment.translation ? <em>{moment.quote}</em> : null}
                        {moment.explanation ? <small>{moment.explanation}</small> : null}
                      </div>
                    ))}
                  </div>
                ) : null}
                {Array.isArray(topic.key_takeaways) && topic.key_takeaways.length ? (
                  <ul className="study-keypoints study-keymoments">
                    {topic.key_takeaways.map((point) => (
                      <li key={`${topic.rank}-takeaway-${point}`}>{point}</li>
                    ))}
                  </ul>
                ) : null}
                {Array.isArray(topic.key_points) && topic.key_points.length ? (
                  <ul className="study-keypoints">
                    {topic.key_points.map((point) => (
                      <li key={`${topic.rank}-${point}`}>{point}</li>
                    ))}
                  </ul>
                ) : null}
                {Array.isArray(topic.people_or_terms) && topic.people_or_terms.length ? (
                  <div className="chip-row">
                    {topic.people_or_terms.map((term) => (
                      <span className="chip" key={`${topic.rank}-${term}`}>{term}</span>
                    ))}
                  </div>
                ) : null}
                {Array.isArray(topic.review_questions) && topic.review_questions.length ? (
                  <ul className="study-keypoints study-review-questions">
                    {topic.review_questions.map((question) => (
                      <li key={`${topic.rank}-question-${question}`}>{question}</li>
                    ))}
                  </ul>
                ) : null}
                {topic.learning_context ? (
                  <p className="study-why">Learning context: {topic.learning_context}</p>
                ) : null}
                <p className="citation-reason">Source cue: {topic.anchor_text}</p>
                <div className="search-actions">
                  <button className="btn search-link-btn" type="button" onClick={() => playEvidence(topic.evidence)}>
                    Play source {formatSeconds(topic.start)}
                  </button>
                  {safeExternalUrl(topic.url) ? (
                    <a className="citation-link" href={topic.url} target="_blank" rel="noreferrer">
                      Open source
                    </a>
                  ) : null}
                </div>
              </article>
            ))}
          </div>
        ) : null}

        {studyResponse?.mode === "quality" && quality ? (
          <div className="study-quality-wrap">
            <div className="study-quality-score">
              <span>Quality score</span>
              <strong>{Math.round(Number(quality.score || 0) * 100)}%</strong>
              <em>{quality.verdict}</em>
            </div>
            <div className="study-metric-grid">
              {Object.entries(quality.metrics || {}).map(([key, value]) => (
                <div className="study-metric-card" key={key}>
                  <span>{key.replaceAll("_", " ")}</span>
                  <strong>{String(value)}</strong>
                </div>
              ))}
            </div>
            <div className="search-cards">
              {(quality.checks || []).map((check) => (
                <article className="search-card study-check-card" key={check.name}>
                  <div className="search-card-head">
                    <div className={`study-check-status ${check.status}`}>{check.status}</div>
                    <div className="search-title">{check.name}</div>
                    <div className="search-lang">{String(check.value)}</div>
                  </div>
                </article>
              ))}
            </div>
            <div className="chip-row">
              {(quality.recommendations || []).map((item) => (
                <span className="chip" key={item}>{item}</span>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      <PlayerPanel videoId={playerState.videoId} startSeconds={playerState.start} title={playerState.title}
        video={videos.find((video) => video.video_id === playerState.videoId)} />
    </>
  );
}

const LOCKED_NAV_HINT = "Index a video in the Library first";

const STUDY_MODES = [
  { route: ROUTES.STUDY, label: "Study", icon: "/icons/icon-library.svg" },
  { route: ROUTES.TLDR, label: "Summary", icon: "/icons/icon-chat.svg" },
  { route: ROUTES.KEY_AREAS, label: "Key areas", icon: "/icons/icon-play.svg" },
];

function StudyWorkspace({ route }) {
  const activeMode = STUDY_MODES.find((mode) => mode.route === route) || STUDY_MODES[0];
  const panelId = `study-panel-${activeMode.label.toLowerCase().replaceAll(" ", "-")}`;

  return (
    <section className="studio-workspace">
      <header className="hero studio-hero">
        <h1>Study</h1>
        <p className="subtitle">Turn an indexed video into study material or a short summary.</p>
      </header>
      <div className="studio-mode-tabs" role="tablist" aria-label="Study tool">
        {STUDY_MODES.map((mode) => {
          const active = mode.route === activeMode.route;
          return (
            <button
              className={`studio-mode-tab ${active ? "active" : ""}`}
              type="button"
              role="tab"
              aria-selected={active}
              aria-controls={active ? panelId : undefined}
              onClick={() => navigate(mode.route)}
              key={mode.route}
            >
              <img src={mode.icon} alt="" aria-hidden="true" />
              <span>{mode.label}</span>
            </button>
          );
        })}
      </div>
      <div className="studio-mode-content" id={panelId} role="tabpanel">
        {route === ROUTES.STUDY ? <StudyStudioPage /> : null}
        {route === ROUTES.TLDR ? <TLDRStudioPage /> : null}
        {route === ROUTES.KEY_AREAS ? <KeyAreasPage /> : null}
      </div>
    </section>
  );
}

function App() {
  const [route, setRoute] = useState(readHashRoute());
  const [unlocked, setUnlocked] = useState(() => localStorage.getItem(LOCK_KEY) === "1");
  const [unlockChecked, setUnlockChecked] = useState(() => localStorage.getItem(LOCK_KEY) === "1");
  const [locale, setLocale] = useState(() => {
    const stored = String(localStorage.getItem(LOCALE_STORAGE_KEY) || "").trim();
    return stored === "ja-JP" ? "ja-JP" : "en-US";
  });
  const [routeAnimationKey, setRouteAnimationKey] = useState(0);
  const [showIntro, setShowIntro] = useState(() => {
    try {
      return sessionStorage.getItem(INTRO_SEEN_SESSION_KEY) !== "1";
    } catch (_) {
      return true;
    }
  });

  useEffect(() => {
    function onHashChange() {
      const next = readHashRoute();
      setRoute(next);
    }
    window.addEventListener("hashchange", onHashChange);
    if (!window.location.hash) {
      navigate(ROUTES.INGEST);
    }
    return () => {
      window.removeEventListener("hashchange", onHashChange);
    };
  }, []);

  useEffect(() => {
    if (unlocked) {
      setUnlockChecked(true);
      return undefined;
    }

    let cancelled = false;
    apiRequest("/v1/videos")
      .then((payload) => {
        if (cancelled) {
          return;
        }
        const rows = Array.isArray(payload?.videos) ? payload.videos : [];
        if (rows.length) {
          localStorage.setItem(LOCK_KEY, "1");
          setUnlocked(true);
        }
      })
      .catch(() => {
        // Keep the locked state when the library cannot be read.
      })
      .finally(() => {
        if (!cancelled) {
          setUnlockChecked(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [unlocked]);

  useEffect(() => {
    if (
      unlockChecked
      && !unlocked
      && route !== ROUTES.INGEST
      && route !== ROUTES.LOCAL_VIDEO
      && route !== ROUTES.SETTINGS
      && route !== ROUTES.QA
      && route !== ROUTES.WHATS_NEW
      && route !== ROUTES.COLLECTION
    ) {
      navigate(ROUTES.INGEST);
    }
  }, [route, unlocked, unlockChecked]);

  useEffect(() => {
    setRouteAnimationKey((prev) => prev + 1);
  }, [route]);

  useEffect(() => {
    document.documentElement.lang = locale === "ja-JP" ? "ja" : "en";
    localStorage.setItem(LOCALE_STORAGE_KEY, locale);
  }, [locale]);

  useEffect(() => {
    if (!showIntro) {
      return undefined;
    }
    const timeoutMs = prefersReducedMotion() ? 70 : 1250;
    const timeoutId = window.setTimeout(() => {
      setShowIntro(false);
      try {
        sessionStorage.setItem(INTRO_SEEN_SESSION_KEY, "1");
      } catch (_) {
        // best effort only
      }
    }, timeoutMs);
    return () => {
      window.clearTimeout(timeoutId);
    };
  }, [showIntro]);

  function handleIngestSuccess(videoId) {
    localStorage.setItem(LOCK_KEY, "1");
    if (videoId) {
      localStorage.setItem(LAST_VIDEO_KEY, videoId);
    }
    setUnlocked(true);
    setUnlockChecked(true);
  }

  const brand = LAB_BRAND[locale] || LAB_BRAND["en-US"];

  function isNavItemLocked(item) {
    return Boolean(item.requiresUnlock) && !unlocked;
  }

  function isNavItemActive(item) {
    if (!item.route) {
      return false;
    }
    const activeRoutes = Array.isArray(item.activeRoutes) ? item.activeRoutes : [item.route];
    return activeRoutes.includes(route);
  }

  function NavItem({ item, mobile = false }) {
    const active = isNavItemActive(item);
    const locked = isNavItemLocked(item);
    const className = mobile ? `bottom-tab-link ${active ? "active" : ""}` : `app-nav-link nav-btn ${active ? "active" : ""}`;
    const content = (
      <>
        <img className={mobile ? "bottom-tab-icon" : "nav-icon"} src={item.icon} alt="" aria-hidden="true" />
        <span className={mobile ? "bottom-tab-label" : "nav-label"}>{labNavLabel(item, locale)}</span>
      </>
    );

    if (locked) {
      return (
        <button type="button" className={`${className} locked`} data-nav-key={item.key} disabled title={LOCKED_NAV_HINT}>
          {content}
        </button>
      );
    }
    if (item.route) {
      return (
        <button
          type="button"
          className={className}
          data-nav-key={item.key}
          onClick={() => navigate(item.route)}
          aria-current={active ? "page" : undefined}
        >
          {content}
        </button>
      );
    }
    return (
      <a className={className} data-nav-key={item.key} href={item.href} aria-current={active ? "page" : undefined}>
        {content}
      </a>
    );
  }


  return (
    <div className={`react-shell-root ${showIntro ? "intro-playing" : ""}`}>
      {showIntro ? (
        <div className="site-intro" data-testid="intro-overlay" aria-hidden="true">
          <div className="site-intro-glow" />
          <div className="site-intro-content">
            <p className="site-intro-eyebrow">{brand.subtitle}</p>
            <p className="site-intro-title">{brand.title}</p>
          </div>
        </div>
      ) : null}
      <header className="appbar">
        <a className="brand brand-link" href="#/ingest" onClick={() => navigate(ROUTES.INGEST)}>
          <img src="/icons/icon-play.svg" alt="" aria-hidden="true" />
          <div>
            <p className="eyebrow">{brand.title}</p>
            <p className="brand-sub">{brand.subtitle}</p>
          </div>
        </a>
        <div className="appbar-actions">
          <nav className="appbar-nav" aria-label="Primary">
            {LAB_NAV_PRIMARY.map((item) => (
              <NavItem key={item.key} item={item} />
            ))}

          </nav>
          <label className="locale-switch">
            <span>Language</span>
            <select value={locale} onChange={(event) => setLocale(event.target.value)}>
              <option value="en-US">English (US)</option>
              <option value="ja-JP">日本語</option>
            </select>
          </label>
        </div>
      </header>

      <main className={`shell react-shell-main ${route === ROUTES.INGEST ? "ingest-route" : ""}`}>
        <div className="route-scene" key={`scene-${route}-${routeAnimationKey}`}>
          {route === ROUTES.INGEST ? (
            <LibraryPage onSuccess={handleIngestSuccess} onSummarizeSelection={() => navigate(ROUTES.COLLECTION)} />
          ) : null}
          {route === ROUTES.LOCAL_VIDEO ? <LocalVideoAnalysisPage locale={locale} /> : null}
          {route === ROUTES.QA ? <SearchWorkbench /> : null}
          {route === ROUTES.WHATS_NEW ? <WhatsNewPage /> : null}
          {route === ROUTES.SETTINGS ? <SettingsPage /> : null}
          {STUDY_MODES.some((mode) => mode.route === route) && unlocked ? <StudyWorkspace route={route} /> : null}
          {route === ROUTES.COLLECTION ? <CollectionPage /> : null}
        </div>
      </main>

      <nav className="bottom-tabbar" aria-label="Primary mobile navigation">
        {LAB_NAV_PRIMARY.map((item) => (
          <NavItem key={`mobile-${item.key}`} item={item} mobile />
        ))}

      </nav>
    </div>
  );
}

export default App;
