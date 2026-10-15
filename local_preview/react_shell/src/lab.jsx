// Helpers shared by the React shell pages.
import React from "react";

export const LAST_VIDEO_KEY = "yt_rag_last_video_id";
export const FEEDBACK_REVISION_STORAGE_KEY = "youtube-rag-feedback-revision";
export const YOUTUBE_ID_PATTERN = /^[a-zA-Z0-9_-]{11}$/;

export function navigate(route) {
  window.location.hash = route;
}

export function markFeedbackRevision() {
  try {
    localStorage.setItem(FEEDBACK_REVISION_STORAGE_KEY, String(Date.now()));
  } catch (_) {
    // best effort only for cross-tab review refreshes
  }
}

export function readLocalStorage(key, fallback = "") {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch (_) {
    return fallback;
  }
}

export function writeLocalStorage(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch (_) {
    // best effort only; storage failures should not break generated output
  }
}

export function optionalNumber(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : null;
}

export async function apiRequest(path, { method = "GET", body } = {}) {
  let response;
  try {
    response = await fetch(path, {
      method,
      cache: "no-store",
      headers: {
        "content-type": "application/json",
      },
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch (error) {
    throw makeFriendlyError(
      "Could not connect to the backend server.",
      String(error?.message || error),
    );
  }

  const rawText = await response.text();
  let payload = null;
  try {
    payload = rawText ? JSON.parse(rawText) : {};
  } catch (error) {
    throw makeFriendlyError(
      "Could not connect to the backend server.",
      [
        `Expected JSON from ${path}, but received a non-JSON response.`,
        `Parse error: ${String(error?.message || error)}`,
        rawText.slice(0, 1200),
      ].filter(Boolean).join("\n\n"),
    );
  }
  if (!response.ok) {
    const message = payload?.error?.message || `Request failed (${response.status})`;
    throw makeFriendlyError(message, JSON.stringify(payload, null, 2) || rawText || message);
  }
  return payload;
}

export function makeFriendlyError(userMessage, debugMessage = "") {
  const error = new Error(userMessage);
  error.userMessage = userMessage;
  error.debugMessage = debugMessage || userMessage;
  return error;
}

export function errorInfo(error, fallbackMessage = "Request failed.") {
  if (!error) {
    return null;
  }
  return {
    userMessage: error.userMessage || String(error.message || error || fallbackMessage),
    debugMessage: error.debugMessage || String(error.message || error || fallbackMessage),
  };
}

export function FriendlyError({ error, title = "Something went wrong.", actionLabel = "Retry", onRetry }) {
  const info = errorInfo(error, title);
  if (!info) {
    return null;
  }
  return (
    <div className="friendly-error" role="alert">
      <div className="friendly-error-main">
        <strong>{title}</strong>
        <span>{info.userMessage}</span>
      </div>
      {onRetry ? (
        <button className="btn secondary" type="button" onClick={onRetry}>
          {actionLabel}
        </button>
      ) : null}
      <details className="friendly-error-debug">
        <summary>Debug details</summary>
        <pre>{info.debugMessage}</pre>
      </details>
    </div>
  );
}

export function formatSeconds(value) {
  const seconds = Math.max(0, Math.floor(Number(value || 0)));
  const minutes = Math.floor(seconds / 60);
  const remaining = seconds % 60;
  return `${minutes}:${String(remaining).padStart(2, "0")}`;
}

export function formatRange(startValue, endValue) {
  const start = Number(startValue || 0);
  const end = Number(endValue ?? startValue ?? 0);
  return `${formatSeconds(start)} - ${formatSeconds(end)}`;
}

export function evidenceSource(row) {
  const source = row?.source || row?.metadata?.source;
  return source && typeof source === "object" ? source : null;
}

export function evidenceProvenanceLabel(row) {
  const source = evidenceSource(row);
  if (!source) {
    return "";
  }
  const platform = String(source.platform || "youtube").trim();
  const channel = String(source.channel?.name || "").trim();
  const platformLabel = platform.toLowerCase() === "youtube" ? "YouTube" : platform;
  return channel ? `${platformLabel} · ${channel}` : platformLabel;
}

export function extractVideoId(value) {
  const raw = String(value || "").trim();
  if (YOUTUBE_ID_PATTERN.test(raw)) {
    return raw;
  }
  const patterns = [
    /[?&]v=([a-zA-Z0-9_-]{11})/,
    /youtu\.be\/([a-zA-Z0-9_-]{11})/,
    /embed\/([a-zA-Z0-9_-]{11})/,
  ];
  for (const pattern of patterns) {
    const match = raw.match(pattern);
    if (match) {
      return match[1];
    }
  }
  return null;
}

export function thumbnailUrlForVideo(videoId) {
  const scopedVideoId = String(videoId || "").trim();
  if (!YOUTUBE_ID_PATTERN.test(scopedVideoId)) {
    return "";
  }
  return `https://i.ytimg.com/vi/${encodeURIComponent(scopedVideoId)}/hqdefault.jpg`;
}

export function safeExternalUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = new URL(raw, window.location.origin);
    return parsed.protocol === "http:" || parsed.protocol === "https:" ? parsed.href : "";
  } catch (_) {
    return "";
  }
}
