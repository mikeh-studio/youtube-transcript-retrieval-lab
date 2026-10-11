# Browser request boundary

Run the UI from the Python server itself. The shipped HTML and React entry point load
`browser-session.js`, which obtains a session cookie and CSRF token and attaches the
token to every same-origin mutation. API clients must GET `/browser-session`, retain
the cookie, then send `Origin: http://127.0.0.1:<server-port>`, the returned
`X-CSRF-Token`, and `Content-Type: application/json` for POST/PATCH/DELETE requests.
Tokens expire after 12 hours and on server restart; the client refreshes the session before each action.
Never blindly retry a mutation. Logout uses POST.

The boundary checks Host including the actual listening port, Origin, Fetch Metadata,
duplicate security headers, and tokens before dispatch. JSON is the only supported
mutation format; multipart and WebSocket upgrades are rejected. No forwarded headers
are trusted. CORS does not grant other local ports access. Serve built or legacy UI
through the backend, rather than accessing the API from a separate development origin.

Retrieval remains loopback-only. Binding to a remote address or adding a proxy does
not enable remote access. A future shared deployment needs explicit authentication
and trusted-authority configuration before this boundary can support it.

Validation: `python -m unittest discover -s tests -p test_browser_boundary.py` runs real
HTTP requests against the actual route handler, stubbing only provider/disk mutations.
It proves rejected requests never dispatch and valid sessions dispatch once; it does
not claim live provider ingestion or deployed proxy validation.
