import { readFileSync } from "node:fs";
import path from "node:path";
import { test, expect } from "@playwright/test";

test("saved report profiles, metrics and question drilldown", async ({
  page,
}) => {
  const report = {
    id: "fixture",
    run_id: "Synthetic run",
    source: "Synthetic CLI",
    gates: {
      source_selection: { target: 0.85, minimum_passes: 34, denominator: 40 },
      underspecification: { target: 0.85, minimum_passes: 17, denominator: 20 },
    },
    profiles: [
      {
        id: "first",
        status: "partial",
        arms: {
          direct: {
            accepted: 1,
            total: 2,
            failed: 0,
            validation_rejections: 0,
            grades: null,
          },
          mcp: { accepted: 0, total: 2, failed: 1, validation_rejections: 1 },
        },
        cases: [
          {
            question: "Synthetic question?",
            status: "failed",
            answer: "<script>unsafe</script>",
            citations: [
              { text: "Synthetic passage", url: "javascript:alert(1)" },
            ],
          },
        ],
      },
      { id: "second", status: "stopped", arms: { direct: {}, mcp: {} } },
    ],
  };
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.goto("/evaluation.html");
  await expect(
    page.getByRole("heading", { name: "Synthetic run" }),
  ).toBeVisible();
  await expect(page.getByText("≥85% · 34 of 40 required")).toBeVisible();
  await expect(page.getByText("Awaiting grades").first()).toBeVisible();
  await expect(page.getByText("Partial run", { exact: false })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Inspect 1 failure (1 validation)" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "Citation relevance", exact: false })
    .click();
  await expect(
    page.getByText("Question-level contributors", { exact: false }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await page.getByText("Synthetic question?", { exact: false }).click();
  await expect(page.getByText("<script>unsafe</script>")).toBeVisible();
  await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
  await page.getByRole("button", { name: "second · stopped" }).click();
  await expect(
    page.getByText("No recorded questions in this view."),
  ).toBeVisible();
});

test("empty installation links to query sets", async ({ page }) => {
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [] } }),
  );
  await page.goto("/evaluation.html");
  await expect(page.getByText("No saved reports yet")).toBeVisible();
  await expect(page.locator(".appbar .brand-sub")).toHaveText(
    "YouTube transcripts",
  );
  await expect(
    page.getByRole("link", { name: "Retrieval Lab home" }),
  ).toHaveAttribute("href", "./index.html");
  await expect(page.getByRole("combobox", { name: "Language" })).toHaveCount(0);
  await expect(
    page.getByRole("link", {
      name: "Open query sets and dataset management →",
    }),
  ).toHaveAttribute("href", "./evaluation-workbench.html");
});

test("version baseline requires complete matching provenance", async ({
  page,
}) => {
  const identity = {
    dataset_hash: "data",
    corpus_hash: "corpus",
    queryset_hash: "questions",
    criteria_hash: "criteria",
    stage: "initial_retrieval",
    cutoff: 5,
  };
  const profile = {
    id: "a",
    model: "model",
    effort: "high",
    arms: { direct: {}, mcp: {} },
  };
  const reports = [
    { id: "current", run_id: "Current", ...identity, profiles: [profile] },
    { id: "matching", run_id: "Matching", ...identity, profiles: [profile] },
    {
      id: "wrong-model",
      run_id: "Wrong model",
      ...identity,
      profiles: [{ ...profile, model: "other" }],
    },
    {
      id: "wrong-profile-hash",
      run_id: "Wrong profile hash",
      ...identity,
      profiles: [{ ...profile, corpus_hash: "other" }],
    },
    {
      id: "wrong-arm-hash",
      run_id: "Wrong arm hash",
      ...identity,
      profiles: [
        {
          ...profile,
          arms: { ...profile.arms, direct: { criteria_hash: "other" } },
        },
      ],
    },
    {
      id: "unknown",
      run_id: "Unknown criteria",
      ...identity,
      criteria_hash: null,
      profiles: [profile],
    },
    {
      id: "different",
      run_id: "Different cutoff",
      ...identity,
      cutoff: 10,
      profiles: [profile],
    },
  ];
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports } }),
  );
  await page.goto("/evaluation.html");
  await page.getByRole("button", { name: "Compare versions" }).click();
  const select = page.getByLabel("Compatible baseline");
  await expect(select.locator("option")).toHaveCount(2);
  await expect(select.locator("option")).toContainText([
    "None selected",
    "Matching",
  ]);
});

test("compares selected workflow versions and exposes failed and unattempted cases", async ({
  page,
}) => {
  const identity = {
    dataset_hash: "d",
    corpus_hash: "c",
    queryset_hash: "q",
    criteria_hash: "g",
    stage: "initial_retrieval",
    cutoff: 5,
  };
  const mk = (id: string, score: number) => ({
    id,
    run_id: id,
    ...identity,
    profiles: [
      {
        id: "p",
        model: "m",
        effort: "high",
        arms: {
          direct: {
            failed: 0,
            validation_rejections: 0,
            ranking: { initial_retrieval: { precision_at_5: score } },
          },
          mcp: {
            failed: 1,
            validation_rejections: 1,
            ranking: { initial_retrieval: { precision_at_5: score + 0.2 } },
          },
        },
        cases: [
          {
            question: "Rejected case",
            workflow: "mcp",
            status: "answer_validation_failed",
          },
          {
            question: "Not attempted case",
            workflow: "mcp",
            status: "not_attempted",
          },
        ],
      },
    ],
  });
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({
      json: { reports: [mk("current", 0.5), mk("baseline", 0.3)] },
    }),
  );
  await page.goto("/evaluation.html");
  await expect(page.getByText("Target not configured").first()).toBeVisible();
  await page.getByRole("button", { name: "Compare versions" }).click();
  await page.getByLabel("Compatible baseline").selectOption("baseline");
  await expect(
    page.getByRole("button", {
      name: "Inspect 0 recorded failure or unattempted attempts · DIRECT workflow",
    }),
  ).toBeVisible();
  await page.getByLabel("Workflow").selectOption("mcp");
  await expect(
    page.getByRole("button", {
      name: "Inspect 2 recorded failure or unattempted attempts · MCP workflow",
    }),
  ).toBeVisible();
  await page.getByLabel("Compatible baseline").selectOption("baseline");
  await expect(
    page.getByRole("columnheader", { name: "Baseline MCP" }).first(),
  ).toBeVisible();
  const precision = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "Precision@5", exact: false }),
  });
  await expect(precision).toContainText("0.5");
  await expect(precision).toContainText("0.7");
  await expect(precision).toContainText("0.2");
  await page
    .getByRole("button", {
      name: "Inspect 2 recorded failure or unattempted attempts · MCP workflow",
    })
    .click();
  await expect(
    page.getByText("2 recorded questions · 2 attempts", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("Rejected case", { exact: false })).toBeVisible();
  await expect(
    page.getByText("Not attempted case", { exact: false }),
  ).toBeVisible();
});

test("offline benchmark compares recorded methods without semantic claims", async ({
  page,
}) => {
  const report = {
    id: "benchmark",
    run_id: "Example benchmark",
    source: "Offline retrieval benchmark",
    description: "Synthetic example fixture · 8 recorded queries",
    profiles: [
      {
        id: "Retrieval methods",
        arms: {
          hybrid_baseline: {
            ranking: { initial_retrieval: { ndcg_at_10: 0.8 } },
          },
          optimized_hybrid: {
            ranking: { initial_retrieval: { ndcg_at_10: 0.9 } },
          },
          dense: { ranking: { initial_retrieval: { ndcg_at_10: 0.6 } } },
        },
      },
    ],
  };
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.goto("/evaluation.html");
  await expect(
    page.getByText("Synthetic example fixture · 8 recorded queries").first(),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "hybrid_baseline" }),
  ).toBeVisible();
  await expect(
    page.getByRole("columnheader", { name: "optimized_hybrid" }),
  ).toBeVisible();
  const ndcg = page.getByRole("row").filter({
    has: page.getByRole("button", { name: "nDCG@10", exact: false }),
  });
  await expect(ndcg).toContainText("0.8");
  await expect(ndcg).toContainText("0.9");
  await expect(page.getByText("Δ (B − A)")).toBeVisible();
  await expect(page.getByText("Δ (MCP − Direct)")).toHaveCount(0);
  await expect(page.getByText("Awaiting grades")).toHaveCount(0);
  await page.getByLabel("Method B").selectOption("dense");
  await expect(ndcg).toContainText("0.6");
});

test("scoped contributors, evidence, failure detail and keyboard focus", async ({
  page,
}) => {
  const report = {
    id: "detailed",
    run_id: "Detailed",
    profiles: [
      {
        id: "profile",
        arms: {
          direct: {
            grades: {
              answer_quality: {
                distribution: { Good: 2, Partial: 1, Poor: 0 },
              },
              citation_relevance: {
                value: 0.19999999999999998,
                question_ids: ["q1"],
              },
            },
            input_tokens: 12345,
            api_equivalent_usd: 0.123456,
          },
          mcp: {
            grades: {
              citation_relevance: { value: 0.5, question_ids: ["q1"] },
            },
            input_tokens: 25000,
            api_equivalent_usd: 0.223456,
          },
        },
        cases: [
          {
            question_id: "q1",
            question: "Paired question?",
            workflow: "direct",
            status: "completed",
            answer: "Accepted answer",
            citations: [
              {
                video_title: "Source title",
                passage_id: "span:v1:" + "x".repeat(100),
                rank: 2,
                original_text: "Unchanged quote.",
                url: "https://www.youtube.com/watch?v=abc&t=1373s",
              },
            ],
          },
          {
            question_id: "q1",
            question: "Paired question?",
            workflow: "mcp",
            status: "answer_validation_failed",
            validation_failure: "quote is not exact canonical text",
            attempted_output: { status: "rejected", answer: "Rejected answer" },
            trace: {
              retrieval_payloads: [
                { hits: [{ rank: 1, title: "Ranked source" }] },
              ],
            },
          },
          {
            question_id: "q2",
            question: "Other question?",
            workflow: "direct",
          },
        ],
      },
    ],
  };
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.goto("/evaluation.html");
  await expect(page.getByText("Good 2 / Partial 1 / Poor 0")).toBeVisible();
  await expect(page.getByText("12,345")).toBeVisible();
  await expect(page.getByText("12,655")).toBeVisible();
  await expect(page.getByText("$0.100000")).toBeVisible();
  await expect(
    page.getByRole("row").filter({
      has: page.getByRole("button", {
        name: "Citation relevance",
        exact: false,
      }),
    }),
  ).toContainText("0.2");
  const overview = page.getByRole("button", { name: "Overview", exact: true });
  await overview.focus();
  await page.keyboard.press("Enter");
  await expect(overview).toBeFocused();
  await expect(overview).toHaveAttribute("aria-pressed", "true");
  const failures = page.getByRole("button", {
    name: "Failures & traces",
    exact: true,
  });
  await failures.focus();
  await page.keyboard.press("Enter");
  await expect(failures).toBeFocused();
  await overview.click();
  await page
    .getByRole("button", { name: "Citation relevance", exact: false })
    .click();
  await expect(
    page.getByRole("button", { name: "Questions", exact: true }),
  ).toBeFocused();
  await expect(
    page.getByText("1 recorded questions · 2 attempts", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("Other question?", { exact: false })).toHaveCount(
    0,
  );
  await page.getByText("Paired question?", { exact: false }).click();
  await expect(
    page.getByText("quote is not exact canonical text"),
  ).toBeVisible();
  await expect(page.getByText("Attempted output: rejected")).toBeVisible();
  await expect(
    page.getByText("Rejected answer", { exact: false }),
  ).toBeHidden();
  await expect(
    page.getByText("Ranked source", { exact: false }).first(),
  ).toBeVisible();
  await page.getByText("Show rejected attempted output").click();
  await expect(
    page.getByText("Rejected answer", { exact: false }),
  ).toBeVisible();
  await expect(page.getByText("Source title", { exact: true })).toBeVisible();
  await expect(page.getByText("Rank 2 · 22:53", { exact: true })).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Open source.*Source title/ }),
  ).toHaveAttribute("href", "https://www.youtube.com/watch?v=abc&t=1373s");
  await page.getByText("Full reference").click();
  await expect(
    page.locator("code").filter({ hasText: "span:v1:" + "x".repeat(100) }),
  ).toBeVisible();
  await expect(page.getByText("Unchanged quote.")).toBeVisible();
  await expect(page.getByText("Show detailed trace")).toBeVisible();
  await expect(
    page.locator("pre").filter({ hasText: "retrieval_payloads" }),
  ).toBeHidden();
  await page.getByRole("button", { name: "Compare versions" }).click();
  await page.getByLabel("Workflow").selectOption("mcp");
  await expect(
    page.getByText("1 recorded questions · 1 attempts", { exact: false }),
  ).toBeVisible();
});

test("cost threshold requires recorded semantic outcomes", async ({ page }) => {
  const basis = { total: 1, cost_basis: "per case", includes_failed: true };
  const report = {
    id: "cost",
    run_id: "Cost",
    profiles: [
      {
        id: "p",
        arms: { direct: basis, mcp: basis },
        cases: [
          {
            question_id: "q",
            workflow: "direct",
            grades: { reviewer: "human", quality: null as string | null },
            cost: { api_equivalent_usd: 1 },
          },
          {
            question_id: "q",
            workflow: "mcp",
            grades: { status: "pending", quality: null as string | null },
            cost: { api_equivalent_usd: 1.2 },
          },
        ],
      },
    ],
  };
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [report] } }),
  );
  await page.goto("/evaluation.html");
  await expect(
    page.getByText("matched graded-case costs are incomplete or unavailable", {
      exact: false,
    }),
  ).toBeVisible();
  report.profiles[0].cases[0].grades.quality = "Good";
  report.profiles[0].cases[1].grades.quality = "Partial";
  report.profiles[0].cases[1].grades.status = "graded";
  await page.reload();
  await expect(
    page.getByText(
      "within threshold (1.2× across 1 matched graded questions)",
      { exact: false },
    ),
  ).toBeVisible();
});

test("mobile run history scrolls without pushing navigation off screen", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const reports = [1, 2, 3].map((index) => ({
    id: `mobile-${index}`,
    run_id: `Saved run ${index}`,
    profiles: [{ id: "fixture", arms: { direct: {}, mcp: {} } }],
  }));
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports } }),
  );
  await page.goto("/evaluation.html");
  await expect(
    page.getByRole("heading", { name: "Saved run 1" }),
  ).toBeVisible();
  const dimensions = await page.evaluate(() => ({
    page: document.documentElement.scrollWidth,
    viewport: innerWidth,
    history: document.getElementById("runList")!.scrollWidth,
    historyViewport: document.getElementById("runList")!.clientWidth,
  }));
  expect(dimensions.page).toBeLessThanOrEqual(dimensions.viewport);
  expect(dimensions.history).toBeGreaterThan(dimensions.historyViewport);
  await expect(
    page
      .getByRole("navigation", { name: "Primary", exact: true })
      .getByRole("link", { name: "Search", exact: true }),
  ).toBeVisible();
});

test("mock metrics stay visibly labeled as demo data", async ({ page }) => {
  const report = JSON.parse(
    readFileSync(path.resolve("docs/examples/evaluation-demo.json"), "utf8"),
  );
  await page.route("**/v1/evaluation/reports", (route) =>
    route.fulfill({ json: { reports: [{ ...report, id: "demo-fixture" }] } }),
  );
  await page.goto("/evaluation.html");
  await expect(
    page.getByRole("heading", { name: "Evaluation demo · mocked metrics" }),
  ).toBeVisible();
  await expect(page.getByRole("note")).toContainText(
    "Demo data — not measured results.",
  );
  await expect(
    page.getByRole("row", { name: "› nDCG@10 0.81 0.9 0.09", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("row", { name: "› ROC-AUC 0.84 0.92 0.08", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByText("Good 48 / Partial 9 / Poor 3", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Questions", exact: true }).click();
  await expect(page.getByRole("note")).toBeVisible();
  await expect(
    page.getByText("No recorded questions in this view."),
  ).toBeVisible();
});
