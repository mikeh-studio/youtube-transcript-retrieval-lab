import React, { useEffect, useMemo, useState } from "react";
import {
  FriendlyError,
  LAST_VIDEO_KEY,
  apiRequest,
  errorInfo,
  navigate,
  thumbnailUrlForVideo,
  writeLocalStorage,
} from "./lab.jsx";

// Search reads this once on load to scope its first query to chosen videos.
export const SEARCH_SCOPE_HANDOFF_KEY = "yt_rag_search_scope";

const CATALOG_PAGE_LIMIT = 100;
const CATALOG_MAX_PAGES = 5;

const QUALIFICATION_LABELS = {
  not_reviewed: "Not reviewed",
  hold: "On hold",
  eligible: "Eligible",
  excluded: "Excluded",
};

// Reads catalog pages until exhausted or the page cap is reached. Returns null
// when the catalog service is not connected.
export async function loadCatalogItems(filters = {}) {
  const items = [];
  let total = 0;
  for (let page = 0; page < CATALOG_MAX_PAGES; page += 1) {
    const params = new URLSearchParams({
      limit: String(CATALOG_PAGE_LIMIT),
      offset: String(page * CATALOG_PAGE_LIMIT),
    });
    Object.entries(filters).forEach(([key, value]) => {
      if (value) {
        params.set(key, value);
      }
    });
    if (filters.term) {
      params.set("vocabulary", "library");
      params.set("version", "v1");
      params.set("descendants", "true");
    }
    let response;
    try {
      response = await fetch(`/v1/catalog/items?${params.toString()}`, { cache: "no-store" });
    } catch (_) {
      return null;
    }
    if (!response.ok) {
      if (page === 0) {
        return null;
      }
      break;
    }
    const payload = await response.json();
    total = Number(payload?.total || 0);
    items.push(...(Array.isArray(payload?.items) ? payload.items : []));
    if (items.length >= total || !payload?.items?.length) {
      break;
    }
  }
  return { items, total, truncated: items.length < total };
}

function channelName(video) {
  return String(video?.source?.channel?.name || "").trim();
}

function openSearchFor(videoIds) {
  try {
    sessionStorage.setItem(
      SEARCH_SCOPE_HANDOFF_KEY,
      JSON.stringify({ mode: "videos", videoIds, at: Date.now() }),
    );
  } catch (_) {
    // best effort only; Search falls back to the whole library
  }
  navigate("/qa");
}

function openStudyFor(videoId) {
  writeLocalStorage(LAST_VIDEO_KEY, videoId);
  navigate("/study");
}

export function LibraryPage({ onSuccess }) {
  const [url, setUrl] = useState("");
  const [mode, setMode] = useState("single");
  const [language, setLanguage] = useState("ja");
  const [force, setForce] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [status, setStatus] = useState("");
  const [rawResponse, setRawResponse] = useState("");
  const [videos, setVideos] = useState([]);
  const [isLoadingVideos, setIsLoadingVideos] = useState(true);
  const [videosError, setVideosError] = useState(null);
  const [deletingVideoId, setDeletingVideoId] = useState("");
  const [filterText, setFilterText] = useState("");
  const [filterLanguage, setFilterLanguage] = useState("");
  const [catalog, setCatalog] = useState({ state: "loading", byVideoId: new Map(), truncated: false });

  async function loadVideos({ silent = false } = {}) {
    if (!silent) {
      setIsLoadingVideos(true);
      setVideosError(null);
    }
    try {
      const payload = await apiRequest("/v1/videos");
      const rows = (Array.isArray(payload?.videos) ? payload.videos : []).slice().reverse();
      setVideos(rows);
      return rows;
    } catch (error) {
      if (!silent) {
        setVideosError(error);
      }
      return [];
    } finally {
      if (!silent) {
        setIsLoadingVideos(false);
      }
    }
  }

  async function refreshUntilVisible(expectedVideoIds, timeoutMs = 8000, intervalMs = 300) {
    const required = Array.from(new Set(expectedVideoIds.filter(Boolean)));
    if (!required.length) {
      await loadVideos();
      return true;
    }
    const startedAt = Date.now();
    while (Date.now() - startedAt <= timeoutMs) {
      const rows = await loadVideos({ silent: true });
      const ids = new Set(rows.map((row) => String(row.video_id || "").trim()));
      if (required.every((id) => ids.has(id))) {
        return true;
      }
      await new Promise((resolve) => window.setTimeout(resolve, intervalMs));
    }
    await loadVideos();
    return false;
  }

  useEffect(() => {
    loadVideos().catch(() => {});
    let cancelled = false;
    loadCatalogItems().then((result) => {
      if (cancelled) {
        return;
      }
      if (!result) {
        setCatalog({ state: "unavailable", byVideoId: new Map(), truncated: false });
        return;
      }
      const byVideoId = new Map(result.items.map((item) => [item.external_id, item]));
      setCatalog({ state: "ready", byVideoId, truncated: result.truncated });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  async function handleSubmit(event) {
    event.preventDefault();
    if (!url.trim()) {
      return;
    }
    setIsSubmitting(true);
    setStatus("Indexing…");
    setRawResponse("");
    try {
      const payload = await apiRequest("/v1/ingest/videos", {
        method: "POST",
        body: { url: url.trim(), mode, language, force },
      });
      const acceptedCount = Number(payload?.queued_count || 0) + Number(payload?.skipped_count || 0);
      if (!payload?.ok || acceptedCount <= 0) {
        throw new Error("The request was accepted but no indexing jobs were created.");
      }
      const completedVideoIds = (payload?.jobs || [])
        .filter((job) => job?.status === "completed")
        .map((job) => String(job?.video_id || "").trim())
        .filter(Boolean);
      const firstVideo = payload?.jobs?.[0]?.video_id || payload?.skipped?.[0]?.video_id || "";
      setRawResponse(JSON.stringify(payload, null, 2));
      const refreshed = await refreshUntilVisible(completedVideoIds);
      const skipped = Number(payload?.skipped_count || 0);
      const noun = completedVideoIds.length === 1 ? "video" : "videos";
      setStatus(
        refreshed
          ? `Indexed ${completedVideoIds.length} ${noun}${skipped ? `, ${skipped} already in the library` : ""}.`
          : "Indexing finished, but the list is still catching up. Use Reload.",
      );
      setUrl("");
      onSuccess(firstVideo);
    } catch (error) {
      const info = errorInfo(error, "Indexing failed.");
      setStatus(`Indexing failed. ${info.userMessage}`);
      setRawResponse(info.debugMessage);
    } finally {
      setIsSubmitting(false);
    }
  }

  async function deleteVideo(videoId) {
    if (!window.confirm(`Delete ${videoId} from the library?`)) {
      return;
    }
    setDeletingVideoId(videoId);
    try {
      await apiRequest(`/v1/videos/${encodeURIComponent(videoId)}`, { method: "DELETE" });
      if (localStorage.getItem(LAST_VIDEO_KEY) === videoId) {
        localStorage.removeItem(LAST_VIDEO_KEY);
      }
      setStatus(`Deleted ${videoId} from the library.`);
      await loadVideos();
    } catch (error) {
      setStatus(`Delete failed. ${errorInfo(error, "Delete failed.").userMessage}`);
    } finally {
      setDeletingVideoId("");
    }
  }

  const languages = useMemo(
    () => Array.from(new Set(videos.map((video) => String(video.language || "").trim()).filter(Boolean))).sort(),
    [videos],
  );
  const totalChunks = videos.reduce((sum, video) => sum + Number(video.num_chunks || 0), 0);
  const staleCount = videos.filter((video) => video.chunking_stale).length;
  const inCatalogCount = catalog.state === "ready"
    ? videos.filter((video) => catalog.byVideoId.has(String(video.video_id))).length
    : 0;

  const visibleVideos = useMemo(() => {
    const needle = filterText.trim().toLowerCase();
    return videos.filter((video) => {
      if (filterLanguage && String(video.language || "") !== filterLanguage) {
        return false;
      }
      if (!needle) {
        return true;
      }
      return [video.title, video.video_id, channelName(video)]
        .some((value) => String(value || "").toLowerCase().includes(needle));
    });
  }, [videos, filterText, filterLanguage]);

  const showCatalogColumn = catalog.state === "ready";
  const showChannelColumn = videos.some((video) => channelName(video));

  return (
    <section className="lab-library">
      <header className="hero">
        <h1>Library</h1>
        <p className="subtitle">
          Videos indexed for retrieval. Each keeps its timestamped transcript as the source of truth for search results and answers.
        </p>
      </header>

      <div className="lab-stats" aria-label="Library summary">
        <div className="lab-stat"><strong>{videos.length}</strong><span>Indexed videos</span></div>
        <div className="lab-stat"><strong>{totalChunks.toLocaleString()}</strong><span>Searchable chunks</span></div>
        <div className="lab-stat">
          <strong>{languages.length ? languages.join(" · ") : "—"}</strong>
          <span>Languages</span>
        </div>
        <div className="lab-stat">
          <strong>{catalog.state === "ready" ? `${inCatalogCount} of ${videos.length}` : "—"}</strong>
          <span>{catalog.state === "unavailable" ? "Catalog not connected" : "Linked to catalog"}</span>
        </div>
      </div>

      <section className="panel lab-add-panel">
        <h2 className="section-title">
          <img src="/icons/icon-upload.svg" alt="" aria-hidden="true" />
          <span>Add to library</span>
        </h2>
        <form className="lab-add-form" onSubmit={handleSubmit}>
          <div className="lab-segmented" role="radiogroup" aria-label="Source type">
            {[
              ["single", "Video"],
              ["playlist", "Playlist"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={mode === value}
                className={mode === value ? "active" : ""}
                onClick={() => setMode(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <label className="lab-add-url">
            <span>YouTube {mode === "playlist" ? "playlist" : "video"} URL</span>
            <input
              required
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder={mode === "playlist" ? "https://www.youtube.com/playlist?list=…" : "https://www.youtube.com/watch?v=…"}
            />
          </label>
          <label>
            <span>Transcript language</span>
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              <option value="ja">Japanese (ja)</option>
              <option value="en">English (en)</option>
            </select>
          </label>
          <label className="checkbox">
            <input type="checkbox" checked={force} onChange={(event) => setForce(event.target.checked)} />
            <span>Re-index if already present</span>
          </label>
          <button className="btn" type="submit" disabled={isSubmitting}>
            {isSubmitting ? "Indexing…" : "Index"}
          </button>
        </form>
        <p className="lab-hint">
          Local video files you own are added through{" "}
          <a href="/index.html#/local-video">Tools → Local video</a>. Videos found in the{" "}
          <a href="/catalog.html">Catalog</a> are indexed here once reviewed.
        </p>
        {status ? <div className="search-summary" role="status">{status}</div> : null}
        {rawResponse ? (
          <details className="raw-json">
            <summary>Raw response</summary>
            <pre className="output">{rawResponse}</pre>
          </details>
        ) : null}
      </section>

      <section className="panel lab-videos-panel">
        <div className="section-head">
          <h2 className="section-title">
            <img src="/icons/icon-library.svg" alt="" aria-hidden="true" />
            <span>Indexed videos</span>
          </h2>
          <div className="lab-filters">
            <input
              type="search"
              value={filterText}
              onChange={(event) => setFilterText(event.target.value)}
              placeholder="Filter by title, channel, or ID"
              aria-label="Filter videos"
            />
            <select
              value={filterLanguage}
              onChange={(event) => setFilterLanguage(event.target.value)}
              aria-label="Filter by language"
            >
              <option value="">All languages</option>
              {languages.map((value) => <option key={value} value={value}>{value}</option>)}
            </select>
            <button className="btn secondary" type="button" onClick={() => loadVideos().catch(() => {})}>
              Reload
            </button>
          </div>
        </div>

        {videosError ? (
          <FriendlyError
            error={videosError}
            title="Could not load indexed videos."
            onRetry={() => loadVideos().catch(() => {})}
          />
        ) : null}
        {isLoadingVideos ? <div className="search-empty">Loading indexed videos…</div> : null}
        {!isLoadingVideos && !videos.length ? (
          <div className="search-empty">No videos indexed yet. Add a YouTube URL above to start.</div>
        ) : null}
        {!isLoadingVideos && videos.length && !visibleVideos.length ? (
          <div className="search-empty">No videos match these filters.</div>
        ) : null}
        {staleCount ? (
          <p className="lab-hint">
            {staleCount} video{staleCount === 1 ? " uses" : "s use"} an older chunking version. Re-index to compare against current chunking.
          </p>
        ) : null}
        {showCatalogColumn && catalog.truncated ? (
          <p className="lab-hint">Catalog status reflects the first {CATALOG_PAGE_LIMIT * CATALOG_MAX_PAGES} catalog items.</p>
        ) : null}

        {!isLoadingVideos && visibleVideos.length ? (
          <div className="lab-table-wrap">
            <table className="lab-table">
              <thead>
                <tr>
                  <th scope="col">Video</th>
                  {showChannelColumn ? <th scope="col">Channel</th> : null}
                  <th scope="col">Lang</th>
                  <th scope="col" className="num">Chunks</th>
                  {showCatalogColumn ? <th scope="col">Catalog</th> : null}
                  <th scope="col"><span className="sr-only">Actions</span></th>
                </tr>
              </thead>
              <tbody>
                {visibleVideos.map((video) => {
                  const videoId = String(video.video_id || "").trim();
                  const thumbnailUrl = thumbnailUrlForVideo(videoId);
                  const catalogItem = catalog.byVideoId.get(videoId);
                  const topics = (catalogItem?.labels || []).map((label) => label.en).filter(Boolean);
                  return (
                    <tr key={videoId} data-video-id={videoId}>
                      <td>
                        <div className="lab-video-cell">
                          <span className="lab-thumb">
                            {thumbnailUrl ? (
                              <img
                                src={thumbnailUrl}
                                alt=""
                                loading="lazy"
                                referrerPolicy="no-referrer"
                                onError={(event) => {
                                  event.currentTarget.style.display = "none";
                                }}
                              />
                            ) : null}
                          </span>
                          <span>
                            <a className="lab-video-title" href={video.url || `https://www.youtube.com/watch?v=${videoId}`} target="_blank" rel="noopener noreferrer">
                              {video.title || videoId}
                            </a>
                            <span className="lab-video-id">
                              {videoId}
                              {video.chunking_stale ? <span className="lab-chip warn">older chunking</span> : null}
                            </span>
                          </span>
                        </div>
                      </td>
                      {showChannelColumn ? <td>{channelName(video) || "—"}</td> : null}
                      <td>{video.language || "—"}</td>
                      <td className="num">{Number(video.num_chunks || 0)}</td>
                      {showCatalogColumn ? (
                        <td>
                          {catalogItem ? (
                            <span className="lab-catalog-cell">
                              <span className={`lab-chip q-${catalogItem.qualification}`}>
                                {QUALIFICATION_LABELS[catalogItem.qualification] || catalogItem.qualification}
                              </span>
                              {topics.slice(0, 2).map((topic) => <span className="lab-chip" key={topic}>{topic}</span>)}
                            </span>
                          ) : (
                            <span className="lab-muted">Not in catalog</span>
                          )}
                        </td>
                      ) : null}
                      <td>
                        <div className="lab-row-actions">
                          <button className="btn secondary" type="button" onClick={() => openSearchFor([videoId])}>
                            Search
                          </button>
                          <button className="btn secondary" type="button" onClick={() => openStudyFor(videoId)}>
                            Study
                          </button>
                          <button
                            className="btn secondary danger"
                            type="button"
                            disabled={deletingVideoId === videoId}
                            onClick={() => deleteVideo(videoId).catch(() => {})}
                            aria-label={`Delete ${video.title || videoId}`}
                          >
                            {deletingVideoId === videoId ? "Deleting…" : "Delete"}
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>
    </section>
  );
}
