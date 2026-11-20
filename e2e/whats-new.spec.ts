import { expect, test } from "./fixtures";

const channelId = "UCabcdefghijklmnopqrstuv";
const feedUrl = "https://paper.example/rss";
const releaseId = "pod_0123456789abcdef";
const priorId = "abcdef12345";
const releaseTitle = "Paper Boat Workshop";
const priorTitle = "The Earlier Boat Workshop";
const priorQuote = "Fold the bow twice. <img src=x>";
const date = "2026-03-08T16:00:00+00:00";

test.use({ timezoneId: "America/Los_Angeles" });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => sessionStorage.setItem("yt_rag_intro_seen", "1"));
  await page.route(/^https?:/, (route) => new URL(route.request().url()).hostname === "127.0.0.1"
    ? route.fallback() : route.fulfill({ body: "" }));
  await page.route("**/v1/videos", (route) => route.fulfill({
    json: {
      ok: true,
      videos: [{
        video_id: releaseId,
        title: releaseTitle,
        source_type: "podcast",
        source: { platform: "podcast", local_media_path: "synthetic.mp3" },
      }],
    },
  }));
  await page.route("**/v1/subscriptions", (route) => route.fulfill({ json: { ok: true, subscriptions: [] } }));
  await page.route("**/v1/releases", (route) => route.fulfill({ json: { ok: true, releases: [] } }));
});

test("adds both subscription types and refreshes release statuses after Check now", async ({ page }) => {
  const subscriptions: Record<string, unknown>[] = [];
  await page.route("**/v1/videos", (route) => route.fulfill({ json: { videos: [] } }));
  await page.route("**/v1/subscriptions", async (route) => {
    if (route.request().method() === "POST") {
      const subscription = { ...route.request().postDataJSON(), id: `feed-${subscriptions.length}` };
      subscriptions.push(subscription);
      await route.fulfill({ status: 201, json: { ok: true, subscription } });
    } else {
      await route.fulfill({ json: { ok: true, subscriptions } });
    }
  });

  const releases = [
    {
      subscription_id: "feed-1", external_id: "first-fold", title: releaseTitle,
      link: "https://paper.example/boats", status: "ingesting", reason: null,
      source_id: releaseId, published_at: date, updated_at: date,
    },
    {
      subscription_id: "feed-1", external_id: "glue-preview", title: "Paper Glue Preview",
      link: "javascript:alert(1)", status: "skipped", reason: "title_exclude",
      source_id: null, published_at: date, updated_at: date,
    },
    {
      subscription_id: "feed-1", external_id: "old-origami", title: "Earlier Origami",
      link: null, status: "discovered", reason: "backlog",
      source_id: null, published_at: date, updated_at: date,
    },
  ];
  let polled = false;
  await page.route("**/v1/releases", (route) => route.fulfill({ json: { ok: true, releases: polled ? releases : [] } }));
  await page.route("**/v1/subscriptions/feed-1/poll", (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({});
    polled = true;
    return route.fulfill({
      json: {
        ok: true, poll: {
          subscription_id: "feed-1", new_count: 3, attempted_count: 1, pending_count: 0,
          ingesting_count: 1, releases,
        },
      },
    });
  });

  await page.goto("/index.html#/ingest");
  await page.locator(".appbar-nav").getByRole("button", { name: "What's new" }).click();
  await expect(page).toHaveURL(/#\/whats-new$/);
  await expect(page.locator(".appbar-nav").getByRole("button", { name: "What's new" })).toHaveAttribute("aria-current", "page");
  const feeds = page.getByRole("region", { name: "Subscriptions" });
  await expect(feeds.getByText("No subscriptions yet.")).toBeVisible();
  await feeds.getByLabel("YouTube channel ID").fill(channelId);
  await feeds.getByRole("button", { name: "Add subscription" }).click();
  await expect(feeds.getByText(channelId, { exact: true })).toBeVisible();
  expect(subscriptions[0]).toEqual({
    id: "feed-0", kind: "youtube", url: `https://www.youtube.com/feeds/videos.xml?channel_id=${channelId}`,
    language: "en", domain_tag: null, title_include: null, title_exclude: null, min_duration_s: 600, auto_ingest: false,
  });

  await feeds.getByLabel("Feed type").selectOption("podcast");
  await feeds.getByLabel("RSS feed URL").fill(feedUrl);
  await feeds.getByLabel("Source language").selectOption("ja");
  await feeds.getByLabel("Domain tag").fill("Paper folding");
  await feeds.getByLabel("Include title (regex)").fill("^Paper");
  await feeds.getByLabel("Exclude title (regex)").fill("Preview$");
  await feeds.getByLabel("Minimum duration (seconds)").fill("120");
  await feeds.getByLabel("Automatically index eligible releases").check();
  await feeds.getByRole("button", { name: "Add subscription" }).click();
  const podcast = feeds.getByRole("listitem").filter({ hasText: feedUrl });
  await expect(podcast).toContainText("Japanese · Paper folding · Auto-index on");
  expect(subscriptions[1]).toEqual({
    id: "feed-1", kind: "podcast", url: feedUrl, language: "ja", domain_tag: "Paper folding",
    title_include: "^Paper", title_exclude: "Preview$", min_duration_s: 120, auto_ingest: true,
  });

  await podcast.getByRole("button", { name: "Check now" }).click();
  await expect(feeds.getByText("Check complete. 3 new, 1 attempted, 0 pending, 1 ingesting.")).toBeVisible();
  const history = page.getByRole("region", { name: "Release history" });
  const episode = history.getByRole("row", { name: /Paper Boat Workshop/ });
  await expect(episode).toContainText("ingesting");
  releases[0].status = "ingested";
  await expect(episode).toContainText("ingested");
  await expect(history.getByRole("row", { name: /Earlier Origami/ })).toContainText("Published before subscription or undated");
  const skipped = history.getByRole("row", { name: /Paper Glue Preview/ });
  await expect(skipped).toContainText("skipped");
  await expect(skipped).toContainText("Title matched the exclude filter");
  await expect(skipped.getByRole("link")).toHaveCount(0);
});

test("builds a dated digest, plays both citations, and saves item feedback", async ({ page }) => {
  const digestId = "digest_0123456789abcdef0123456789abcdef";
  const priorPassages = [
    { passage_id: "P1", source_id: priorId, title: priorTitle, start: 44, end: 49, text: priorQuote },
    { passage_id: "P2", source_id: priorId, title: priorTitle, start: 65, end: 72, text: "Discard the scraps." },
  ];
  const highlights = [
    {
      claim_id: `${releaseId}:1:1`, text: "One fold holds the bow.", start: 92, end: 98,
      evidence_lines: [9, 10], relation: "contradicts", prior: ["P1"], prior_passages: priorPassages,
      reason: "The earlier workshop used two folds.", score: 3,
    },
    {
      claim_id: `${releaseId}:2:1`, text: "A wax pencil marks the crease.", start: 155, end: 161,
      evidence_lines: [17], relation: "new", prior: [], prior_passages: [], reason: "", score: 2,
    },
    {
      claim_id: `${releaseId}:3:1`, text: "Press the crease with a spoon.", start: 196, end: 198,
      evidence_lines: [20], relation: "extends", prior: ["P1"], prior_passages: priorPassages, reason: "", score: 0.6,
    },
  ];

  await page.route("**/v1/digest/whats-new", (route) => {
    expect(route.request().method()).toBe("POST");
    expect(route.request().postDataJSON()).toEqual({
      window: { start: "2026-03-07T08:00:00.000Z", end: "2026-03-09T07:00:00.000Z" }, language: "ja",
    });
    return route.fulfill({
      json: {
        ok: true,
        digest: {
          digest_id: digestId,
          releases: [{
            source_id: releaseId, title: releaseTitle, show: "Paper Radio", published_at: date,
            covered_count: 2, highlights, disagreements: [highlights[0]],
          }],
        },
      },
    });
  });
  const votes: unknown[] = [];
  await page.route(`**/v1/digest/${digestId}/feedback`, (route) => {
    expect(route.request().method()).toBe("POST");
    const vote = route.request().postDataJSON();
    votes.push(vote);
    return route.fulfill({ status: 201, json: { ok: true, feedback: vote } });
  });
  await page.route(`**/v1/sources/${releaseId}/media`, (route) => route.fulfill({ status: 204 }));

  await page.goto("/index.html#/whats-new");
  const digest = page.getByRole("region", { name: "Digest" });
  await digest.getByLabel("Start date").fill("2026-03-07");
  await digest.getByLabel("End date (inclusive)").fill("2026-03-08");
  await digest.getByLabel("Output language").selectOption("ja");
  await digest.getByRole("button", { name: "Build digest" }).click();

  const card = digest.getByRole("article");
  for (const name of [releaseTitle, "Highlights", "New to you", "Disagrees with prior episodes", "Extends"]) {
    await expect(card.getByRole("heading", { name, exact: true })).toBeVisible();
  }
  await expect(card).toContainText("Covered by prior episodes: 2");
  const disagreement = card.getByRole("listitem").filter({ hasText: highlights[0].text });
  await expect(disagreement).toContainText("lines 9, 10");
  await expect(disagreement.getByText(priorQuote, { exact: true })).toBeVisible();
  await expect(disagreement.locator("img")).toHaveCount(0);
  await expect(disagreement).not.toContainText("Discard the scraps.");

  const currentCitation = disagreement.getByRole("button", { name: `Play ${releaseTitle} at 1:32` });
  const audioRequest = page.waitForRequest(`**/v1/sources/${releaseId}/media`);
  await currentCitation.click();
  await audioRequest;
  await expect(digest.getByRole("heading", { name: "Audio player" })).toBeVisible();
  await digest.getByRole("button", { name: "Close player" }).click();
  await expect(currentCitation).toBeFocused();

  await disagreement.getByRole("button", { name: `Play ${priorTitle} at 0:44` }).click();
  await expect(digest.locator('iframe[title="YouTube player"]')).toHaveAttribute("src", new RegExp(`/embed/${priorId}\\?start=44&`));

  for (const direction of ["up", "down"]) {
    const button = disagreement.getByRole("button", { name: `Thumbs ${direction}` });
    await button.click();
    await expect(button).toHaveAttribute("aria-pressed", "true");
  }
  expect(votes).toEqual(["up", "down"].map((rating) => ({ item_id: highlights[0].claim_id, rating })));
});

test("shows feed and digest errors and allows an empty digest on retry", async ({ page }) => {
  await page.route("**/v1/subscriptions", (route) => route.fulfill({
    status: 503, json: { ok: false, error: { message: "Subscriptions unavailable." } },
  }));
  let attempt = 0;
  await page.route("**/v1/digest/whats-new", (route) => ++attempt === 1
    ? route.fulfill({ status: 502, json: { ok: false, error: { message: "Digest provider unavailable." } } })
    : route.fulfill({ json: { ok: true, digest: { digest_id: "digest_empty", releases: [] } } }));

  await page.goto("/index.html#/whats-new");
  await expect(page.getByRole("region", { name: "Subscriptions" }).getByRole("alert"))
    .toContainText("Subscriptions unavailable.");
  const digest = page.getByRole("region", { name: "Digest" });
  const build = digest.getByRole("button", { name: "Build digest" });
  await build.click();
  await expect(digest.getByRole("alert")).toContainText("Digest provider unavailable.");
  await build.click();
  await expect(digest.getByText("No indexed sources in this window.")).toBeVisible();
  await expect(digest.getByRole("alert")).toHaveCount(0);
});
