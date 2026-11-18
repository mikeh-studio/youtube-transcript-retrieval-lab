import { expect, test } from "./fixtures";

test("reviews a saved digest with escaped prior passages and retains a rejection", async ({ page }) => {
  const digestId = "digest_fixture";
  const draftId = "draft_fixture";
  const draftPath = `/v1/labels/drafts/${draftId}`;
  const claim = '<b>The felt panel closes inward.</b>';
  const priorText = '<img src=x onerror="window.injected = true"> The felt panel closes outward.';
  const reason = '<i>The earlier panel moves the other way.</i>';
  const source = { source_id: "invented-panel", title: "Felt panel notes", url: "https://example.test/panel" };
  const draft = {
    draft_id: draftId,
    task: "digest_novelty",
    sources: [source],
    language: "en",
    status: "pending_review",
    items: [{
      id: "item_1", source_id: source.source_id, relation: "contradicts",
      text: claim, reason, start: 5, end: 7, prior: ["P1"],
      prior_passages: [{ passage_id: "P1", title: "Panel rehearsal", start: 12, end: 16, text: priorText }],
      review: null as null | { decision: string },
    }],
  };
  let started = false;
  await page.route("**/v1/videos", (route) => route.fulfill({ json: { videos: [] } }));
  await page.route("**/v1/digests", (route) => route.fulfill({ json: { digests: [
    { digest_id: digestId, created_at: "2026-03-05", release_titles: [source.title], reviewable_count: 1 },
    { digest_id: "digest_empty", created_at: "2026-03-04", release_titles: [], reviewable_count: 0 },
  ] } }));
  await page.route("**/v1/labels/**", (route) => {
    switch (new URL(route.request().url()).pathname) {
      case "/v1/labels/tasks":
        return route.fulfill({ json: { tasks: [
          { task: "digest_novelty", title: "Digest novelty", min_sources: 0, max_sources: 0, input: "digest" },
        ] } });
      case "/v1/labels/jobs":
        expect(route.request().postDataJSON()).toEqual({ task: "digest_novelty", digest_id: digestId });
        started = true;
        return route.fulfill({ status: 202, json: { job: { status: "completed", draft_id: draftId } } });
      case "/v1/labels/drafts":
        return route.fulfill({ json: { drafts: started ? [{ ...draft, item_count: 1, decided_count: 0 }] : [] } });
      case draftPath:
        return route.fulfill({ json: { draft } });
      case `${draftPath}/context`:
        return route.fulfill({ json: { context: {
          lines: [{ line: 1, start: 5, end: 7, text: "The new panel moves inward." }],
        } } });
      case `${draftPath}/review`: {
        const { decisions } = route.request().postDataJSON();
        expect(decisions).toEqual([{ id: "item_1", decision: "rejected" }]);
        draft.items[0].review = decisions[0];
        return route.fulfill({ json: { draft } });
      }
      case `${draftPath}/finalize`:
        return route.fulfill({ json: { dataset: { dataset_id: "dataset_fixture", row_count: 1 } } });
      default:
        return route.fulfill({ status: 404, json: { error: { message: "Unexpected mock request" } } });
    }
  });

  await page.goto("/labels.html");
  await expect(page.getByLabel("Saved digest").locator("option")).toHaveCount(1);
  await expect(page.getByLabel("Sources")).toBeHidden();
  await page.getByRole("button", { name: "Start draft" }).click();
  await expect(page.getByText(claim, { exact: true })).toBeVisible();
  await expect(page.getByText(reason, { exact: true })).toBeVisible();
  await expect(page.getByText(priorText, { exact: true })).toBeVisible();
  await expect(page.getByText("P1 (cited) · Panel rehearsal", { exact: false })).toBeVisible();
  await expect(page.getByText("The new panel moves inward.")).toBeVisible();
  await expect(page.locator("#itemText b, #rationale i, #priorPassages img")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Edit", exact: true })).toBeHidden();
  await page.getByRole("button", { name: "Reject" }).click();
  await page.getByRole("button", { name: "Finalize dataset" }).click();
  await expect(page.getByText("Dataset saved as dataset_fixture")).toBeVisible();
});
