import type { Page } from "@playwright/test";
import { expect, test } from "./fixtures";

const feedURL = "https://radio.example/feed.xml";
const enclosure = "https://radio.example/kite.wav";
const source = {
  platform: "podcast_rss",
  local_media_path: "media/pod_kite.wav",
  media_url: enclosure,
  stt: { engine: "faster_whisper", model: "fixture" },
};
const episode = {
  video_id: "pod_kite", title: "The paper kite", source_type: "podcast_rss",
  language: "ja", num_chunks: 3, source,
};
const mediaPath = "/v1/sources/pod_kite/media";
const draftPath = "/v1/labels/drafts/draft_audio_fixture";

async function mockWorkspace(page: Page, videos: () => unknown[]) {
  await page.addInitScript(() => {
    localStorage.setItem("yt_rag_ingest_unlocked", "1");
    sessionStorage.setItem("yt_rag_intro_seen", "1");
  });
  await page.route("https://**/*", (route) => route.abort());
  await page.route("**/v1/videos", (route) => route.fulfill({ json: { ok: true, videos: videos() } }));
  await page.route(/\/v1\/catalog\/items/, (route) => route.fulfill({ status: 503, json: { error: "Unavailable" } }));
  await page.route("**/v1/stt/capabilities", (route) => route.fulfill({
    json: { ok: true, capabilities: {
      default_engine: "faster_whisper", av_importable: true,
      faster_whisper_importable: true, openai_key_present: false,
    } },
  }));
}

// A generated four-second PCM silence response exercises native media seeking.
function silence() {
  const samples = 4 * 8000;
  const wav = Buffer.alloc(44 + samples, 128);
  wav.write("RIFF", 0);
  wav.writeUInt32LE(wav.length - 8, 4);
  wav.write("WAVEfmt ", 8);
  wav.writeUInt32LE(16, 16);
  wav.writeUInt16LE(1, 20);
  wav.writeUInt16LE(1, 22);
  wav.writeUInt32LE(8000, 24);
  wav.writeUInt32LE(8000, 28);
  wav.writeUInt16LE(1, 32);
  wav.writeUInt16LE(8, 34);
  wav.write("data", 36);
  wav.writeUInt32LE(samples, 40);
  return wav;
}

test("previews podcast episodes, indexes the selection, and refreshes the Library", async ({ page }) => {
  let indexed = false;
  let allowCompletion = false;
  await mockWorkspace(page, () => indexed ? [episode] : []);
  await page.route("**/v1/feeds/preview", (route) => {
    expect(route.request().postDataJSON()).toEqual({ url: feedURL });
    return route.fulfill({ json: { ok: true, title: "Kite Radio", language: "ja-JP", items: [
      { external_id: "note", title: "Show notes only", media_url: null },
      { external_id: "kite-1", title: episode.title, media_url: enclosure },
    ] } });
  });
  const queued = { job_id: "job_kite", video_id: episode.video_id, status: "queued", step: "queued", progress: 0 };
  await page.route("**/v1/ingest/audio", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      kind: "rss_episode", feed_url: feedURL, guid: "kite-1", language: "ja", engine: "faster_whisper",
    });
    return route.fulfill({ status: 202, json: { ok: true, job: queued } });
  });
  await page.route("**/v1/ingest/jobs", (route) => {
    indexed = allowCompletion;
    const job = indexed ? { ...queued, status: "completed", step: "completed", progress: 1 }
      : { ...queued, status: "running", step: "transcribing", progress: 0.4 };
    return route.fulfill({ json: { ok: true, jobs: [job] } });
  });

  await page.goto("/index.html#/ingest");
  await page.getByText("Add audio", { exact: true }).click();
  await page.getByLabel("RSS feed URL").fill(feedURL);
  await page.getByRole("button", { name: "Preview feed" }).click();
  const episodes = page.getByRole("combobox", { name: "Episode", exact: true });
  await expect(episodes.locator("option")).toHaveText(["Choose an episode", episode.title]);
  await episodes.selectOption("kite-1");
  await expect(page.getByLabel("Audio language")).toHaveValue("ja");
  await expect(page.getByLabel("Transcription engine").locator('option[value="openai"]')).toHaveJSProperty("disabled", true);
  await page.getByRole("button", { name: "Index audio" }).click();
  await expect(page.getByRole("status")).toContainText("transcribing · 40%");
  await expect(page.getByLabel("Audio indexing progress")).toHaveAttribute("value", "0.4");
  allowCompletion = true;
  await expect(page.getByRole("status")).toContainText("Audio indexed.");
  await expect(page.getByRole("row", { name: /The paper kite/ })).toContainText("Podcast");
});

test("submits a local file path and displays a failed job", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockWorkspace(page, () => []);
  const job = { job_id: "job_local", status: "queued", step: "queued", progress: 0 };
  await page.route("**/v1/ingest/audio", (route) => {
    expect(route.request().postDataJSON()).toEqual({
      kind: "local_file", path: "recordings/paper.wav", language: "en", engine: "faster_whisper",
    });
    return route.fulfill({ status: 202, json: { ok: true, job } });
  });
  await page.route("**/v1/ingest/jobs", (route) => route.fulfill({ json: { ok: true, jobs: [] } }));
  await page.route("**/v1/ingest/jobs/job_local", (route) => route.fulfill({
    json: { ok: true, job: { ...job, status: "failed", error_message: "The file has no audio stream." } },
  }));

  await page.goto("/index.html#/ingest");
  await page.getByText("Add audio", { exact: true }).click();
  await page.getByLabel("Audio source").selectOption("local_file");
  await page.getByLabel("Local file path").fill("recordings/paper.wav");
  await page.getByRole("button", { name: "Index audio" }).click();
  await expect(page.getByRole("status")).toContainText("Audio indexing failed. The file has no audio stream.");
  await expect(page.getByRole("button", { name: "Index audio" })).toBeEnabled();
});

for (const local of [true, false]) {
  test(`plays a timestamp in the ${local ? "retained copy" : "original enclosure"}`, async ({ page }) => {
    await mockWorkspace(page, () => [episode]);
    await page.route("**" + mediaPath, (route) => local
      ? route.fulfill({ contentType: "audio/wav", body: silence() })
      : route.fulfill({ status: 404 }));
    await page.route(enclosure, (route) => route.fulfill({ contentType: "audio/wav", body: silence() }));
    await page.route("**/v1/llm-options", (route) => route.fulfill({
      json: { providers: { chatgpt: { default: "fixture", models: [] } } },
    }));
    await page.route("**/v1/summaries/key-areas", (route) => route.fulfill({
      json: {
        overview: { tldr: "The paper kite folds flat." },
        quality: { citation_validity: 1, section_coverage: 1, repaired_boundaries: 0 },
        sections: [{
          start_line: 1, end_line: 1, start: 1.25, end: 3.5, title: "Folding",
          summary: "Fold the kite.", key_points: [],
        }],
      },
    }));

    await page.goto("/index.html#/key-areas");
    await page.getByRole("button", { name: "Generate key areas" }).click();
    await page.getByRole("button", { name: /Play Folding/ }).click();
    const player = page.locator("audio");
    await expect(player).toHaveAttribute("src", `${local ? mediaPath : enclosure}#t=1.25`);
    await expect(player).toHaveAttribute("autoplay", "");
    await expect.poll(() => player.evaluate((audio: HTMLAudioElement) => audio.currentTime)).toBeGreaterThanOrEqual(1.25);
    const note = page.getByText("Playing the original enclosure. Timestamps may drift.");
    if (local) await expect(note).toHaveCount(0);
    else await expect(note).toBeVisible();
    await expect(page.getByTitle("YouTube player")).toHaveCount(0);
  });
}

for (const task of ["stt_reference", "ts_landing"]) {
  test(`reviews ${task} with a timestamped audio player`, async ({ page }) => {
    await mockWorkspace(page, () => [episode]);
    await page.route("**" + mediaPath, (route) => route.fulfill({ contentType: "audio/wav", body: silence() }));
    const text = "<b>Fold the blue kite.</b>";
    const item = { id: "item_1", source_id: episode.video_id, start: 1.25, end: 3.5, text, review: null };
    const draft = {
      draft_id: "draft_audio_fixture", task, language: "ja", status: "pending_review",
      sources: [{ source_id: episode.video_id, title: episode.title, duration: 4, audio: source }],
      items: [item], item_count: 1, decided_count: 0,
    };
    let started = false;
    let decision;
    await page.route("**/v1/labels/tasks", (route) => route.fulfill({
      json: { tasks: [{ task, title: task, min_sources: 1, max_sources: 1, input: "stt_segments" }] },
    }));
    await page.route("**/v1/labels/jobs", (route) => {
      expect(route.request().postDataJSON()).toEqual({
        task, source_ids: [episode.video_id], language: "ja", seed: 7,
      });
      started = true;
      return route.fulfill({ status: 202, json: { job: { status: "completed", draft_id: draft.draft_id } } });
    });
    await page.route("**/v1/labels/drafts", (route) => route.fulfill({ json: { drafts: started ? [draft] : [] } }));
    await page.route("**" + draftPath, (route) => route.fulfill({ json: { draft } }));
    await page.route(`**${draftPath}/context?*`, (route) => route.fulfill({ json: { context: { lines: [] } } }));
    await page.route(`**${draftPath}/review`, (route) => {
      [decision] = route.request().postDataJSON().decisions;
      return route.fulfill({ json: { draft: { ...draft, items: [{ ...item, review: decision }] } } });
    });

    await page.goto("/labels.html");
    await page.getByLabel("Task").selectOption(task);
    await page.getByLabel("Sources").selectOption(episode.video_id);
    await page.getByLabel("Sample seed").fill("7");
    await page.getByRole("button", { name: "Start draft" }).click();
    await expect(page.getByText(text, { exact: true })).toBeVisible();
    await expect(page.locator("#itemText b")).toHaveCount(0);
    await expect(page.locator("#reviewAudio")).toHaveAttribute("src",
      `${mediaPath}#t=1.25${task === "stt_reference" ? ",3.5" : ""}`);

    if (task === "stt_reference") {
      await page.getByRole("button", { name: "Edit", exact: true }).click();
      await page.getByLabel("Reference transcript").fill("Fold the red kite.");
    } else {
      await page.getByRole("button", { name: "Record offset" }).click();
      await page.getByLabel("Actual onset minus sample start (seconds)").fill("-1.2");
    }
    await page.getByRole("button", { name: "Save edit" }).click();
    await expect(page.getByText("1 of 1 decided")).toBeVisible();
    expect(decision).toEqual({
      id: "item_1", decision: "edited",
      edits: task === "stt_reference" ? { text: "Fold the red kite." } : { offset_seconds: -1.2 },
    });
  });
}
