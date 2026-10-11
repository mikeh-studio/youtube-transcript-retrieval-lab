// Bootstrap a browser session before every same-origin mutation. Never retry writes.
(() => {
  const originalFetch = window.fetch.bind(window);
  let session;
  window.fetch = async (input, init = {}) => {
    const request = new Request(input, init);
    if (new URL(request.url).origin === location.origin &&
        !["GET", "HEAD", "OPTIONS"].includes(request.method)) {
      session ||= originalFetch("/browser-session", {cache: "no-store", credentials: "same-origin"})
        .then(async response => {
          if (!response.ok) throw new Error("Could not establish browser session");
          return (await response.json()).csrf_token;
        }).catch(error => { session = undefined; throw error; });
      const headers = new Headers(request.headers);
      const token = await session;
      session = undefined; // Refresh before the next action; never replay a write.
      headers.set("X-CSRF-Token", token);
      return originalFetch(new Request(request, {headers, credentials: "same-origin"}));
    }
    return originalFetch(request);
  };
})();
