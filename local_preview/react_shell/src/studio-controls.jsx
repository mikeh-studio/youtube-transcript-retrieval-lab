import React, { useEffect, useState } from "react";
import { apiRequest, formatSeconds } from "./lab.jsx";
import { audioPlayback, remoteTimingNote } from "../../web/audio-playback.js";

const FALLBACK_LLM_PROVIDER_OPTIONS = {
  chatgpt: {
    default: "gpt-5.4-mini",
    models: [
      { id: "gpt-5.4-nano", label: "GPT-5.4 nano" },
      { id: "gpt-5.4-mini", label: "GPT-5.4 mini" },
      { id: "gpt-5.5", label: "GPT-5.5" },
    ],
  },
  claude: {
    default: "",
    models: [
      { id: "claude-haiku-4-5", label: "Claude Haiku 4.5" },
      { id: "claude-sonnet-4-6", label: "Claude Sonnet 4.6" },
      { id: "claude-opus-4-8", label: "Claude Opus 4.8" },
    ],
  },
  sakana: {
    default: "fugu",
    models: [
      { id: "fugu", label: "Sakana Fugu" },
      { id: "fugu-ultra", label: "Sakana Fugu Ultra" },
      { id: "fugu-ultra-20260615", label: "Sakana Fugu Ultra 20260615" },
    ],
  },
};

let llmProviderOptionsPromise = null;

function loadLlmProviderOptions() {
  if (!llmProviderOptionsPromise) {
    llmProviderOptionsPromise = apiRequest("/v1/llm-options")
      .then((payload) => payload?.providers || FALLBACK_LLM_PROVIDER_OPTIONS)
      .catch(() => FALLBACK_LLM_PROVIDER_OPTIONS);
  }
  return llmProviderOptionsPromise;
}

export function useLlmProviderOptions() {
  const [options, setOptions] = useState(FALLBACK_LLM_PROVIDER_OPTIONS);
  useEffect(() => {
    let cancelled = false;
    loadLlmProviderOptions().then((value) => {
      if (!cancelled) {
        setOptions(value);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);
  return options;
}

export function ModelSelectLabel({ providerOptions, provider, model, onChange, disabled = false, label = "Model" }) {
  const providerEntry = providerOptions?.[provider] || {};
  const models = Array.isArray(providerEntry.models) ? providerEntry.models : [];
  const defaultLabel = providerEntry.default
    ? `Default (${providerEntry.default})`
    : "Default";
  return (
    <label>
      <span>{label}</span>
      <select
        value={model}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">{defaultLabel}</option>
        {models
          .map((row) => (
            <option key={row.id} value={row.id}>
              {row.id === providerEntry.default ? `${row.label} (default)` : row.label}
            </option>
          ))}
      </select>
    </label>
  );
}

function AudioPlayer({ videoId, source, start, title }) {
  const [localFailed, setLocalFailed] = useState(false);
  const [remoteFailed, setRemoteFailed] = useState(false);
  const { local, remote } = audioPlayback(videoId, source, start);
  const useLocal = local && !localFailed;
  const src = useLocal ? local : remoteFailed ? "" : remote;

  return (
    <section className="panel">
      <h2 className="section-title">Audio player</h2>
      <p className="section-desc">{title} ({formatSeconds(start)})</p>
      {src ? <audio controls autoPlay key={src} src={src} onError={() => {
        if (useLocal) setLocalFailed(true);
        else setRemoteFailed(true);
      }} /> : <p role="status">Audio is unavailable for this source.</p>}
      {!useLocal && src ? <p className="lab-hint">{remoteTimingNote}</p> : null}
    </section>
  );
}

export function PlayerPanel({ videoId, startSeconds, title, video }) {
  if (!videoId) {
    return null;
  }

  const offset = Number(startSeconds);
  const seconds = Number.isFinite(offset) ? Math.max(0, offset) : 0;
  const source = video?.source;
  if ((video?.source_type || source?.platform || "youtube") !== "youtube") {
    return <AudioPlayer key={`${videoId}-${seconds}`} videoId={videoId} source={source || {}}
      start={seconds} title={title || videoId} />;
  }

  const safeVideoId = encodeURIComponent(videoId);
  const start = Math.floor(seconds);
  const src = `https://www.youtube.com/embed/${safeVideoId}?start=${start}&autoplay=1&rel=0&modestbranding=1&playsinline=1`;

  return (
    <section className="panel">
      <h2 className="section-title">
        <img src="/icons/icon-play.svg" alt="" aria-hidden="true" />
        <span>YouTube Player</span>
      </h2>
      <p className="section-desc">{title ? `Now playing: ${title} (${formatSeconds(start)})` : "Now playing"}</p>
      <div className="player-shell player-active">
        <iframe
          key={`${videoId}-${start}`}
          className="player-frame"
          title="YouTube player"
          src={src}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
        />
      </div>
    </section>
  );
}
