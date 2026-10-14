import { expect, test } from "./fixtures";

test("plays cinematic intro only once per browser session", async ({ page }) => {
  await page.goto("/index.html#/ingest");

  const introOverlay = page.locator("[data-testid='intro-overlay']");
  await expect(introOverlay).toHaveCount(1);
  await introOverlay.waitFor({ state: "detached", timeout: 5000 });

  await page.reload();
  await expect(page.locator("[data-testid='intro-overlay']")).toHaveCount(0);
});

test.describe("mobile bottom tabs", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("shows tab bar and supports route tab switching when unlocked", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("yt_rag_ingest_unlocked", "1");
    });

    await page.goto("/index.html#/ingest");

    const tabBar = page.locator(".bottom-tabbar");
    await expect(tabBar).toBeVisible();
    await expect(tabBar.locator(".bottom-tab-link")).toHaveCount(5);

    await tabBar.getByRole("button", { name: "Search" }).click();
    await expect(page).toHaveURL(/#\/qa$/);
    await expect(tabBar.locator(".bottom-tab-link.active")).toContainText("Search");
  });
});

const PRIMARY_LABELS = ["Catalog", "Library", "Search", "Evaluate", "Tools"];
const TOOLS_LABELS = ["Chunking", "Data quality", "Search feedback", "Local video", "Study"];

async function expectLabNav(nav) {
  const directItems = nav.locator(":scope > .app-nav-link, :scope > .nav-tools-menu > summary");
  await expect(directItems).toHaveText(PRIMARY_LABELS);

  const toolsMenu = nav.locator('[data-nav-group="tools"]');
  await toolsMenu.evaluate((element) => element.setAttribute("open", ""));
  await expect(toolsMenu.locator(".nav-tools-link")).toHaveText(TOOLS_LABELS);
}

test("lab navigation is identical across pages and marks the current page", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });

  const pages = [
    { url: "/index.html#/ingest", active: "Library" },
    { url: "/evaluation.html", active: "Evaluate" },
    { url: "/reviews.html", active: "Tools", tool: "Search feedback" },
    { url: "/evidence.html", active: "Tools", tool: "Data quality" },
    { url: "/chunking.html", active: "Tools", tool: "Chunking" },
  ];
  for (const { url, active, tool } of pages) {
    await page.goto(url);
    const nav = page.locator(".appbar-nav");
    await expectLabNav(nav);
    await expect(nav.locator(":scope > .active, :scope > .nav-tools-menu > summary.active")).toHaveText([active]);
    if (tool) {
      await expect(nav.locator(".nav-tools-link.active")).toHaveText([tool]);
    }
    await expect(page.locator(".appbar .eyebrow")).toHaveText("Retrieval Lab");
  }
});

test("hides lab pages until a video is indexed", async ({ page }) => {
  await page.goto("/index.html#/ingest");
  const nav = page.locator(".appbar-nav");
  await expect(nav.locator(":scope > .app-nav-link, :scope > .nav-tools-menu > summary")).toHaveText([
    "Catalog",
    "Library",
    "Tools",
  ]);
  await expect(page.getByRole("heading", { name: "Library", level: 1 })).toBeVisible();
});

test("keeps Search separate from the secondary Study tool", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.goto("/index.html#/qa");

  await expect(page.getByRole("heading", { name: "Search", level: 1 })).toBeVisible();
  await expect(page.getByRole("tablist", { name: "Study tool" })).toHaveCount(0);

  await page.goto("/index.html#/study");
  const studyTabs = page.getByRole("tablist", { name: "Study tool" });
  await expect(studyTabs.getByRole("tab")).toHaveText(["Study", "Summary"]);
  await expect(studyTabs.getByRole("tab", { name: "Study" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator('.appbar-nav [data-nav-group="tools"] > summary')).toHaveClass(/active/);

  await studyTabs.getByRole("tab", { name: "Summary" }).click();
  await expect(page).toHaveURL(/#\/tldr$/);
});

test("shows supported speaker context on generated study flashcards", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("**/v1/videos", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        videos: [
          {
            video_id: "curry-demo",
            title: "The Stephen Curry Interview",
            language: "en",
            num_chunks: 12,
          },
        ],
      }),
    });
  });
  await page.route("**/v1/llm-options", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ providers: [] }) });
  });
  await page.route("**/v1/study/generate", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        mode: "flashcards",
        video_id: "curry-demo",
        video_title: "The Stephen Curry Interview",
        provider: "local",
        model: "deterministic-learning-cards",
        source: { segment_count: 12, chunk_count: 12 },
        focus: { preset_label: "Main ideas", model_profile_label: "Balanced" },
        evidence_pack: { section_count: 5, selected_section_count: 5, selected_sections: [] },
        deck: {
          cards: [
            {
              card_type: "recall",
              question: "How did Stephen Curry describe rebuilding confidence?",
              answer: "He treated preparation as a way to regain trust in his game.",
              explanation: "The section connects preparation with confidence.",
              learning_objective: "Recall Curry's preparation mindset.",
              why_it_matters: "It connects a setback to a repeatable response.",
              source_cue: "Stephen Curry explains how preparation restored confidence.",
              speaker: "Stephen Curry",
              speaker_role: "interview subject",
              speaker_confidence: "named_in_section",
              tags: ["leadership"],
              evidence: {
                video_id: "curry-demo",
                video_title: "The Stephen Curry Interview",
                start: 60,
                timestamp: "1:00",
                url: "https://www.youtube.com/watch?v=curry-demo&t=60s",
              },
            },
          ],
        },
      }),
    });
  });

  await page.goto("/index.html#/study");
  await page.getByRole("button", { name: "Generate Flashcards" }).click();

  const speaker = page.locator(".study-speaker");
  await expect(speaker).toContainText("Speaker: Stephen Curry · interview subject");
  await expect(speaker).toContainText("Supported by section context");
});

test("routes All videos before Ask and shows YouTube channel provenance", async ({ page }) => {
  let askBody: Record<string, unknown> | null = null;
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("**/v1/videos", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        videos: [
          {
            video_id: "bread-demo1",
            title: "Sourdough Timing",
            language: "en",
            num_chunks: 8,
          },
        ],
      }),
    });
  });
  await page.route("**/v1/llm-options", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ providers: [] }) });
  });
  await page.route("**/v1/ask", async (route) => {
    askBody = route.request().postDataJSON();
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        question: "When should I feed the starter?",
        status: "answered",
        answer: "Feed it after peak fermentation [1].",
        confidence: "high",
        provider: "chatgpt",
        model: "test-model",
        warnings: [],
        retrieval_details: {
          video_routing: {
            enabled: true,
            selected_video_ids: ["bread-demo1"],
          },
        },
        citations: [
          {
            citation_id: 1,
            source_type: "transcript",
            video_id: "bread-demo1",
            video_title: "Sourdough Timing",
            chunk_index: 2,
            start_seconds: 65,
            end_seconds: 80,
            timestamp_range_label: "1:05-1:20",
            snippet: "Feed the starter after peak fermentation.",
            url: "https://www.youtube.com/watch?v=bread-demo1&t=65s",
            source: {
              platform: "youtube",
              video_id: "bread-demo1",
              url: "https://www.youtube.com/watch?v=bread-demo1",
              channel: {
                id: "UC-bread",
                name: "Bread Lab",
                url: "https://www.youtube.com/channel/UC-bread",
              },
            },
          },
        ],
      }),
    });
  });

  await page.goto("/index.html#/qa");
  await page.getByLabel("Video scope").selectOption("");
  await page.getByLabel("Question").fill("When should I feed the starter?");
  await page.getByRole("button", { name: "Generate Answer" }).click();

  await expect.poll(() => askBody).not.toBeNull();
  expect(askBody).toMatchObject({
    video_routing: "multi_vector",
    video_top_k: 3,
  });
  await expect(page.getByText("YouTube · Bread Lab")).toBeVisible();
  await expect(page.getByRole("link", { name: "Video", exact: true })).toHaveAttribute(
    "href",
    "https://www.youtube.com/watch?v=bread-demo1",
  );
  await expect(page.getByRole("link", { name: "Channel", exact: true })).toHaveAttribute(
    "href",
    "https://www.youtube.com/channel/UC-bread",
  );
});
