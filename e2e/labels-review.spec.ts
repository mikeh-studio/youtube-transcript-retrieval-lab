import { expect, test } from "./fixtures";

const source = {
  source_id: "orcharddemo",
  title: "Orchard cart rehearsal",
  url: "https://www.youtube.com/watch?v=orcharddemo",
  duration: 120,
};
const transcript = '<img src=x onerror="window.injected = true"> Cart lane';
const proposal = '<b>Leave room for the cart.</b>';
const draftId = "draft_fixture";
const jobId = "labeljob_fixture";
const startPath = "/v1/labels/jobs";
const draftPath = `/v1/labels/drafts/${draftId}`;

function makeDraft() {
  return {
    draft_id: draftId,
    task: "summary_keypoints",
    language: "ja",
    status: "pending_review",
    sources: [source],
    notes: "Check where sorting begins.",
    items: [proposal, "Sort fruit before washing.", "The gate swings outward."].map((text, index) => ({
      id: `item_${index + 1}`,
      source_id: source.source_id,
      text,
      importance: 2,
      onset: 25.5 + 10 * index,
      span_start: 20 + 10 * index,
      span_end: 30 + 10 * index,
      rationale: "Mentioned in the recording.",
      review: null as null | { decision: string; edits?: Record<string, unknown> },
    })),
  };
}

test("starts a label draft, reviews with shortcuts and edits, then finalizes", async ({ page }) => {
  let started = false;
  let polls = 0;
  let finalized = false;
  const requests: unknown[] = [];
  const draft = makeDraft();
  await page.route("**/v1/evaluation/reports", (route) => route.fulfill({ json: { reports: [] } }));
  await page.route("**/v1/videos", (route) => route.fulfill({
    json: { videos: [{ video_id: source.source_id, title: source.title }] },
  }));
  await page.route("**/v1/labels/**", async (route) => {
    const url = new URL(route.request().url());
    switch (url.pathname) {
      case "/v1/labels/tasks":
        return route.fulfill({ json: { tasks: [{
          task: "summary_keypoints", title: "Summary key points", min_sources: 1, max_sources: 1,
        }] } });
      case startPath:
        started = true;
        requests.push(route.request().postDataJSON());
        return route.fulfill({ status: 202, json: { job: { job_id: jobId, status: "queued" } } });
      case `${startPath}/${jobId}`:
        polls += 1;
        return route.fulfill({ json: { job: {
          job_id: jobId, status: polls === 1 ? "running" : "completed", draft_id: draftId,
        } } });
      case "/v1/labels/drafts":
        return route.fulfill({ json: { drafts: started ? [{
          ...draft, item_count: draft.items.length, decided_count: 0,
        }] : [] } });
      case draftPath:
        return route.fulfill({ json: { draft } });
      case `${draftPath}/context`:
        expect(url.searchParams.get("item")).toMatch(/^item_[1-3]$/);
        return route.fulfill({ json: { context: {
          lines: [{ line: 12, start: 25.5, end: 26, text: transcript }],
        } } });
      case `${draftPath}/review`: {
        const { decisions: [row] } = route.request().postDataJSON();
        requests.push(row);
        const item = draft.items.find((entry) => entry.id === row.id)!;
        item.review = { decision: row.decision, edits: row.edits };
        return route.fulfill({ json: { draft } });
      }
      case `${draftPath}/finalize`:
        finalized = true;
        return route.fulfill({ json: { dataset: { dataset_id: "dataset_fixture", row_count: 2 } } });
      default:
        return route.fulfill({ status: 404, json: { error: { message: "Unexpected mock request" } } });
    }
  });

  await page.goto("/evaluation.html");
  await page.getByRole("link", { name: "Label review" }).click();
  await expect(page).toHaveURL(/\/labels\.html$/);
  await expect(page.getByRole("navigation", { name: "Primary", exact: true }).getByRole("link", { name: "Evaluation" })).toHaveAttribute("aria-current", "page");
  await page.getByLabel("Task").selectOption("summary_keypoints");
  await page.getByLabel("Sources").selectOption(source.source_id);
  await page.getByLabel("Output language").fill("ja");
  await page.getByRole("button", { name: "Start draft" }).click();

  await expect(page.getByText("0 of 3 decided")).toBeVisible();
  expect(polls).toBe(2);
  await expect(page.getByText(proposal, { exact: true })).toBeVisible();
  await expect(page.getByText(transcript, { exact: true })).toBeVisible();
  await expect(page.locator("#context img, #itemText b")).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Open at timestamp" })).toHaveAttribute("href", `${source.url}&t=25s`);
  await expect(page.getByRole("button", { name: "Finalize dataset" })).toBeDisabled();

  await page.keyboard.press("j");
  await expect(page.getByRole("heading", { name: "Item 2 of 3" })).toBeVisible();
  await page.keyboard.press("k");
  await page.keyboard.press("a");
  await expect(page.getByText("1 of 3 decided")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Item 2 of 3" })).toBeFocused();
  await page.keyboard.press("e");
  await page.getByLabel("Label text").fill("Check fruit before bringing it to the sink.");
  await page.getByLabel("Importance (1–3)").fill("3");
  await page.getByLabel("Onset (seconds)").fill("34.25");
  await page.getByLabel("Span start (seconds)").fill("32");
  await page.getByLabel("Span end (seconds)").fill("43");
  await page.getByRole("button", { name: "Save edit" }).click();
  await expect(page.getByText("2 of 3 decided")).toBeVisible();

  await page.keyboard.press("r");
  await expect(page.getByText("3 of 3 decided")).toBeVisible();
  await page.getByRole("button", { name: "Finalize dataset" }).click();
  await expect(page.getByText("Dataset saved as dataset_fixture")).toBeVisible();
  await expect(page.getByRole("button", { name: "Approve" })).toBeHidden();
  await page.keyboard.press("k");
  await expect(page.getByText("Check fruit before bringing it to the sink.")).toBeVisible();
  await expect(page.getByRole("link", { name: "Open at timestamp" })).toHaveAttribute("href", `${source.url}&t=34s`);
  expect(finalized).toBe(true);
  expect(requests).toEqual([
    { task: "summary_keypoints", source_ids: [source.source_id], language: "ja" },
    { id: "item_1", decision: "approved" },
    {
      id: "item_2",
      decision: "edited",
      edits: {
        text: "Check fruit before bringing it to the sink.",
        importance: 3,
        onset: 34.25,
        span_start: 32,
        span_end: 43,
      },
    },
    { id: "item_3", decision: "rejected" },
  ]);
});
