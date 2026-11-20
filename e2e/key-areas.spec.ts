import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const VIDEOS = [
  { video_id: "clip0000001", title: "Paper lantern workshop", language: "en", num_chunks: 4 },
  { video_id: "clip0000002", title: "Folded lantern notes", language: "ja", num_chunks: 3 },
];

async function mockLibrary(page: Page, videos = VIDEOS) {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/v1/videos", (route) => route.fulfill({ json: { ok: true, videos } }));
  await page.route(/\/v1\/catalog\/items/, (route) => route.fulfill({ status: 503, json: { error: "Unavailable" } }));
  await page.route("**/v1/llm-options", (route) => route.fulfill({
    json: {
      ok: true,
      providers: {
        chatgpt: { default: "fixture-default", models: [] },
        claude: { default: "fixture-default", models: [{ id: "fixture-model", label: "Fixture model" }] },
      },
    },
  }));
}

test("generates key areas with a duration-weighted timeline and cited playback", async ({ page }) => {
  await mockLibrary(page);
  await page.route("https://www.youtube.com/embed/**", (route) => route.fulfill({
    contentType: "text/html", body: "<p>Player fixture</p>",
  }));
  const overview = "<img src=x> Fold the paper before hanging it.";
  await page.route("**/v1/summaries/key-areas", (route) => route.fulfill({
    json: {
      ok: true,
      overview: { tldr: overview },
      quality: { citation_validity: 0.75, section_coverage: 1, repaired_boundaries: 2 },
      sections: [
        {
          start_line: 1, end_line: 2, start: 0, end: 30,
          title: "Folding", summary: "Start with a square.",
          key_points: [{ text: "Crease the corners.", start: 12, end: 18, speakers: ["Mira"] }],
        },
        {
          start_line: 3, end_line: 5, start: 30, end: 120,
          title: "Hanging", summary: "Use a loose loop.",
          key_points: [{ text: "Leave room for the hook.", start: 32, end: 44, speakers: [] }],
        },
      ],
    },
  }));

  await page.goto("/index.html#/study");
  await page.getByRole("tab", { name: "Key areas" }).click();
  await expect(page).toHaveURL(/#\/key-areas$/);
  const videoPicker = page.getByRole("combobox", { name: "Video", exact: true });
  await videoPicker.selectOption(VIDEOS[1].video_id);
  await page.getByRole("combobox", { name: "Output language", exact: true }).selectOption("ja");
  await page.getByRole("combobox", { name: "Provider", exact: true }).selectOption("claude");
  await page.getByRole("combobox", { name: "Model", exact: true }).selectOption("fixture-model");
  const request = page.waitForRequest((req) => req.url().endsWith("/v1/summaries/key-areas"));
  await page.getByRole("button", { name: "Generate key areas" }).click();
  expect((await request).postDataJSON()).toEqual({
    source_id: VIDEOS[1].video_id, language: "ja", provider: "claude", model: "fixture-model",
  });

  await expect(page.getByText(overview, { exact: true })).toBeVisible();
  await expect(page.locator('img[src="x"]')).toHaveCount(0);
  await expect(page.getByText("Citation validity 75% · Transcript line coverage 100% · Repaired boundaries 2")).toBeVisible();
  const timeline = page.getByRole("group", { name: "Section timeline" });
  const widths = await timeline.getByRole("button").evaluateAll((buttons) => buttons.map((button) => button.getBoundingClientRect().width));
  expect(widths[1] / widths[0]).toBeCloseTo(3, 1);
  const speakerChips = page.locator(".summary-speaker");
  await expect(speakerChips).toHaveText(["Mira"]);
  await expect(page.getByText("Leave room for the hook.")).toBeVisible();

  const player = page.getByTitle("YouTube player");
  await timeline.getByRole("button", { name: /Hanging/ }).click();
  await expect(player).toHaveAttribute("src", new RegExp(`/embed/${VIDEOS[1].video_id}\\?start=30&`));
  await page.getByRole("button", { name: "0:12", exact: true }).click();
  await expect(player).toHaveAttribute("src", new RegExp(`/embed/${VIDEOS[1].video_id}\\?start=12&`));
  await videoPicker.selectOption(VIDEOS[0].video_id);
  await expect(player).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "Overview" })).toHaveCount(0);
});

test("keeps Library selections across filters and renders cited collection perspectives", async ({ page }) => {
  await mockLibrary(page);
  const firstUrl = `https://www.youtube.com/watch?v=${VIDEOS[0].video_id}&t=12s`;
  const secondUrl = `https://www.youtube.com/watch?v=${VIDEOS[1].video_id}&t=65s`;
  await page.route("**/v1/summaries/collection", (route) => route.fulfill({
    json: {
      ok: true,
      source_ids: VIDEOS.map((video) => video.video_id),
      overview: "Two ways to hang a lantern.",
      themes: [{
        title: "Fastening a lantern", summary: "Both designs use a loop.",
        perspectives: [
          { source_id: VIDEOS[1].video_id, stance: "differs", statement: "Tie a separate loop.", citations: ["E2"] },
          { source_id: VIDEOS[0].video_id, stance: "agrees", statement: "Use the folded loop.", citations: ["E1"] },
        ],
      }],
      evidence_catalog: [
        { citation_id: "E1", video_id: VIDEOS[0].video_id, start_seconds: 12, end_seconds: 18, url: firstUrl },
        { citation_id: "E2", video_id: VIDEOS[1].video_id, start_seconds: 65, end_seconds: 72, url: secondUrl },
      ],
      coverage: { sources_selected: 2, sources_cited: 2, citation_validity: 0.8 },
    },
  }));

  await page.goto("/index.html#/ingest");
  const summarize = page.getByRole("button", { name: "Summarize selection" });
  await expect(summarize).toBeDisabled();
  await page.getByRole("checkbox", { name: `Select ${VIDEOS[0].title}` }).check();
  await expect(summarize).toBeDisabled();
  await page.getByLabel("Filter by language").selectOption("ja");
  await page.getByRole("checkbox", { name: `Select ${VIDEOS[1].title}` }).check();
  await summarize.click();

  await expect(page).toHaveURL(/#\/collection$/);
  await expect(page.getByRole("heading", { name: "Collection summary" })).toBeVisible();
  await page.reload();
  await expect(page.getByText(VIDEOS[0].title, { exact: true })).toBeVisible();
  await expect(page.getByText(VIDEOS[1].title, { exact: true })).toBeVisible();

  const request = page.waitForRequest((req) => req.url().endsWith("/v1/summaries/collection"));
  await page.getByRole("button", { name: "Generate collection summary" }).click();
  expect((await request).postDataJSON()).toEqual({
    source_ids: VIDEOS.map((video) => video.video_id), language: "en", provider: "chatgpt",
  });

  const theme = page.getByRole("article").filter({ hasText: "Fastening a lantern" });
  await expect(theme.getByRole("heading", { level: 4 })).toHaveText(["Agrees", "Differs"]);
  await expect(theme.getByText("Use the folded loop.")).toBeVisible();
  await expect(theme.getByRole("link", { name: `${VIDEOS[0].title} at 0:12` })).toHaveAttribute("href", firstUrl);
  await expect(theme.getByRole("link", { name: `${VIDEOS[1].title} at 1:05` })).toHaveAttribute("href", secondUrl);
  await expect(page.getByText("Sources cited 2 of 2 · Citation validity 80%")).toBeVisible();
});

test("caps the collection selection at twelve videos", async ({ page }) => {
  const videos = Array.from({ length: 13 }, (_, index) => ({
    ...VIDEOS[0], video_id: `extra${String(index).padStart(6, "0")}`, title: `Lantern plan ${index}`,
  }));
  await mockLibrary(page, videos);
  await page.goto("/index.html#/ingest");
  const boxes = page.getByRole("checkbox", { name: /^Select Lantern plan/ });
  await expect(boxes).toHaveCount(13);
  for (let index = 0; index < 12; index++) {
    await boxes.nth(index).check();
  }
  await expect(boxes.nth(12)).toBeDisabled();
  await expect(page.getByText("12 selected · Choose 2–12 sources")).toBeVisible();
  await boxes.first().uncheck();
  await expect(boxes.nth(12)).toBeEnabled();
});

test("provides a Library link when the collection route has no selection", async ({ page }) => {
  await mockLibrary(page);
  await page.goto("/index.html#/collection");
  await expect(page.getByText(/Select 2–12 indexed videos/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Generate collection summary" })).toHaveCount(0);
  await page.getByRole("link", { name: "Back to Library" }).click();
  await expect(page).toHaveURL(/#\/ingest$/);
});
