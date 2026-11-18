import React, { useEffect, useState } from "react";
import { apiRequest, errorInfo } from "./lab.jsx";

const ENGINES = [
  ["faster_whisper", "Faster Whisper"],
  ["openai", "OpenAI"],
];
const isActive = (job) => ["queued", "running"].includes(job?.status);

export function AudioIngestPanel({ onIndexed }) {
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState("rss_episode");
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [feed, setFeed] = useState(null);
  const [guid, setGuid] = useState("");
  const [language, setLanguage] = useState("en");
  const [capabilities, setCapabilities] = useState(null);
  const [engine, setEngine] = useState("");
  const [busy, setBusy] = useState(false);
  const [job, setJob] = useState(null);
  const [error, setError] = useState("");
  const [capabilityError, setCapabilityError] = useState("");
  const [pollError, setPollError] = useState("");

  useEffect(() => {
    if (!open || capabilities || capabilityError) return;
    let cancelled = false;
    apiRequest("/v1/stt/capabilities").then(({ capabilities: options }) => {
      if (cancelled) return;
      setCapabilities(options);
      const available = availableEngines(options);
      setEngine(available.includes(options.default_engine) ? options.default_engine : available[0] || "");
    }).catch((failure) => {
      if (!cancelled) setCapabilityError(errorInfo(failure, "Could not load speech-to-text engines.").userMessage);
    });
    return () => { cancelled = true; };
  }, [open, capabilities, capabilityError]);

  useEffect(() => {
    if (!isActive(job) || pollError) return;
    let cancelled = false;
    const timer = window.setTimeout(async () => {
      try {
        const { jobs } = await apiRequest("/v1/ingest/jobs");
        let next = jobs.find((row) => row.job_id === job.job_id);
        if (!next) {
          ({ job: next } = await apiRequest(`/v1/ingest/jobs/${encodeURIComponent(job.job_id)}`));
        }
        if (cancelled) return;
        setJob(next);
        if (next.status === "completed") onIndexed(next.video_id);
      } catch (failure) {
        if (!cancelled) setPollError(errorInfo(failure, "Could not load indexing progress.").userMessage);
      }
    }, 750);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [job, onIndexed, pollError]);

  async function preview(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setJob(null);
    setFeed(null);
    setGuid("");
    try {
      const result = await apiRequest("/v1/feeds/preview", {
        method: "POST",
        body: { url: url.trim() },
      });
      setFeed(result);
      const feedLanguage = result.language?.toLowerCase().split("-")[0];
      if (["en", "ja"].includes(feedLanguage)) setLanguage(feedLanguage);
    } catch (failure) {
      setError(errorInfo(failure, "Could not preview the feed.").userMessage);
    } finally {
      setBusy(false);
    }
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setJob(null);
    try {
      const source = mode === "rss_episode" ? { feed_url: url.trim(), guid } : { path: path.trim() };
      const { job: next } = await apiRequest("/v1/ingest/audio", {
        method: "POST",
        body: { kind: mode, language, engine, ...source },
      });
      setJob(next);
      if (next.status === "completed") onIndexed(next.video_id);
    } catch (failure) {
      setError(errorInfo(failure, "Could not queue the audio.").userMessage);
    } finally {
      setBusy(false);
    }
  }

  const locked = busy || isActive(job);
  const seen = new Set();
  const episodes = (feed?.items || []).filter((item) => {
    if (!item.external_id || seen.has(item.external_id)) return false;
    seen.add(item.external_id);
    return Boolean(item.media_url);
  });
  const available = capabilities ? availableEngines(capabilities) : [];

  return (
    <details className="panel lab-add-panel" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary className="section-title">Add audio</summary>
      <div className="grid lab-audio-fields">
        <label>
          <span>Audio source</span>
          <select value={mode} disabled={locked} onChange={(event) => {
            setMode(event.target.value);
            setJob(null);
            setError("");
          }}>
            <option value="rss_episode">Podcast feed</option>
            <option value="local_file">Local audio file</option>
          </select>
        </label>
        {mode === "rss_episode" ? (
          <form className="lab-audio-form" onSubmit={preview}>
            <label className="lab-add-url">
              <span>RSS feed URL</span>
              <input type="url" required value={url} disabled={locked} onChange={(event) => {
                setUrl(event.target.value);
                setFeed(null);
                setGuid("");
              }} />
            </label>
            <button className="btn secondary" type="submit" disabled={locked}>Preview feed</button>
          </form>
        ) : null}
        <form className="lab-audio-form" onSubmit={submit}>
          {mode === "local_file" ? (
            <label className="lab-add-url">
              <span>Local file path</span>
              <input required value={path} disabled={locked} onChange={(event) => setPath(event.target.value)} />
            </label>
          ) : feed ? (
            <label className="lab-add-url">
              <span>Episode</span>
              <select required value={guid} disabled={locked || !episodes.length} onChange={(event) => setGuid(event.target.value)}>
                <option value="">{episodes.length ? "Choose an episode" : "No episodes with audio enclosures"}</option>
                {episodes.map((item) => (
                  <option key={item.external_id} value={item.external_id}>{item.title || item.external_id}</option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            <span>Audio language</span>
            <select value={language} disabled={locked} onChange={(event) => setLanguage(event.target.value)}>
              <option value="en">English (en)</option>
              <option value="ja">Japanese (ja)</option>
            </select>
          </label>
          <label>
            <span>Transcription engine</span>
            <select required value={engine} disabled={locked || !available.length} onChange={(event) => setEngine(event.target.value)}>
              {!engine ? <option value="">{capabilities || capabilityError ? "No available engine" : "Loading engines…"}</option> : null}
              {capabilities ? ENGINES.map(([id, label]) => (
                <option key={id} value={id} disabled={!available.includes(id)}>{label}</option>
              )) : null}
            </select>
          </label>
          <button className="btn" type="submit" disabled={locked || !engine || (mode === "rss_episode" ? !guid : !path.trim())}>
            Index audio
          </button>
        </form>
        {capabilityError ? (
          <div role="alert">{capabilityError} <button className="btn secondary" type="button" onClick={() => setCapabilityError("")}>Retry engines</button></div>
        ) : null}
        {capabilities && !available.length ? <p className="lab-hint">Install the audio dependencies and configure an engine to index audio.</p> : null}
        {busy ? <p role="status">Sending request…</p> : null}
        {error ? <p role="alert">{error}</p> : null}
        {job ? (
          <div className="search-summary">
            <span role="status">{job.status === "completed" ? "Audio indexed." : job.status === "failed" ? (
              `Audio indexing failed. ${job.error_message || ""}`
            ) : `Audio indexing · ${job.step || job.status} · ${Math.round(job.progress * 100)}%`}</span>
            {isActive(job) ? <progress aria-label="Audio indexing progress" value={job.progress} max={1} /> : null}
            {pollError ? (
              <div role="alert">{pollError} <button className="btn secondary" type="button" onClick={() => setPollError("")}>Retry status</button></div>
            ) : null}
          </div>
        ) : null}
      </div>
    </details>
  );
}

function availableEngines(options) {
  if (!options.av_importable) return [];
  return [
    ...(options.faster_whisper_importable ? ["faster_whisper"] : []),
    ...(options.openai_key_present ? ["openai"] : []),
  ];
}
