import React, { useEffect, useRef, useState } from "react";
import {
  COLLECTION_MAX_SOURCES,
  COLLECTION_MIN_SOURCES,
  COLLECTION_SELECTION_KEY,
  LAST_VIDEO_KEY,
  apiRequest,
  errorInfo,
  formatRange,
  formatSeconds,
  readLocalStorage,
  safeExternalUrl,
  writeLocalStorage,
} from "./lab.jsx";
import { ModelSelectLabel, PlayerPanel, useLlmProviderOptions } from "./studio-controls.jsx";
import "./summaries.css";

const PROVIDER_NAMES = {
  chatgpt: "ChatGPT",
  claude: "Claude",
  sakana: "Sakana AI",
  codex_cli: "Codex CLI",
  claude_cli: "Claude CLI",
};

const STANCE_LABELS = {
  agrees: "Agrees",
  differs: "Differs",
  adds: "Adds",
  only_here: "Only here",
};

const percent = new Intl.NumberFormat("en", { style: "percent", maximumFractionDigits: 1 });

function useSummaryVideos() {
  const [state, setState] = useState({ videos: [], loading: true, error: "" });
  useEffect(() => {
    let cancelled = false;
    apiRequest("/v1/videos").then((payload) => {
      if (!cancelled) {
        const videos = Array.isArray(payload?.videos) ? payload.videos : [];
        setState({ videos, loading: false, error: "" });
      }
    }).catch((error) => {
      if (!cancelled) {
        setState({ videos: [], loading: false, error: errorInfo(error).userMessage });
      }
    });
    return () => { cancelled = true; };
  }, []);
  return state;
}

function useSummary(endpoint) {
  const [result, setResult] = useState(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  function clear() {
    setResult(null);
    setError("");
  }

  async function generate(body) {
    clear();
    setBusy(true);
    try {
      setResult(await apiRequest(endpoint, { method: "POST", body }));
    } catch (failure) {
      setError(errorInfo(failure).userMessage);
    } finally {
      setBusy(false);
    }
  }

  return { result, error, busy, clear, generate };
}

function SummaryForm({ children, action, disabled, summary, onGenerate }) {
  const providerOptions = useLlmProviderOptions();
  const [language, setLanguage] = useState("en");
  const [provider, setProvider] = useState("chatgpt");
  const [model, setModel] = useState("");

  function submit(event) {
    event.preventDefault();
    if (!disabled && !summary.busy) {
      onGenerate({ language, provider, model: model || undefined });
    }
  }

  return (
    <form onSubmit={submit} onChange={summary.clear}>
      <fieldset className="summary-form grid" disabled={summary.busy}>
        {children}
        <label>
          <span>Output language</span>
          <select value={language} onChange={(event) => setLanguage(event.target.value)}>
            <option value="en">English</option>
            <option value="ja">Japanese</option>
          </select>
        </label>
        <label>
          <span>Provider</span>
          <select value={provider} onChange={(event) => { setProvider(event.target.value); setModel(""); }}>
            {Object.keys(providerOptions).map((id) => (
              <option key={id} value={id}>{PROVIDER_NAMES[id] || id}</option>
            ))}
          </select>
        </label>
        <ModelSelectLabel providerOptions={providerOptions} provider={provider} model={model} onChange={setModel} />
        <button className="btn" type="submit" disabled={disabled || summary.busy}>
          {summary.busy ? "Generating…" : action}
        </button>
      </fieldset>
      {summary.error ? <p className="search-summary" role="alert">{summary.error}</p> : null}
    </form>
  );
}

function SectionTimeline({ sections, onPlay }) {
  const durations = sections.map(({ start, end }) => Math.max(0, end - start));
  const hasDuration = durations.some((duration) => duration > 0);
  return (
    <div className="summary-timeline" role="group" aria-label="Section timeline">
      {sections.map((section, index) => {
        const weight = hasDuration ? durations[index] : 1;
        if (!weight) {
          return null;
        }
        const label = `${section.title} (${formatRange(section.start, section.end)})`;
        return (
          <button
            key={section.start_line}
            type="button"
            style={{ flexGrow: weight }}
            title={label}
            aria-label={`Play ${label}`}
            onClick={() => onPlay(section.start)}
          >
            {section.title}
          </button>
        );
      })}
    </div>
  );
}

function KeyAreasResult({ result, video }) {
  const [playback, setPlayback] = useState(null);
  const playerRef = useRef(null);
  const { overview, sections, quality } = result;

  useEffect(() => {
    if (playback) {
      playerRef.current?.scrollIntoView({ block: "nearest" });
    }
  }, [playback]);

  function play(start) {
    setPlayback({ start });
  }

  return (
    <>
      <section className="panel">
        <h2 className="section-title">Overview</h2>
        <p>{overview.tldr}</p>
        <span className="lab-chip summary-quality">
          Citation validity {percent.format(quality.citation_validity)} · Transcript line coverage {percent.format(quality.section_coverage)} · Repaired boundaries {quality.repaired_boundaries}
        </span>
      </section>
      {playback ? (
        <div ref={playerRef}>
          <PlayerPanel videoId={video.video_id} startSeconds={playback.start} title={video.title || video.video_id}
            video={video} />
        </div>
      ) : null}
      <section className="panel">
        <h2 className="section-title">Sections</h2>
        <SectionTimeline sections={sections} onPlay={play} />
        <div className="search-cards">
          {sections.map((section) => (
            <article className="search-card summary-section" key={section.start_line}>
              <div className="summary-section-heading">
                <h3>{section.title}</h3>
                <button className="btn secondary" type="button" onClick={() => play(section.start)}>
                  {formatRange(section.start, section.end)}
                </button>
              </div>
              <p>{section.summary}</p>
              {section.key_points.length ? (
                <ul className="summary-points">
                  {section.key_points.map((point, index) => (
                    <li key={index}>
                      <p>{point.text}</p>
                      <div className="summary-inline">
                        <button className="btn secondary" type="button" onClick={() => play(point.start)}>
                          {formatSeconds(point.start)}
                        </button>
                        {point.speakers.map((speaker) => (
                          <span className="lab-chip summary-speaker" key={speaker}>{speaker}</span>
                        ))}
                      </div>
                    </li>
                  ))}
                </ul>
              ) : null}
            </article>
          ))}
        </div>
      </section>
    </>
  );
}

export function KeyAreasPage() {
  const { videos, loading, error } = useSummaryVideos();
  const [sourceId, setSourceId] = useState(() => readLocalStorage(LAST_VIDEO_KEY));
  const video = videos.find((row) => row.video_id === sourceId) || videos[0];
  const summary = useSummary("/v1/summaries/key-areas");

  function generate(settings) {
    writeLocalStorage(LAST_VIDEO_KEY, video.video_id);
    summary.generate({ source_id: video.video_id, ...settings });
  }

  return (
    <>
      <section className="panel">
        <h2 className="section-title">Episode key areas</h2>
        <SummaryForm action="Generate key areas" disabled={!video} summary={summary} onGenerate={generate}>
          <label>
            <span>Video</span>
            <select value={video?.video_id || ""} onChange={(event) => setSourceId(event.target.value)} disabled={!video}>
              {!video ? <option value="">{loading ? "Loading videos…" : "No videos"}</option> : null}
              {videos.map((row) => (
                <option key={row.video_id} value={row.video_id}>{row.title || row.video_id}</option>
              ))}
            </select>
          </label>
        </SummaryForm>
        {error ? <p className="search-summary" role="alert">Could not load indexed videos. {error}</p> : null}
      </section>
      {summary.result ? <KeyAreasResult result={summary.result} video={video} /> : null}
    </>
  );
}

function readCollectionSelection() {
  try {
    const ids = JSON.parse(sessionStorage.getItem(COLLECTION_SELECTION_KEY) || "[]");
    if (
      Array.isArray(ids) && ids.length >= COLLECTION_MIN_SOURCES && ids.length <= COLLECTION_MAX_SOURCES
      && ids.every((id) => typeof id === "string" && id.trim() === id && id.length > 0)
      && new Set(ids).size === ids.length
    ) {
      return ids;
    }
  } catch (_) {
    // An unreadable handoff is treated as an empty selection.
  }
  return [];
}

function CollectionResult({ result, titles }) {
  const { overview, themes, evidence_catalog: evidence, coverage } = result;
  const citations = new Map(evidence.map((row) => [row.citation_id, row]));
  const citedCount = themes.filter(
    (theme) => theme.status !== "not_returned" && theme.perspectives.length > 0
  ).length;

  return (
    <section className="panel">
      <h2 className="section-title">Themes</h2>
      {overview ? <p>{overview}</p> : null}
      <p className="lab-hint" aria-label="Source coverage">
        Sources cited {coverage.sources_cited} of {coverage.sources_selected} · Citation validity {percent.format(coverage.citation_validity)}
      </p>
      {!citedCount ? <p className="search-empty">No themes with citations were found for this selection.</p> : null}
      <div className="search-cards">
        {themes.map((theme, index) => {
          const notReturned = theme.status === "not_returned";
          return (
            <article className="search-card summary-section" key={theme.theme_index ?? index}>
              <div className="summary-section-heading">
                <h3>{theme.title}</h3>
                {notReturned ? <span className="lab-chip">Not returned</span> : null}
              </div>
              <p>{notReturned ? "The provider did not return this candidate theme." : theme.summary}</p>
              {Object.entries(STANCE_LABELS).map(([stance, label]) => {
                const perspectives = theme.perspectives.filter((row) => row.stance === stance);
                return perspectives.length ? (
                  <div className="summary-stance" key={stance}>
                    <h4>{label}</h4>
                    <ul className="summary-points">
                      {perspectives.map((row) => {
                        const title = titles.get(row.source_id) || row.source_id;
                        return (
                          <li key={row.source_id}>
                            <strong>{title}</strong>
                            <p>{row.statement}</p>
                            <div className="summary-inline">
                              {row.citations.map((id) => {
                                const citation = citations.get(id);
                                const url = safeExternalUrl(citation?.url);
                                if (!url || citation.video_id !== row.source_id) {
                                  return null;
                                }
                                const time = formatSeconds(citation.start_seconds);
                                return (
                                  <a key={id} href={url} target="_blank" rel="noopener noreferrer" aria-label={`${title} at ${time}`}>
                                    {time}
                                  </a>
                                );
                              })}
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                  </div>
                ) : null;
              })}
            </article>
          );
        })}
      </div>
    </section>
  );
}

export function CollectionPage() {
  const [sourceIds] = useState(readCollectionSelection);
  const { videos, loading, error } = useSummaryVideos();
  const titles = new Map(videos.map((video) => [video.video_id, video.title || video.video_id]));
  const summary = useSummary("/v1/summaries/collection");
  const missingSources = !loading && !error && sourceIds.some((id) => !titles.has(id));

  return (
    <section className="grid">
      <header className="hero">
        <h1>Collection summary</h1>
        <a href="#/ingest">Back to Library</a>
      </header>
      <section className="panel">
        <h2 className="section-title">Selected sources</h2>
        {!sourceIds.length ? (
          <p>Select {COLLECTION_MIN_SOURCES}–{COLLECTION_MAX_SOURCES} indexed videos in the Library to summarize them together.</p>
        ) : (
          <>
            <ul className="summary-points">
              {sourceIds.map((id) => <li key={id}>{titles.get(id) || id}</li>)}
            </ul>
            {missingSources ? (
              <p role="alert">Some selected videos are no longer indexed. Choose a new selection in the Library.</p>
            ) : null}
            <SummaryForm
              action="Generate collection summary"
              disabled={loading || missingSources}
              summary={summary}
              onGenerate={(settings) => summary.generate({ source_ids: sourceIds, ...settings })}
            />
          </>
        )}
      </section>
      {summary.result ? <CollectionResult result={summary.result} titles={titles} /> : null}
    </section>
  );
}
