import { expect, test } from "./fixtures";

const VIDEOS = [
  {
    video_id: "TWbQXYQMCxg",
    title: "Frieren Radio Episode 3",
    url: "https://www.youtube.com/watch?v=TWbQXYQMCxg",
    language: "ja",
    num_chunks: 123,
    source: { platform: "youtube", channel: { name: "Anime Radio" } },
  },
  {
    video_id: "abcdefghijk",
    title: "Building useful AI products",
    url: "https://www.youtube.com/watch?v=abcdefghijk",
    language: "en",
    num_chunks: 40,
    chunking_stale: true,
    source: { platform: "youtube", channel: { name: "Tech Talks" } },
  },
];

const CATALOG_ITEM = {
  id: "8f14e45f-ceea-467a-9575-6b2c4f1d2a01",
  external_id: "TWbQXYQMCxg",
  url: "https://www.youtube.com/watch?v=TWbQXYQMCxg",
  qualification: "eligible",
  metadata: {},
  missing_fields: {},
  labels: [{ vocabulary: "library", version: "v1", key: "anime", namespace: "topic", en: "Anime", ja: "アニメ" }],
  readiness: { state: "preconditions_met", blockers: [], expires_at: null },
  searchable: "not_checked",
  first_seen: null,
  last_seen: null,
};

async function mockLibrary(page, { catalog = true } = {}) {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("**/v1/videos", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ ok: true, videos: VIDEOS }),
  }));
  await page.route(/\/v1\/catalog\/items/, (route) => (catalog
    ? route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ generated_at: "2026-10-14T00:00:00Z", total: 1, limit: 100, offset: 0, scope: "private_personal_search", items: [CATALOG_ITEM] }),
    })
    : route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"unavailable"}' })));
}

test("lists indexed videos with corpus stats and catalog status", async ({ page }) => {
  await mockLibrary(page);
  await page.goto("/index.html#/ingest");

  const stats = page.getByLabel("Library summary");
  await expect(stats).toContainText("2Indexed videos");
  await expect(stats).toContainText("163Searchable chunks");
  await expect(stats).toContainText("1 of 2Linked to catalog");

  const frieren = page.locator('tr[data-video-id="TWbQXYQMCxg"]');
  await expect(frieren.getByRole("link", { name: "Frieren Radio Episode 3" })).toHaveAttribute(
    "href",
    "https://www.youtube.com/watch?v=TWbQXYQMCxg",
  );
  await expect(frieren.locator(".lab-thumb img")).toHaveAttribute("src", /i\.ytimg\.com\/vi\/TWbQXYQMCxg\/hqdefault\.jpg/);
  await expect(frieren).toContainText("Anime Radio");
  await expect(frieren).toContainText("Eligible");
  await expect(frieren).toContainText("Anime");
  await expect(page.locator('tr[data-video-id="abcdefghijk"]')).toContainText("Not in catalog");
  await expect(page.locator('tr[data-video-id="abcdefghijk"]')).toContainText("older chunking");

  await page.getByLabel("Filter by language").selectOption("en");
  await expect(page.locator("tbody tr")).toHaveCount(1);
  await page.getByLabel("Filter by language").selectOption("");
  await page.getByLabel("Filter videos").fill("frieren");
  await expect(page.locator("tbody tr")).toHaveCount(1);
});

test("hides the catalog column when the catalog service is not connected", async ({ page }) => {
  await mockLibrary(page, { catalog: false });
  await page.goto("/index.html#/ingest");

  await expect(page.getByLabel("Library summary")).toContainText("Catalog not connected");
  await expect(page.locator("thead")).not.toContainText("Catalog");
});

test("opens Search scoped to the chosen video", async ({ page }) => {
  await mockLibrary(page);
  let body = null;
  await page.route("**/v1/search", async (route) => {
    body = route.request().postDataJSON();
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, results: [], result_count: 0, retrieval_details: {} }) });
  });
  await page.route(/\/v1\/feedback\/search-review/, (route) => route.fulfill({ contentType: "application/json", body: '{"reviews":[]}' }));
  await page.goto("/index.html#/ingest");

  await page.locator('tr[data-video-id="TWbQXYQMCxg"]').getByRole("button", { name: "Search" }).click();
  await expect(page).toHaveURL(/#\/qa$/);
  await expect(page.getByLabel("Scope")).toHaveValue("videos");
  await page.getByLabel("Query").fill("作画監督");
  await page.getByRole("button", { name: "Run A and B" }).click();
  await expect.poll(() => body?.video_ids).toEqual(["TWbQXYQMCxg"]);
});

test("indexes a video URL from the Add to library form", async ({ page }) => {
  await mockLibrary(page);
  let ingestBody = null;
  await page.route("**/v1/ingest/videos", async (route) => {
    ingestBody = route.request().postDataJSON();
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ ok: true, queued_count: 1, skipped_count: 0, jobs: [{ video_id: "TWbQXYQMCxg", status: "completed" }] }),
    });
  });
  await page.goto("/index.html#/ingest");

  await page.getByRole("radio", { name: "Playlist" }).click();
  await page.getByLabel("YouTube playlist URL").fill("https://www.youtube.com/playlist?list=PL123");
  await page.getByRole("button", { name: "Index", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("Indexed 1 video");
  expect(ingestBody).toMatchObject({ url: "https://www.youtube.com/playlist?list=PL123", mode: "playlist", language: "ja" });
});
