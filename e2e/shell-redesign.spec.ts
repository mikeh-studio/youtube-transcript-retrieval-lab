import { expect, test } from "./fixtures";

test("plays cinematic intro only once per browser session", async ({
  page,
}) => {
  await page.addInitScript(() => {
    (window as any).__introObserved = false;
    new MutationObserver(() => {
      if (document.querySelector("[data-testid='intro-overlay']"))
        (window as any).__introObserved = true;
    }).observe(document, { childList: true, subtree: true });
  });
  await page.goto("/index.html#/ingest");
  await expect
    .poll(() => page.evaluate(() => (window as any).__introObserved))
    .toBe(true);
  const introOverlay = page.locator("[data-testid='intro-overlay']");
  await introOverlay.waitFor({ state: "detached", timeout: 5000 });

  await page.evaluate(() => sessionStorage.setItem("yt_rag_intro_seen", "1"));
  await page.reload();
  await expect(page.locator("[data-testid='intro-overlay']")).toHaveCount(0);
});

test.describe("mobile bottom tabs", () => {
  test.use({ viewport: { width: 390, height: 844 } });

  test("shows tab bar and supports route tab switching when unlocked", async ({
    page,
  }) => {
    await page.addInitScript(() => {
      localStorage.setItem("yt_rag_ingest_unlocked", "1");
    });

    await page.goto("/index.html#/ingest");

    const tabBar = page.locator(".bottom-tabbar");
    await expect(tabBar).toBeVisible();
    await expect(tabBar.locator(".bottom-tab-link")).toHaveCount(4);

    await tabBar.getByRole("button", { name: "Search" }).click();
    await expect(page).toHaveURL(/#\/qa$/);
    await expect(tabBar.locator(".bottom-tab-link.active")).toContainText(
      "Search",
    );
  });
});

const PRIMARY_LABELS = ["Library", "Search", "Evaluation", "Settings"];

test("shared navigation exposes both workspaces and utilities", async ({
  page,
}) => {
  await page.addInitScript(() => {
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  for (const url of [
    "/index.html#/ingest",
    "/evaluation.html",
    "/reviews.html",
    "/evidence.html",
    "/chunking.html",
  ]) {
    await page.goto(url);
    await expect(page.locator(".appbar-nav > .app-nav-link")).toHaveText(
      PRIMARY_LABELS,
    );
  }
});

test("Search and Settings remain accessible before indexing", async ({
  page,
}) => {
  await page.route("**/v1/videos", (route) =>
    route.fulfill({ json: { videos: [] } }),
  );
  await page.goto("/index.html#/ingest");
  await expect(
    page.locator(".appbar-nav").getByRole("button", { name: "Search" }),
  ).toBeEnabled();
  await page
    .locator(".appbar-nav")
    .getByRole("button", { name: "Settings" })
    .click();
  await expect(
    page.getByRole("heading", { name: "Providers & costs" }),
  ).toBeVisible();
});

test("Study remains available as a secondary tool", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
  });
  await page.goto("/index.html#/study");
  await expect(
    page.getByRole("tablist", { name: "Study tool" }).getByRole("tab"),
  ).toHaveText(["Study", "Summary"]);
});

test("shows supported speaker context on generated study flashcards", async ({
  page,
}) => {
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
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ providers: [] }),
    });
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
        evidence_pack: {
          section_count: 5,
          selected_section_count: 5,
          selected_sections: [],
        },
        deck: {
          cards: [
            {
              card_type: "recall",
              question: "How did Stephen Curry describe rebuilding confidence?",
              answer:
                "He treated preparation as a way to regain trust in his game.",
              explanation: "The section connects preparation with confidence.",
              learning_objective: "Recall Curry's preparation mindset.",
              why_it_matters: "It connects a setback to a repeatable response.",
              source_cue:
                "Stephen Curry explains how preparation restored confidence.",
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
  await expect(speaker).toContainText(
    "Speaker: Stephen Curry · interview subject",
  );
  await expect(speaker).toContainText("Supported by section context");
});

for (const width of [1440, 900]) {
  test(`keeps Library and Search anchored beside the brand at ${width}px`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 1024 });
    await page.addInitScript(() => {
      sessionStorage.setItem("yt_rag_intro_seen", "1");
    });
    let anchor: number | undefined;
    for (const url of [
      "/index.html#/ingest",
      "/index.html#/qa",
      "/evaluation.html",
    ]) {
      await page.goto(url);
      await expect(page.locator(".appbar-nav > .app-nav-link")).toHaveText(
        PRIMARY_LABELS,
      );
      const layout = await page.evaluate(() => {
        const brand = document
          .querySelector(".appbar > .brand")!
          .getBoundingClientRect();
        const links = [
          ...document.querySelectorAll<HTMLElement>(
            ".appbar-nav > .app-nav-link",
          ),
        ];
        return {
          brandRight: brand.right,
          links: links.map((link) => ({
            left: link.getBoundingClientRect().left,
            right: link.getBoundingClientRect().right,
            top: link.getBoundingClientRect().top,
            height: link.getBoundingClientRect().height,
            font: getComputedStyle(link).fontSize,
          })),
          pageWidth: document.documentElement.scrollWidth,
          viewport: innerWidth,
        };
      });
      const [library, search, evaluation, settings] = layout.links;
      expect(library.left - layout.brandRight).toBeGreaterThanOrEqual(8);
      expect(library.left - layout.brandRight).toBeLessThanOrEqual(32);
      expect(search.left - library.right).toBeLessThanOrEqual(8);
      expect(evaluation.left - search.right).toBeGreaterThan(16);
      expect(settings.right).toBeLessThanOrEqual(width);
      expect(layout.pageWidth).toBeLessThanOrEqual(layout.viewport);
      expect(
        new Set(layout.links.map((link) => `${link.height}:${link.font}`)).size,
      ).toBe(1);
      if (anchor !== undefined)
        expect(Math.abs(library.left - anchor)).toBeLessThan(1);
      anchor = library.left;
    }
  });
}
