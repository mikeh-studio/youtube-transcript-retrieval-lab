import { expect, test } from "./fixtures";

const VIDEOS = [
  { video_id: "animeVideo1", title: "アニメ制作の現場", language: "ja", num_chunks: 80 },
  { video_id: "techVideo01", title: "Building useful AI products", language: "en", num_chunks: 40 },
];

function row(videoId, chunkIndex, text, extra = {}) {
  return {
    video_id: videoId,
    video_title: VIDEOS.find((video) => video.video_id === videoId)?.title,
    chunk_index: chunkIndex,
    start: chunkIndex * 30,
    end: chunkIndex * 30 + 30,
    text,
    url: `https://www.youtube.com/watch?v=${videoId}&t=${chunkIndex * 30}s`,
    language: "ja",
    score: 0.03,
    ...extra,
  };
}

// Run A (single pass) and Run B (agentic) return overlapping, reordered results.
const RESULTS_A = [
  row("animeVideo1", 12, "作画監督は原画の線の統一感を確認する", { dense_score: 0.8, lexical_score: 3.1 }),
  row("animeVideo1", 3, "制作進行はスケジュールを担当する", { lexical_score: 2.2 }),
  row("animeVideo1", 21, "色彩設計との打ち合わせ", { dense_score: 0.7 }),
];
const RESULTS_B = [
  row("animeVideo1", 12, "作画監督は原画の線の統一感を確認する", { rerank_score: 0.91, context_segment_count: 3 }),
  row("animeVideo1", 13, "修正指示は赤ペンで原画に直接入れる", { rerank_score: 0.84 }),
  row("animeVideo1", 21, "色彩設計との打ち合わせ", { rerank_score: 0.62 }),
];
const AGENTIC_TRACE = {
  agentic_retrieval: {
    sufficient: true,
    stopped_reason: "sufficient_evidence",
    attempts: [
      { attempt: 1, strategy: "initial", tool: "keyword_search", query: "作画監督", result_count: 2, k: 5, sufficient: false, reason_code: "thin_support" },
      { attempt: 2, strategy: "switch_mode", tool: "semantic_search", query: "作画監督", result_count: 3, k: 5, sufficient: true, reason_code: "multi_chunk_support" },
    ],
  },
};

async function mockWorkbench(page, { existingReviews = [], catalogItems = null } = {}) {
  const calls = { search: [], reviews: [] };
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("**/v1/videos", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ ok: true, videos: VIDEOS }),
  }));
  await page.route("**/v1/llm-options", (route) => route.fulfill({ contentType: "application/json", body: '{"providers":[]}' }));
  await page.route("**/v1/search", async (route) => {
    const body = route.request().postDataJSON();
    calls.search.push(body);
    const agentic = Boolean(body.agentic);
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        agentic,
        retrieval_mode: agentic ? "lexical" : body.retrieval_mode,
        result_count: 3,
        results: agentic ? RESULTS_B : RESULTS_A,
        retrieval_details: agentic ? AGENTIC_TRACE : {},
      }),
    });
  });
  await page.route(/\/v1\/feedback\/search-review/, async (route) => {
    if (route.request().method() === "POST") {
      calls.reviews.push(route.request().postDataJSON());
      await route.fulfill({ contentType: "application/json", body: '{"ok":true}' });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, reviews: existingReviews }) });
  });
  await page.route(/\/v1\/catalog\/items/, (route) => (catalogItems
    ? route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ generated_at: "2026-10-14T00:00:00Z", total: catalogItems.length, limit: 100, offset: 0, scope: "private_personal_search", items: catalogItems }),
    })
    : route.fulfill({ status: 503, contentType: "application/json", body: '{"error":"unavailable"}' })));
  return calls;
}

async function runQuery(page, text = "作画監督") {
  await page.goto("/index.html#/qa");
  await page.getByLabel("Query").fill(text);
  await page.getByRole("button", { name: "Run A and B" }).click();
}

test("compares Run A and Run B side by side with rank changes", async ({ page }) => {
  const calls = await mockWorkbench(page);
  await runQuery(page);

  await expect.poll(() => calls.search.length).toBe(2);
  const [a, b] = [calls.search.find((body) => !body.agentic), calls.search.find((body) => body.agentic)];
  expect(a).toMatchObject({ query: "作画監督", retrieval_mode: "hybrid", retrieval_profile: "baseline_rrf", reranker: "none", k: 5, feedback_tuning: false });
  expect(b).toMatchObject({ agentic: true, reranker: "cross_encoder", feedback_tuning: false });
  expect(a.video_ids).toBeUndefined();

  const columnA = page.locator('.lab-column[data-run="A"]');
  const columnB = page.locator('.lab-column[data-run="B"]');
  await expect(columnA.locator(".lab-hit")).toHaveCount(3);
  await expect(columnB.locator(".lab-hit").nth(0).locator(".lab-delta")).toHaveText("=");
  await expect(columnB.locator(".lab-hit").nth(1).locator(".lab-delta")).toHaveText("new");
  await expect(columnB.locator(".lab-hit").nth(2).locator(".lab-delta")).toHaveText("=");
  await expect(columnA.locator(".lab-hit").nth(1).locator(".lab-delta")).toHaveText("only A");
  await expect(columnA.locator(".lab-hit").nth(0).locator(".lab-signal")).toHaveText(["dense", "bm25"]);
  await expect(columnB.locator(".lab-hit").nth(0).locator(".lab-signal")).toHaveText(["rerank", "context"]);

  await expect(page.locator(".lab-metrics")).toContainText("0 of 4 unique hits");
  await expect(page.locator(".lab-metrics")).toContainText("2 shared");

  const traceB = page.locator('.lab-trace[data-run="B"]');
  await expect(traceB.locator(".lab-steps li")).toHaveCount(2);
  await expect(traceB).toContainText("Switched search tool");
  await expect(traceB).toContainText("sufficient");
});

test("loads existing labels, saves new ones, and scores both runs", async ({ page }) => {
  const calls = await mockWorkbench(page, {
    existingReviews: [{ chunk_key: "animeVideo1:12", label: "relevant", query: "作画監督" }],
  });
  await runQuery(page);

  const columnA = page.locator('.lab-column[data-run="A"]');
  const columnB = page.locator('.lab-column[data-run="B"]');
  await expect(columnA.locator(".lab-hit").first().getByRole("button", { name: "Relevant", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(page.locator(".lab-metrics")).toContainText("1 of 4 unique hits");

  await columnB.locator(".lab-hit").nth(1).getByRole("button", { name: "Relevant", exact: true }).click();
  await expect.poll(() => calls.reviews.length).toBe(1);
  expect(calls.reviews[0]).toMatchObject({
    query: "作画監督",
    label: "relevant",
    video_id: "animeVideo1",
    chunk_index: 13,
    retrieval_mode: "lexical",
  });
  await columnA.locator(".lab-hit").nth(1).getByRole("button", { name: "Not relevant" }).click();

  // Relevant: chunks 12 and 13. A ranks 12 first only; B ranks 12 and 13 first and second.
  await expect(page.locator(".lab-metrics")).toContainText("3 of 4 unique hits");
  await expect(page.locator(".lab-metrics")).toContainText("A 0.20 · B 0.40");
  await expect(page.locator(".lab-metrics")).toContainText("A 0.61 · B 1.00");
});

test("scopes the search to indexed videos that match a catalog filter", async ({ page }) => {
  const catalogItem = (externalId, key, en) => ({
    id: "8f14e45f-ceea-467a-9575-6b2c4f1d2a01",
    external_id: externalId,
    url: `https://www.youtube.com/watch?v=${externalId}`,
    qualification: "eligible",
    metadata: {},
    missing_fields: {},
    labels: [{ vocabulary: "library", version: "v1", key, namespace: "topic", en, ja: en }],
    readiness: { state: "preconditions_met", blockers: [], expires_at: null },
    searchable: "not_checked",
    first_seen: null,
    last_seen: null,
  });
  const calls = await mockWorkbench(page, {
    catalogItems: [catalogItem("animeVideo1", "anime", "Anime"), catalogItem("notIndexed1", "anime", "Anime")],
  });
  await page.goto("/index.html#/qa");
  await page.getByLabel("Scope").selectOption("catalog");
  await page.getByLabel("Catalog topic").selectOption("anime");
  await page.getByLabel("Query").fill("作画監督");
  await page.getByRole("button", { name: "Run A and B" }).click();

  await expect.poll(() => calls.search.length).toBe(2);
  expect(calls.search.every((body) => JSON.stringify(body.video_ids) === '["animeVideo1"]')).toBe(true);
  await expect(page.locator(".lab-result-head")).toContainText("1 indexed of 2 catalog items (Anime · eligible)");
});

test("explains when the catalog filter is unavailable", async ({ page }) => {
  const calls = await mockWorkbench(page);
  await page.goto("/index.html#/qa");
  await page.getByLabel("Scope").selectOption("catalog");
  await expect(page.getByText("The catalog service is not connected, so catalog filters cannot be applied.")).toBeVisible();
  await page.getByLabel("Query").fill("作画監督");
  await page.getByRole("button", { name: "Run A and B" }).click();
  await expect(page.getByRole("alert")).toContainText("catalog service is not connected");
  expect(calls.search).toHaveLength(0);
});

test("saves the query to Evaluate's Saved from Search query set once", async ({ page }) => {
  await mockWorkbench(page);
  await runQuery(page);
  await page.getByRole("button", { name: "Save to query set" }).click();
  await expect(page.getByRole("status")).toContainText("Saved to the Saved from Search query set");
  await page.getByRole("button", { name: "Save to query set" }).click();
  await expect(page.getByRole("status")).toContainText("already in the Saved from Search query set");

  const stored = await page.evaluate(() => JSON.parse(localStorage.getItem("youtube-rag-eval-v1") || "{}"));
  const querySet = stored.query_sets.find((row) => row.id === "qs-search-workbench");
  expect(querySet.name).toBe("Saved from Search");
  expect(querySet.queries.map((row) => row.text)).toEqual(["作画監督"]);
});

test("keeps the answer flow in the Answer tab with the current query", async ({ page }) => {
  await mockWorkbench(page);
  await runQuery(page);
  await page.getByRole("tab", { name: "Answer" }).click();
  await expect(page.getByLabel("Question")).toHaveValue("作画監督");
  await expect(page.getByRole("button", { name: "Generate Answer" })).toBeVisible();
});

test("agentic setup exposes automatic tools and saves the actual retrieval mode", async ({ page }) => {
  const calls = await mockWorkbench(page);
  await page.goto("/index.html#/qa");
  await expect(page.getByLabel("Run B mode", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Run B hybrid profile")).toHaveCount(0);
  await expect(page.locator('.lab-run[data-run="B"]')).toContainText("Tools are chosen automatically");
  await page.getByLabel("Run B strategy").selectOption("retrieval");
  await page.getByLabel("Run B mode", { exact: true }).selectOption("lexical");
  await page.getByLabel("Run B strategy").selectOption("agentic");
  await page.getByLabel("Query").fill("作画監督");
  await page.getByRole("button", { name: "Run A and B" }).click();
  await page.locator('.lab-column[data-run="B"] .lab-hit').first().getByRole("button", { name: "Relevant", exact: true }).click();
  expect(calls.search.find((body) => body.agentic).retrieval_profile).toBeUndefined();
  expect(calls.search.find((body) => body.agentic).retrieval_mode).toBeUndefined();
  await expect.poll(() => calls.reviews.length).toBe(1);
  expect(calls.reviews[0].retrieval_mode).toBe("lexical");
  await expect(page.locator('.lab-column[data-run="B"] .lab-column-head')).not.toContainText("baseline_rrf");
});

test("keeps labels tied to their results while another query is running", async ({ page }) => {
  await mockWorkbench(page);
  await runQuery(page, "first query");
  const relevant = page.locator('.lab-column[data-run="A"] .lab-hit').first().getByRole("button", { name: "Relevant", exact: true });
  await relevant.click();
  await expect(relevant).toBeEnabled();

  let releaseSearch;
  const held = new Promise<void>((resolve) => { releaseSearch = resolve; });
  await page.route("**/v1/search", async (route) => {
    await held;
    await route.fallback();
  });
  await page.getByLabel("Query").fill("second query");
  await page.getByRole("button", { name: "Run A and B" }).click();
  await expect(relevant).toBeDisabled();
  await expect(relevant).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("button", { name: "Save to query set" })).toBeDisabled();
  releaseSearch();
  await expect(page.locator(".lab-result-head")).toContainText("second query");
  await expect(relevant).toHaveAttribute("aria-pressed", "false");
});

for (const failed of [false, true]) {
  test(`waits for a pending label save before changing queries (${failed ? "failure" : "success"})`, async ({ page }) => {
    await mockWorkbench(page);
    await runQuery(page, "first query");
    let releaseSave;
    const held = new Promise<void>((resolve) => { releaseSave = resolve; });
    await page.route("**/v1/feedback/search-review", async (route) => {
      if (route.request().method() !== "POST") return route.fallback();
      await held;
      await route.fulfill({ status: failed ? 500 : 200, contentType: "application/json", body: failed ? '{"error":{"message":"save failed"}}' : '{"ok":true}' });
    });
    const relevant = page.locator('.lab-column[data-run="A"] .lab-hit').first().getByRole("button", { name: "Relevant", exact: true });
    await relevant.click();
    await page.getByLabel("Query").fill("second query");
    await expect(page.getByRole("button", { name: "Run A and B" })).toBeDisabled();
    releaseSave();
    await expect(page.getByRole("button", { name: "Run A and B" })).toBeEnabled();
    await page.getByRole("button", { name: "Run A and B" }).click();
    await expect(page.locator(".lab-result-head")).toContainText("second query");
    await expect(relevant).toHaveAttribute("aria-pressed", "false");
    await expect(page.locator(".lab-metrics")).toContainText("0 of 4 unique hits");
  });
}
