import { expect, test } from "./fixtures";

test("clear reviews sends JSON content type with its bodyless DELETE", async ({ page }) => {
  await page.route("**/v1/feedback/videos", route => route.fulfill({json: {videos: []}}));
  let deleted = false;
  await page.route("**/v1/feedback/search-review?*", async route => {
    if (route.request().method() === "DELETE") {
      expect(route.request().headers()["content-type"]).toBe("application/json");
      expect(route.request().headers()["x-csrf-token"]).toBe("ui-test-session-token");
      expect(route.request().postData()).toBeNull();
      deleted = true;
      await route.fulfill({json: {ok: true, deleted_count: 1}});
    } else {
      await route.fulfill({json: {reviews: []}});
    }
  });
  await page.addInitScript(() => localStorage.setItem("yt_rag_ingest_unlocked", "1"));
  await page.goto("/reviews.html");
  await page.locator("#filterLabel").selectOption("relevant");
  page.once("dialog", dialog => dialog.accept());
  await page.locator("#clearReviewsBtn").click();
  await expect.poll(() => deleted).toBe(true);
});
