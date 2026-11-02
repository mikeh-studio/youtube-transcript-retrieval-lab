import { expect, test } from "./fixtures";

test("preserves scoped evidence and requires review before answering", async ({
  page,
}) => {
  const requests: { path: string; body: any }[] = [];
  const row = {
    id: "p1",
    video_id: "video1",
    video_title: "First video",
    chunk_index: 0,
    start: 10,
    end: 20,
    text: "Original transcript passage.",
    url: "https://www.youtube.com/watch?v=video1&t=10s",
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("yt_rag_intro_seen", "1");
    sessionStorage.setItem(
      "yt_rag_search_scope",
      JSON.stringify({ mode: "videos", videoIds: ["video1"], at: Date.now() }),
    );
  });
  await page.route("**/v1/videos", (route) =>
    route.fulfill({
      json: {
        videos: [
          { video_id: "video1", title: "First video", num_chunks: 2 },
          { video_id: "video2", title: "Second video", num_chunks: 1 },
        ],
      },
    }),
  );
  await page.route("**/v1/search", (route) => {
    requests.push({ path: "search", body: route.request().postDataJSON() });
    return route.fulfill({ json: { results: [row] } });
  });
  await page.route("**/v1/evidence/**", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop()!;
    const body = route.request().postDataJSON();
    requests.push({ path, body });
    if (path === "snapshots")
      return route.fulfill({ json: { snapshot_id: "s1", passages: [row] } });
    if (path === "estimate")
      return route.fulfill({
        json: {
          estimated_cost: null,
          snapshot_id: "s1",
          estimate_id: "e1",
          input_tokens_estimate: 620,
          estimate_basis: "Approximate character heuristic.",
        },
      });
    if (path === "answer")
      return route.fulfill({
        json: { answer: "A cited answer.", citations: [] },
      });
    return route.fulfill({ json: { passages: [] } });
  });
  await page.goto("/index.html#/qa");
  await expect(page.getByText("First video").first()).toBeVisible();
  await page.getByLabel("Query").fill("What happened?");
  await page.getByRole("button", { name: "Find passages" }).click();
  await expect(
    page.getByText("Original transcript passage.", { exact: true }),
  ).toBeVisible();
  const reviewButton = page.getByRole("button", {
    name: "Review cost and answer",
  });
  await expect(reviewButton).toBeInViewport();
  await expect(page.locator(".workspace-reading-scroll")).toHaveAttribute(
    "tabindex",
    "0",
  );
  expect(requests.find((r) => r.path === "search")?.body.video_ids).toEqual([
    "video1",
  ]);
  await page.getByRole("button", { name: "Review cost and answer" }).click();
  await expect(page.getByText("Estimated cost unavailable.")).toBeVisible();
  await expect(page.getByText("Estimated input tokens: 620")).toBeVisible();
  await expect(
    page.getByText("Approximate character heuristic."),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Generate answer" }),
  ).toBeDisabled();
  await page
    .getByLabel(
      "I understand the cost is unavailable and generation may incur charges.",
    )
    .check();
  await page.getByRole("button", { name: "Generate answer" }).click();
  await expect(page.getByText("A cited answer.")).toBeVisible();
  expect(requests.find((r) => r.path === "answer")?.body).toMatchObject({
    snapshot_id: "s1",
    passage_ids: ["p1"],
    estimate_id: "e1",
    cost_acknowledged: true,
  });
  await page.getByLabel("Query").fill("A different question");
  await expect(page.getByText("A cited answer.")).toHaveCount(0);
});

test("keeps selected collapsed context and applies saved answer language", async ({
  page,
}) => {
  const requests: { path: string; body: any }[] = [];
  const original = {
    id: "p1",
    video_id: "video1",
    video_title: "First video",
    chunk_index: 0,
    start: 10,
    end: 20,
    text: "Original passage.",
    url: "https://www.youtube.com/watch?v=video1&t=10s",
  };
  const neighbor = {
    ...original,
    id: "p2",
    chunk_index: 1,
    start: 20,
    end: 30,
    text: "Neighbor passage.",
  };
  await page.addInitScript(() => {
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("**/v1/videos", (route) =>
    route.fulfill({
      json: {
        videos: [{ video_id: "video1", title: "First video", num_chunks: 2 }],
      },
    }),
  );
  await page.route("**/v1/workspace/settings", (route) =>
    route.fulfill({ json: { providers: [] } }),
  );
  await page.route("**/v1/search", (route) =>
    route.fulfill({ json: { results: [original] } }),
  );
  await page.route("**/v1/evidence/**", (route) => {
    const path = new URL(route.request().url()).pathname.split("/").pop()!;
    const body = route.request().postDataJSON();
    requests.push({ path, body });
    if (path === "snapshots")
      return route.fulfill({
        json: {
          snapshot_id: "s1",
          passages: body.passages.map((p: any) =>
            p.chunk_index === 0 ? original : neighbor,
          ),
        },
      });
    if (path === "context")
      return route.fulfill({ json: { passages: [original, neighbor] } });
    if (path === "estimate")
      return route.fulfill({
        json: { estimated_cost: null, estimate_id: "e1" },
      });
    return route.fulfill({ json: { answer: "回答", citations: [] } });
  });
  await page.goto("/index.html#/settings");
  await page.getByLabel("Output language").selectOption("ja");
  await page.getByRole("button", { name: "Save defaults" }).click();
  await page
    .getByRole("button", { name: "Search", exact: true })
    .first()
    .click();
  await page.getByLabel("Query").fill("What happened?");
  await page.reload();
  await expect(page.getByLabel("Query")).toHaveValue("What happened?");
  await page.getByRole("button", { name: "Find passages" }).click();
  await page
    .getByRole("button", { name: /Read surrounding transcript/ })
    .click();
  await page
    .locator(".workspace-context")
    .getByLabel("Include in answer")
    .check();
  await page
    .getByRole("button", { name: /Read surrounding transcript/ })
    .click();
  await expect(page.getByText("2 passages selected")).toBeVisible();
  await page.getByRole("button", { name: "Review cost and answer" }).click();
  await expect(page.getByText("Estimated cost unavailable.")).toBeVisible();
  const selectedSnapshot = requests
    .filter((r) => r.path === "snapshots")
    .at(-1)!;
  expect(selectedSnapshot.body.passages.map((p: any) => p.chunk_index)).toEqual(
    [0, 1],
  );
  expect(requests.find((r) => r.path === "estimate")?.body).toMatchObject({
    passage_ids: ["p1", "p2"],
    answer_language: "ja",
  });
  await page
    .getByLabel(
      "I understand the cost is unavailable and generation may incur charges.",
    )
    .check();
  await page.getByRole("button", { name: "Generate answer" }).click();
  await expect
    .poll(() => requests.find((r) => r.path === "answer")?.body.answer_language)
    .toBe("ja");
});

test("keeps OCR ingestion and multimodal Ask in the Local Video tool", async ({
  page,
}) => {
  let requestBody = null;
  let searchRequestBody = null;
  let ocrJobRequests = 0;
  await page.route("**/v1/local-video-ocr/jobs", async (route) => {
    ocrJobRequests += 1;
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        jobs: [
          {
            job_id: "ocr-demo-1",
            video_id: "demo_001",
            video_path: "data/raw/demo_001.mp4",
            status: "completed",
            step: "indexed",
            frame_count: 12,
            ocr_count: 10,
            vector_count: 10,
            interval_sec: 10,
          },
        ],
      }),
    });
  });
  await page.route("**/v1/search-multimodal", async (route) => {
    searchRequestBody = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        query: "scoreboard",
        video_id_filter: "demo_001",
        source_mode: "ocr",
        retrieval_mode: "hybrid",
        result_count: 1,
        results: [
          {
            source_type: "ocr",
            video_id: "demo_001",
            video_title: "demo_001",
            frame_id: "frame_000120",
            frame_path: "data/frames/demo_001/frame_000120.jpg",
            timestamp_label: "2:00",
            snippet: "Q4 98 96",
          },
        ],
      }),
    });
  });
  await page.route("**/v1/ask-multimodal", async (route) => {
    requestBody = route.request().postDataJSON();
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        question: "What score is visible?",
        video_id: "demo_001",
        source_mode: "ocr",
        status: "answered",
        confidence: "high",
        answer: "The scoreboard shows 98-96 in the fourth quarter [1].",
        citations: [
          {
            citation_id: 1,
            source_type: "ocr",
            video_id: "demo_001",
            video_title: "demo_001",
            frame_id: "frame_000120",
            frame_path: "data/frames/demo_001/frame_000120.jpg",
            timestamp_label: "2:00",
            timestamp_range_label: "2:00",
            snippet: "Q4 98 96",
          },
        ],
        retrieved_chunks: [],
        warnings: [],
        provider: "chatgpt",
        model: "gpt-5.4-mini",
      }),
    });
  });

  await page.goto("/index.html#/local-video");
  await expect(
    page.getByRole("heading", { name: "Local Video Analysis" }),
  ).toBeVisible();

  const searchPanel = page.locator(".local-video-tool-panel").filter({
    has: page.getByRole("heading", { name: "Search Local Video Evidence" }),
  });
  await expect(searchPanel.getByLabel("Video ID")).toHaveValue("demo_001");
  await searchPanel.getByLabel("Search query").fill("scoreboard");
  await searchPanel.getByRole("button", { name: "Search Local Video" }).click();

  await expect
    .poll(() => searchRequestBody)
    .toMatchObject({
      query: "scoreboard",
      video_id: "demo_001",
      source_mode: "ocr",
      retrieval_mode: "hybrid",
    });
  await expect(page.getByTestId("local-video-search-result")).toContainText(
    "Q4 98 96",
  );

  const askPanel = page.locator(".local-video-ask-panel");
  await expect(askPanel.getByLabel("Video ID")).toHaveValue("demo_001");
  await askPanel.getByLabel("Question").fill("What score is visible?");
  await askPanel.getByRole("button", { name: "Ask Local Video" }).click();

  await expect
    .poll(() => requestBody)
    .toMatchObject({
      question: "What score is visible?",
      video_id: "demo_001",
      source_mode: "ocr",
      retrieval_mode: "hybrid",
    });
  await expect(page.getByTestId("local-video-answer-panel")).toContainText(
    "98-96",
  );
  await expect(page.getByTestId("local-video-evidence-card")).toHaveCount(1);
  await expect(page.getByTestId("local-video-evidence-card")).toContainText(
    "Q4 98 96",
  );

  const requestCountAfterLoad = ocrJobRequests;
  await page.waitForTimeout(3200);
  expect(ocrJobRequests).toBe(requestCountAfterLoad);
});

test("localizes the Local Video tool in Japanese", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("youtube-rag-ui-locale", "ja-JP");
  });
  await page.route("**/v1/local-video-ocr/jobs", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ok: true, jobs: [] }),
    });
  });

  await page.goto("/index.html#/local-video");

  await expect(
    page.getByRole("heading", { name: "ローカル動画分析" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "ローカル動画の根拠を検索" }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "OCR を実行" })).toBeVisible();
  await expect(
    page.getByRole("button", { name: "ローカル動画に質問" }),
  ).toBeVisible();
});

for (const status of ["answered", "insufficient_evidence", "error"]) {
  test(`shows ${status} and preserves selected canonical sources`, async ({
    page,
  }) => {
    const row = {
      id: "p1",
      video_id: "vid1",
      video_title: "Stephen Curry interview",
      chunk_index: 0,
      start: 65,
      end: 88,
      text: "A <b>literal</b> transcript.",
      url: "https://www.youtube.com/watch?v=vid1&t=65s",
    };
    let answers = 0;
    await page.addInitScript(() => {
      sessionStorage.setItem("yt_rag_intro_seen", "1");
    });
    await page.route("**/v1/videos", (route) =>
      route.fulfill({
        json: { videos: [{ video_id: "vid1", title: row.video_title }] },
      }),
    );
    await page.route("**/v1/search", (route) =>
      route.fulfill({ json: { results: [row] } }),
    );
    await page.route("**/v1/evidence/**", (route) => {
      const path = new URL(route.request().url()).pathname.split("/").pop();
      if (path === "snapshots")
        return route.fulfill({ json: { snapshot_id: "s1", passages: [row] } });
      if (path === "estimate")
        return route.fulfill({
          json: { estimate_id: "e1", estimated_cost: null },
        });
      if (path === "answer") {
        answers += 1;
        return route.fulfill({
          json: {
            status: status === "error" && answers > 1 ? "answered" : status,
            answer: "Answer text",
            confidence: "low",
            warnings:
              status === "answered" ? [] : ["Evidence or model unavailable."],
            citations:
              status === "answered"
                ? [
                    {
                      video_id: "vid1",
                      video_title: row.video_title,
                      timestamp_range_label: "1:05–1:28",
                      text: row.text,
                      url: row.url,
                    },
                  ]
                : [],
          },
        });
      }
      return route.fulfill({ json: { passages: [] } });
    });
    await page.goto("/index.html#/qa");
    await page.getByLabel("Query").fill("What was said?");
    await page.getByRole("button", { name: "Find passages" }).click();
    await expect(
      page.getByText("Speaker not identified · Original transcript"),
    ).toBeVisible();
    await expect(page.locator(".workspace-passage b")).toHaveCount(0);
    await page.getByRole("button", { name: "Review cost and answer" }).click();
    await page
      .getByLabel(
        "I understand the cost is unavailable and generation may incur charges.",
      )
      .check();
    await page.getByRole("button", { name: "Generate answer" }).click();
    await expect(page.getByTestId("answer-status")).toContainText(
      status === "answered"
        ? "Answered from evidence"
        : status === "error"
          ? "Answer unavailable"
          : "Insufficient evidence",
    );
    await expect(
      page.locator(".workspace-passage").getByText(row.text),
    ).toBeVisible();
    if (status === "answered") {
      await expect(
        page.locator(".workspace-answer").getByRole("link"),
      ).toHaveAttribute("href", row.url);
      await expect(page.locator(".workspace-answer")).toContainText(
        "1:05–1:28",
      );
      await expect(page.locator(".workspace-answer b")).toHaveCount(0);
      await expect(page.locator(".workspace-answer")).toContainText(row.text);
    } else {
      await expect(page.locator(".workspace-answer")).toContainText(
        "Evidence or model unavailable.",
      );
    }
    if (status === "error") {
      await page.getByRole("button", { name: "Retry generation" }).click();
      await expect(page.getByTestId("answer-status")).toContainText(
        "Answered from evidence",
      );
      expect(answers).toBe(2);
    }
    await page.getByRole("button", { name: "Change sources" }).click();
    await page
      .getByRole("checkbox", { name: "Stephen Curry interview", exact: true })
      .check();
    await expect(page.locator(".workspace-answer")).toHaveCount(0);
  });
}

test("groups consecutive sources without changing ranked passage order", async ({
  page,
}) => {
  const rows = [
    {
      id: "p1",
      video_id: "a",
      video_title: "A long source title for the first video",
      chunk_index: 0,
      start: 10,
      end: 20,
      text: "First exact passage.",
      url: "https://www.youtube.com/watch?v=a&t=10s",
    },
    {
      id: "p2",
      video_id: "a",
      video_title: "A long source title for the first video",
      chunk_index: 1,
      start: 20,
      end: 30,
      text: "Second exact passage.",
      url: "https://www.youtube.com/watch?v=a&t=20s",
    },
    {
      id: "p3",
      video_id: "b",
      video_title: "Second source",
      chunk_index: 0,
      start: 30,
      end: 40,
      text: "Third exact passage.",
      url: "https://www.youtube.com/watch?v=b&t=30s",
    },
    {
      id: "p4",
      video_id: "a",
      video_title: "A long source title for the first video",
      chunk_index: 2,
      start: 40,
      end: 50,
      text: "Fourth exact passage.",
      url: "https://www.youtube.com/watch?v=a&t=40s",
    },
  ];
  await page.addInitScript(() =>
    sessionStorage.setItem("yt_rag_intro_seen", "1"),
  );
  await page.route("**/v1/videos", (route) =>
    route.fulfill({ json: { videos: [] } }),
  );
  await page.route("**/v1/search", (route) =>
    route.fulfill({ json: { results: rows } }),
  );
  await page.route("**/v1/evidence/**", (route) =>
    route.fulfill({ json: { snapshot_id: "s1", passages: rows } }),
  );
  await page.goto("/index.html#/qa");
  await page.getByLabel("Query").fill("Find evidence");
  await page.getByRole("button", { name: "Find passages" }).click();

  await expect(page.locator(".workspace-reading-source")).toHaveText([
    rows[0].video_title,
    rows[2].video_title,
    rows[3].video_title,
  ]);
  await expect(page.locator(".workspace-sidebar-source")).toHaveText([
    rows[0].video_title,
    rows[2].video_title,
    rows[3].video_title,
  ]);
  await expect(
    page.locator(".workspace-sidebar-source").first(),
  ).toHaveAttribute("title", rows[0].video_title);
  await expect(page.locator(".workspace-transcript")).toHaveText(
    rows.map((row) => row.text),
  );
  await expect(
    page.getByRole("link", { name: /Passage 2, A long source title/ }),
  ).toBeVisible();
  await expect(
    page.getByRole("link", { name: /Open Second source at/ }),
  ).toHaveAttribute("href", rows[2].url);
  await page.getByRole("link", { name: /Passage 3, Second source/ }).click();
  await expect(
    page.locator(".workspace-reader aside a.active"),
  ).toHaveAttribute("href", "#passage-b-0");
  await expect(page.getByText("4 passages selected")).toBeVisible();
});
