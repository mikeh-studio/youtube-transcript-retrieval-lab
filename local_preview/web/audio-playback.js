// Shared by the React player and standalone label review.
export function audioPlayback(sourceId, source, start, end) {
  const offset = Number.isFinite(start) ? Math.max(0, start) : 0;
  const fragment = `#t=${offset}${Number.isFinite(end) && end > offset ? `,${end}` : ""}`;
  let remote = "";
  try {
    const url = new URL(source.media_url);
    if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password) {
      url.hash = fragment;
      remote = url.href;
    }
  } catch {
    // The source may only have a local copy.
  }
  const local = source.local_media_path ? `/v1/sources/${encodeURIComponent(sourceId)}/media${fragment}` : "";
  return { local, remote };
}

export const remoteTimingNote = "Playing the original enclosure. Timestamps may drift.";
