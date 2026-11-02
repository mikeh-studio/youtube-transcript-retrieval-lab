# Security and privacy

This is a local, single-user research app. Serve it through the Python backend
on loopback; it is not an authenticated public web service. See
[the browser boundary](BROWSER_SECURITY.md) for Host, Origin, session, and CSRF
checks. Do not expose it through a public tunnel or proxy without a separate
deployment security review.

Keep provider keys in the environment or an ignored `.env.local`. Indexed
transcripts, local video files, generated reports, and runtime logs belong in
ignored `data/` or `reports/` directories. Report imports use an allowlist and
redact common credential formats and local paths; review the content before
sharing it. This redaction is not a general personal-information classifier.
Internal API failures return a generic message; detailed tracebacks remain in
local server logs, which should also be reviewed before sharing.

Use a fresh virtual environment, upgrade pip, then `pip install -r requirements.txt`.
`requirements-security.txt` supplies audited version floors, and CI checks
installed Python packages with `pip-audit` and the frontend with `npm audit`.
An advisory scan covers known dependency issues, not every possible flaw.
Only load indexes and model artifacts from sources you trust.

Before publishing, inspect the staged files, screenshot content and metadata,
and Git author/committer identities. Ignoring or deleting a file does not remove
it from history. Prefer GitHub's private noreply address for future commits.
If a credential was published, revoke it before considering history cleanup.

Report a suspected vulnerability through the repository's private security
advisory channel when available. Do not put credentials or private transcripts
in a public issue.
