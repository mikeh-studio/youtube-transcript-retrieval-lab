import React, { useEffect, useState } from "react";
import { apiRequest } from "./lab.jsx";

export const PREFERENCES_KEY = "youtube-rag-workspace-preferences-v1";
export const DEFAULT_PREFERENCES = {
  provider: "chatgpt",
  model: "",
  max_output_tokens: 500,
  language: "match",
  response_length: "concise",
};
export function readPreferences() {
  try {
    return {
      ...DEFAULT_PREFERENCES,
      ...JSON.parse(localStorage.getItem(PREFERENCES_KEY) || "{}"),
    };
  } catch (_) {
    return { ...DEFAULT_PREFERENCES };
  }
}

export function SettingsPage() {
  const [saved, setSaved] = useState(readPreferences);
  const [draft, setDraft] = useState(saved);
  const [section, setSection] = useState("Providers & costs");
  const [status, setStatus] = useState("");
  const [server, setServer] = useState(null);
  useEffect(() => {
    apiRequest("/v1/workspace/settings")
      .then(setServer)
      .catch(() => setServer(null));
  }, []);
  const update = (key, value) => {
    setDraft((prev) => ({ ...prev, [key]: value }));
    setStatus("");
  };
  function save() {
    try {
      localStorage.setItem(PREFERENCES_KEY, JSON.stringify(draft));
      setSaved(draft);
      setStatus("Defaults saved on this browser.");
    } catch (_) {
      setStatus("Could not save defaults in this browser.");
    }
  }
  return (
    <section className="workspace-settings">
      <aside>
        <h1>Settings</h1>
        <p>Defaults for future searches and answers.</p>
        {["General", "Providers & costs", "Retrieval", "Data & storage"].map(
          (name) => (
            <button
              type="button"
              className={section === name ? "active" : ""}
              onClick={() => setSection(name)}
              key={name}
            >
              {name}
            </button>
          ),
        )}
      </aside>
      <div className="workspace-settings-content">
        <h1>{section}</h1>
        {section === "Providers & costs" ? (
          <>
            <h2>Answer provider</h2>
            <label>
              Default provider
              <select
                value={draft.provider}
                onChange={(e) => update("provider", e.target.value)}
              >
                <option value="chatgpt">OpenAI</option>
                <option value="claude">Anthropic</option>
                <option value="sakana">Sakana</option>
              </select>
            </label>
            <label>
              Model
              <input
                value={draft.model}
                onChange={(e) => update("model", e.target.value)}
                placeholder="Use configured default"
              />
            </label>
            <p>
              Credential state:{" "}
              {server?.providers?.find((p) => p.id === draft.provider)
                ?.configured === true
                ? "Configured"
                : server?.providers?.find((p) => p.id === draft.provider)
                      ?.configured === false
                  ? "Not configured"
                  : "Not checked"}
              . Credentials are read from local configuration.
            </p>
            <details>
              <summary>Setup instructions</summary>
              <p>
                Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or SAKANA_API_KEY in your
                local environment before starting the backend. Restart the
                backend after changing a key. Credentials are never entered or
                saved on this page.
              </p>
            </details>
            <h2>Before spending</h2>
            <p>
              Review an estimate before generation. If cost is unavailable,
              acknowledgement is required. Generation never starts
              automatically.
            </p>
            <h2>Answer defaults</h2>
            <label>
              Output language
              <select
                value={draft.language}
                onChange={(e) => update("language", e.target.value)}
              >
                <option value="match">Match the question</option>
                <option value="en">English</option>
                <option value="ja">Japanese</option>
              </select>
            </label>
            <label>
              Response length
              <select
                value={draft.response_length}
                onChange={(e) => {
                  update("response_length", e.target.value);
                  update(
                    "max_output_tokens",
                    e.target.value === "detailed" ? 1000 : 500,
                  );
                }}
              >
                <option value="concise">Concise</option>
                <option value="detailed">Detailed</option>
              </select>
            </label>
            <p>
              Local transcript search does not require an answer-provider call.
            </p>
          </>
        ) : section === "Retrieval" ? (
          <>
            <p>
              Search settings are chosen in Advanced. Index experiments change
              the indexed representation and can invalidate passage identities.
            </p>
            <a href="/chunking.html">Open index experiments</a>
            <p>
              <a href="/reviews.html">Search feedback</a>
            </p>
          </>
        ) : section === "Data & storage" ? (
          <>
            <p>
              Preferences are stored in this browser. Existing saved query sets
              and runs are preserved.
            </p>
            <a href="/evidence.html">Data quality</a>
          </>
        ) : (
          <>
            <p>Manage your collection and other tools.</p>
            <p>
              <a href="/catalog.html">Catalog metadata</a> ·{" "}
              <a href="/index.html#/local-video">Local files and OCR</a> ·{" "}
              <a href="/index.html#/study">Study</a>
            </p>
          </>
        )}
        <footer>
          <span>
            Changes apply to future runs. Saved evidence and results stay
            unchanged.
          </span>
          <button
            className="btn secondary"
            type="button"
            onClick={() => {
              setDraft(saved);
              setStatus("Changes cancelled.");
            }}
          >
            Cancel
          </button>
          <button className="btn" type="button" onClick={save}>
            Save defaults
          </button>
        </footer>
        {status ? <p role="status">{status}</p> : null}
      </div>
    </section>
  );
}
