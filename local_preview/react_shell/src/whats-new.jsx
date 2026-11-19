import React, { useEffect, useRef, useState } from "react";
import {
  apiRequest,
  formatSeconds,
  FriendlyError,
  safeExternalUrl,
  YOUTUBE_ID_PATTERN,
} from "./lab.jsx";
import { PlayerPanel } from "./studio-controls.jsx";

const RELEASE_REASONS = {
  title_include: "Title did not match the include filter",
  title_exclude: "Title matched the exclude filter",
  invalid_video_id: "Invalid YouTube video ID",
  missing_enclosure: "Feed item has no audio enclosure",
  min_duration_s: "Shorter than the minimum duration",
  duration_unavailable: "Duration unavailable",
  already_indexed: "Already indexed",
  rate_limited: "YouTube rate limit",
  request_blocked: "YouTube blocked the request",
  ingest_failed: "Ingestion failed",
  source_missing: "Indexed source unavailable",
  backlog: "Published before subscription or undated",
  job_lost: "Audio job unavailable. Check again to retry.",
};
const HIGHLIGHT_GROUPS = [
  ["new", "New to you"],
  ["contradicts", "Disagrees with prior episodes"],
  ["extends", "Extends"],
];

function useRows(path, field, subscriptions) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [revision, setRevision] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    apiRequest(path).then((payload) => {
      if (!cancelled) setRows(Array.isArray(payload[field]) ? payload[field] : []);
    }).catch((failure) => {
      if (!cancelled) setError(failure);
    }).finally(() => {
      if (!cancelled) setLoading(false);
    });
    return () => { cancelled = true; };
  }, [path, field, revision]);

  useEffect(() => {
    if (!subscriptions || loading || error || !rows.some((row) =>
      row.status === "ingesting" && subscriptions.some((sub) => sub.id === row.subscription_id)
    )) return;
    const timer = setTimeout(() => setRevision((value) => value + 1), 3000);
    return () => clearTimeout(timer);
  }, [subscriptions, rows, loading, error]);

  return { rows, loading, error, refresh: () => setRevision((value) => value + 1) };
}

function SubscriptionForm({ busy, onAdd }) {
  const [kind, setKind] = useState("youtube");
  const youtube = kind === "youtube";

  function submit(event) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    const feed = data.get("feed").trim();
    if (!feed) return;
    onAdd({
      kind,
      url: youtube ? `https://www.youtube.com/feeds/videos.xml?channel_id=${encodeURIComponent(feed)}` : feed,
      language: data.get("language"),
      domain_tag: data.get("domain_tag").trim() || null,
      title_include: data.get("title_include") || null,
      title_exclude: data.get("title_exclude") || null,
      min_duration_s: Number(data.get("min_duration_s")),
      auto_ingest: data.has("auto_ingest"),
    }).then((added) => {
      if (added) form.reset();
    });
  }

  return (
    <form onSubmit={submit}>
      <fieldset className="grid whats-new-form" disabled={busy}>
        <label>
          Feed type
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="youtube">YouTube</option>
            <option value="podcast">Podcast RSS</option>
          </select>
        </label>
        <label>
          {youtube ? "YouTube channel ID" : "RSS feed URL"}
          <input key={kind} name="feed" type={youtube ? "text" : "url"} required
            pattern={youtube ? "UC[A-Za-z0-9_\\-]{22}" : undefined}
            title={youtube ? "A 24-character channel ID starting with UC" : undefined} />
        </label>
        <label>
          Source language
          <select name="language">
            <option value="en">English</option>
            <option value="ja">Japanese</option>
          </select>
        </label>
        <label>Domain tag<input name="domain_tag" /></label>
        <label>Include title (regex)<input name="title_include" /></label>
        <label>Exclude title (regex)<input name="title_exclude" /></label>
        <label>Minimum duration (seconds)<input name="min_duration_s" type="number" min="0" defaultValue="600" required /></label>
        <label className="whats-new-checkbox">
          <input name="auto_ingest" type="checkbox" />
          Automatically index eligible releases
        </label>
        <button className="btn" type="submit">Add subscription</button>
      </fieldset>
    </form>
  );
}

function Subscriptions({ subscriptions, refreshReleases }) {
  const [busy, setBusy] = useState(null);
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState("");

  async function add(body) {
    setBusy({ adding: true });
    setError(null);
    setNotice("");
    try {
      await apiRequest("/v1/subscriptions", { method: "POST", body });
      subscriptions.refresh();
      setNotice("Subscription added.");
      return true;
    } catch (failure) {
      setError(failure);
      return false;
    } finally {
      setBusy(null);
    }
  }

  async function poll(id) {
    setBusy({ polling: id });
    setError(null);
    setNotice("");
    try {
      const { poll: result } = await apiRequest(`/v1/subscriptions/${encodeURIComponent(id)}/poll`, {
        method: "POST", body: {},
      });
      const retry = result.retry_at ? ` YouTube retry after ${new Date(result.retry_at).toLocaleString()}.` : "";
      setNotice(`Check complete. ${result.new_count} new, ${result.attempted_count} attempted, ${result.pending_count} pending, ${result.ingesting_count} ingesting.${retry}`);
    } catch (failure) {
      setError(failure);
    } finally {
      subscriptions.refresh();
      refreshReleases();
      setBusy(null);
    }
  }

  return (
    <section className="panel" aria-labelledby="whats-new-subscriptions">
      <h2 className="section-title" id="whats-new-subscriptions">Subscriptions</h2>
      <SubscriptionForm busy={Boolean(busy)} onAdd={add} />
      <FriendlyError error={error} />
      <p className="lab-hint" role="status">{busy?.adding ? "Adding subscription…" : busy?.polling ? "Checking for releases…" : notice}</p>
      <FriendlyError error={subscriptions.error} title="Could not load subscriptions." onRetry={subscriptions.refresh} />
      {subscriptions.loading && !subscriptions.rows.length ? <p role="status">Loading subscriptions…</p> : null}
      {!subscriptions.loading && !subscriptions.error && !subscriptions.rows.length ? <p>No subscriptions yet.</p> : null}
      {subscriptions.rows.length ? (
        <ul className="whats-new-feeds">
          {subscriptions.rows.map((row) => {
            const identifier = row.kind === "youtube" ? new URL(row.url).searchParams.get("channel_id") || row.url : row.url;
            return (
              <li key={row.id}>
                <div>
                  <strong>{identifier}</strong>
                  <p className="lab-muted">
                    {[row.kind === "youtube" ? "YouTube" : "Podcast RSS", row.language === "ja" ? "Japanese" : "English",
                      row.domain_tag, row.auto_ingest ? "Auto-index on" : "Auto-index off"].filter(Boolean).join(" · ")}
                  </p>
                  <p className="lab-muted">
                    {[
                      row.title_include && `Include: ${row.title_include}`,
                      row.title_exclude && `Exclude: ${row.title_exclude}`,
                      row.min_duration_s > 0 && `Minimum duration: ${formatSeconds(row.min_duration_s)}`,
                    ].filter(Boolean).join(" · ")}
                  </p>
                </div>
                <button className="btn secondary" type="button" onClick={() => poll(row.id)} disabled={Boolean(busy)}>
                  {busy?.polling === row.id ? "Checking…" : "Check now"}
                </button>
              </li>
            );
          })}
        </ul>
      ) : null}
    </section>
  );
}

function Releases({ releases }) {
  const rows = [...releases.rows].sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  return (
    <section className="panel" aria-labelledby="whats-new-releases">
      <h2 className="section-title" id="whats-new-releases">Releases</h2>
      <FriendlyError error={releases.error} title="Could not load releases." onRetry={releases.refresh} />
      {releases.loading && !rows.length ? <p role="status">Loading releases…</p> : null}
      {!releases.loading && !releases.error && !rows.length ? <p>No releases yet. Check a subscription to find releases.</p> : null}
      {rows.length ? (
        <div className="lab-table-wrap" role="region" aria-label="Release history" tabIndex={0}>
          <table className="lab-table">
            <thead><tr><th scope="col">Release</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead>
            <tbody>
              {rows.map((row) => {
                const link = safeExternalUrl(row.link);
                const title = row.title || row.external_id;
                return (
                  <tr key={JSON.stringify([row.subscription_id, row.external_id])}>
                    <td>{link ? <a href={link} target="_blank" rel="noreferrer">{title}</a> : title}</td>
                    <td className="whats-new-release-status">{row.status}</td>
                    <td>{RELEASE_REASONS[row.reason] || row.reason || "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function dateInputValue(date) {
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${date.getFullYear()}-${month}-${day}`;
}

function defaultWindow() {
  const end = new Date();
  const start = new Date(end);
  start.setDate(start.getDate() - 6);
  return { start: dateInputValue(start), end: dateInputValue(end) };
}

function TimestampButton({ title, start, onPlay }) {
  return (
    <button className="btn secondary" type="button" onClick={(event) => onPlay(event.currentTarget)}
      aria-label={`Play ${title} at ${formatSeconds(start)}`}>
      {formatSeconds(start)}
    </button>
  );
}

function Highlight({ claim, release, digestId, onPlay }) {
  const [rating, setRating] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);
  const prior = claim.prior_passages.filter((row) => claim.prior.includes(row.passage_id));

  async function vote(value) {
    if (value === rating) return;
    setSaving(true);
    setError(null);
    try {
      await apiRequest(`/v1/digest/${encodeURIComponent(digestId)}/feedback`, {
        method: "POST", body: { item_id: claim.claim_id, rating: value },
      });
      setRating(value);
    } catch (failure) {
      setError(failure);
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="whats-new-highlight">
      <p><strong>{claim.text}</strong></p>
      {claim.reason ? <p>{claim.reason}</p> : null}
      <div className="whats-new-citation">
        <span>New release: {release.title} · lines {claim.evidence_lines.join(", ")}</span>
        <TimestampButton title={release.title} start={claim.start}
          onPlay={(button) => onPlay({ id: release.source_id, title: release.title, start: claim.start }, button)} />
      </div>
      {prior.map((row) => (
        <div className="whats-new-citation" key={row.passage_id}>
          <span>Prior episode: {row.title}</span>
          <TimestampButton title={row.title} start={row.start}
            onPlay={(button) => onPlay({ id: row.source_id, title: row.title, start: row.start }, button)} />
          <blockquote>{row.text}</blockquote>
        </div>
      ))}
      <div className="whats-new-feedback">
        {[["up", "👍", "Thumbs up"], ["down", "👎", "Thumbs down"]].map(([value, icon, label]) => (
          <button type="button" className="btn secondary" key={value} aria-label={label}
            aria-pressed={rating === value} disabled={saving} onClick={() => vote(value)}>
            <span aria-hidden="true">{icon}</span>
          </button>
        ))}
        <span className="lab-muted" role="status">{saving ? "Saving…" : rating ? "Feedback saved." : ""}</span>
      </div>
      <FriendlyError error={error} title="Could not save feedback." />
    </li>
  );
}

function DigestPlayer({ playback, onClose }) {
  const [video, setVideo] = useState(null);
  const [error, setError] = useState(null);
  const closeButton = useRef(null);
  const youtube = YOUTUBE_ID_PATTERN.test(playback.id);

  useEffect(() => { closeButton.current.focus(); }, [playback]);

  useEffect(() => {
    if (youtube) return;
    let cancelled = false;
    apiRequest("/v1/videos").then(({ videos }) => {
      if (cancelled) return;
      const source = videos.find((row) => row.video_id === playback.id);
      if (source) setVideo(source);
      else setError(new Error("This source is no longer in the library."));
    }).catch((failure) => {
      if (!cancelled) setError(failure);
    });
    return () => { cancelled = true; };
  }, [playback.id, youtube]);

  return (
    <div className="whats-new-player">
      <button ref={closeButton} className="btn secondary" type="button" onClick={onClose}>Close player</button>
      <FriendlyError error={error} title="Could not open playback." />
      {youtube || video ? (
        <PlayerPanel videoId={playback.id} title={playback.title} startSeconds={playback.start} video={video} />
      ) : !error ? <p role="status">Loading audio source…</p> : null}
    </div>
  );
}

function DigestReport({ digest }) {
  const [playback, setPlayback] = useState(null);
  const trigger = useRef(null);

  function closePlayer() {
    setPlayback(null);
    trigger.current?.focus();
  }

  function play(source, button) {
    trigger.current = button;
    setPlayback(source);
  }

  if (!digest.releases.length) return <p role="status">No indexed sources in this window.</p>;
  return (
    <div className="whats-new-results">
      {playback ? <DigestPlayer key={playback.id} playback={playback} onClose={closePlayer} /> : null}
      {digest.releases.map((release) => (
        <article className="whats-new-release" key={release.source_id}>
          <h3>{release.title}</h3>
          <p className="lab-muted">
            {[release.show, new Date(release.published_at).toLocaleDateString()].filter(Boolean).join(" · ")}
          </p>
          <h4>Highlights</h4>
          {!release.highlights.length ? <p>No highlights for this release.</p> : null}
          {HIGHLIGHT_GROUPS.map(([relation, label]) => {
            const claims = release.highlights.filter((claim) => claim.relation === relation);
            if (!claims.length) return null;
            return (
              <div className="whats-new-group" key={relation}>
                <h5>{label}</h5>
                <ul>
                  {claims.map((claim) => <Highlight key={claim.claim_id} claim={claim} release={release}
                    digestId={digest.digest_id} onPlay={play} />)}
                </ul>
              </div>
            );
          })}
          <p className="lab-muted">Covered by prior episodes: {release.covered_count}</p>
        </article>
      ))}
    </div>
  );
}

function Digest() {
  const [windowDates, setWindowDates] = useState(defaultWindow);
  const [digest, setDigest] = useState(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function build(event) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const start = new Date(`${windowDates.start}T00:00:00`);
    const end = new Date(`${windowDates.end}T00:00:00`);
    end.setDate(end.getDate() + 1);
    setBusy(true);
    setError(null);
    try {
      const result = await apiRequest("/v1/digest/whats-new", {
        method: "POST",
        body: { window: { start: start.toISOString(), end: end.toISOString() }, language: data.get("language") },
      });
      setDigest(result.digest);
    } catch (failure) {
      setError(failure);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel" aria-labelledby="whats-new-digest">
      <h2 className="section-title" id="whats-new-digest">Digest</h2>
      <p className="section-desc">Compare sources indexed in your library with earlier episodes. Dates use your local time zone.</p>
      <form onSubmit={build}>
        <fieldset className="grid whats-new-form" disabled={busy}>
          <label>Start date<input type="date" value={windowDates.start} required
            onChange={(event) => setWindowDates({ ...windowDates, start: event.target.value })} /></label>
          <label>End date (inclusive)<input type="date" value={windowDates.end} min={windowDates.start} required
            onChange={(event) => setWindowDates({ ...windowDates, end: event.target.value })} /></label>
          <label>
            Output language
            <select name="language">
              <option value="en">English</option>
              <option value="ja">Japanese</option>
            </select>
          </label>
          <button className="btn" type="submit">Build digest</button>
        </fieldset>
      </form>
      {busy ? <p role="status">Building digest…</p> : null}
      <FriendlyError error={error} title="Could not build digest." />
      {digest ? <DigestReport key={digest.digest_id} digest={digest} /> : null}
    </section>
  );
}

export function WhatsNewPage() {
  const subscriptions = useRows("/v1/subscriptions", "subscriptions");
  const releases = useRows("/v1/releases", "releases", subscriptions.rows);
  return (
    <div className="whats-new-page">
      <h1>What's new</h1>
      <Subscriptions subscriptions={subscriptions} refreshReleases={releases.refresh} />
      <Releases releases={releases} />
      <Digest />
    </div>
  );
}
