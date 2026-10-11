import { test as base, expect } from "@playwright/test";

// These UI-only tests mock API responses, including session bootstrap. Actual
// cookie/origin enforcement is exercised by tests/test_browser_boundary.py.
export const test = base.extend({
  page: async ({ page }, use) => {
    await page.route("**/browser-session", route => route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ csrf_token: "ui-test-session-token" }),
    }));
    const mutationTokens: (string | undefined)[] = [];
    page.on("request", request => {
      if (new URL(request.url()).pathname.startsWith("/v1/") &&
          !["GET", "HEAD", "OPTIONS"].includes(request.method())) {
        mutationTokens.push(request.headers()["x-csrf-token"]);
      }
    });
    await use(page);
    for (const token of mutationTokens) expect(token).toBe("ui-test-session-token");
  },
});
export { expect };
