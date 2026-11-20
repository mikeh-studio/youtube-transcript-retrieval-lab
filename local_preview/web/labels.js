import { renderLabNav } from "./lab-nav.js";
import { audioPlayback, remoteTimingNote } from "./audio-playback.js";

renderLabNav({ active: "evaluate" });

const $ = (id) => document.getElementById(id);
const state = {
  tasks: [],
  videos: [],
  drafts: [],
  draft: null,
  index: 0,
  saving: false,
  starting: false,
  listRequest: 0,
  draftRequest: 0,
  contextRequest: 0,
  digestsRequest: 0,
};
const apiRoot = "/v1/labels";
const draftPath = (id) => `${apiRoot}/drafts/${encodeURIComponent(id)}`;
const currentItem = () => state.draft?.items[state.index];
const effectiveItem = (item) => ({ ...item, ...item.review?.edits });
const isAudit = () => state.draft?.task === "judge_audit";
const isSections = () => state.draft?.task === "sections";
const isMultiSourceQA = () => state.draft?.task === "multi_source_qa";
const isCollectionThemes = () => state.draft?.task === "collection_themes";
const isReference = () => state.draft?.task === "stt_reference";
const isLanding = () => state.draft?.task === "ts_landing";
const isNovelty = () => state.draft?.task === "digest_novelty";
const draftSource = (id) => state.draft.sources.find((source) => source.source_id === id);

async function api(path, body) {
  const response = await fetch(path, body === undefined ? undefined : {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok || payload.ok === false) {
    const error = typeof payload.error === "string" ? payload.error : payload.error?.message;
    throw new Error(error || `Request failed (${response.status}).`);
  }
  return payload;
}

function timecode(seconds, precision = false) {
  const value = precision ? Number(seconds.toFixed(3)) : Math.floor(seconds);
  const whole = Math.floor(value);
  const secondsPart = String(whole % 60).padStart(2, "0");
  const fraction = String(value).split(".")[1];
  return `${Math.floor(whole / 60)}:${secondsPart}${fraction ? `.${fraction}` : ""}`;
}

function timestampUrl(source, onset) {
  const playback = source.playback === undefined ? { kind: "youtube", url: source.url } : source.playback;
  if (!playback?.url) return "";
  try {
    const url = playback.kind === "media" ? new URL(playback.url, location.origin) : new URL(playback.url);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "";
    const seconds = Math.max(0, Math.floor(onset));
    if (playback.kind === "media") {
      url.hash = `t=${seconds}`;
    } else if (playback.kind === "youtube" && (
      url.hostname === "youtube.com" || url.hostname.endsWith(".youtube.com") || url.hostname === "youtu.be"
    )) {
      url.searchParams.set("t", `${seconds}s`);
    } else {
      return "";
    }
    return url.href;
  } catch {
    return "";
  }
}

function populateDrafts(selected) {
  $("drafts").replaceChildren(new Option("Choose a draft", ""));
  for (const draft of state.drafts) {
    const title = draft.sources.map((source) => source.title).join(", ");
    const status = draft.status === "finalized" ? "finalized" : `${draft.decided_count}/${draft.item_count} decided`;
    $("drafts").add(new Option(`${title} · ${draft.language} · ${status}`, draft.draft_id));
  }
  $("drafts").value = selected ?? "";
  $("drafts").disabled = state.saving || !state.drafts.length;
}

async function refreshDrafts() {
  const request = ++state.listRequest;
  const { drafts } = await api(`${apiRoot}/drafts`);
  if (request !== state.listRequest) return;
  state.drafts = drafts;
  populateDrafts($("drafts").value);
  $("draftStatus").textContent = drafts.length ? "" : "No drafts yet.";
}

async function openDraft(id) {
  const request = ++state.draftRequest;
  state.draft = null;
  state.index = 0;
  $("review").hidden = true;
  clearReviewAudio();
  $("reviewStatus").textContent = id ? "Loading draft…" : "Choose a saved draft to review.";
  if (!id) return;
  try {
    const { draft } = await api(draftPath(id));
    if (request !== state.draftRequest) return;
    state.draft = draft;
    const pending = draft.items.findIndex((item) => !item.review);
    state.index = Math.max(0, pending);
    $("reviewStatus").textContent = "";
    renderItem();
    $("itemHeading").focus();
  } catch (error) {
    if (request === state.draftRequest) $("reviewStatus").textContent = error.message;
  }
}

function transcriptRows(lines) {
  return lines.map((line) => {
    const row = document.createElement("li");
    const time = document.createElement("time");
    const text = document.createElement("span");
    time.textContent = `[${String(line.line).padStart(4, "0")}] ${timecode(line.start)}`;
    text.textContent = line.text;
    row.append(time, text);
    return row;
  });
}

async function loadContext(draftID, itemID) {
  const request = ++state.contextRequest;
  $("context").replaceChildren();
  $("context").hidden = true;
  $("contextGroups").replaceChildren();
  $("contextStatus").textContent = "Loading transcript context…";
  try {
    const { context } = await api(`${draftPath(draftID)}/context?item=${encodeURIComponent(itemID)}`);
    if (request !== state.contextRequest || state.draft?.draft_id !== draftID) return;
    if (context.groups) {
      for (const group of context.groups) {
        const heading = document.createElement("h4");
        heading.textContent = draftSource(group.source_id).title;
        const list = document.createElement("ul");
        list.className = "label-context";
        list.tabIndex = 0;
        list.append(...transcriptRows(group.lines));
        $("contextGroups").append(heading, list);
      }
      $("contextStatus").textContent = "";
      return;
    }
    $("context").replaceChildren(...transcriptRows(context.lines));
    $("context").hidden = !context.lines.length;
    $("contextStatus").textContent = context.lines.length ? "" : "No transcript lines overlap this span.";
  } catch (error) {
    if (request === state.contextRequest && state.draft?.draft_id === draftID) {
      $("contextStatus").textContent = error.message;
    }
  }
}

function updateControls() {
  const { draft, index, saving } = state;
  const final = draft.status === "finalized";
  const audit = isAudit();
  const decided = draft.items.filter((item) => item.review).length;
  const accepted = audit || isNovelty() || draft.items.some((item) => item.review && item.review.decision !== "rejected");
  const editing = !$("editForm").hidden;
  $("progress").textContent = `${decided} of ${draft.items.length} decided`;
  $("previous").disabled = saving || editing || index === 0;
  $("next").disabled = saving || editing || index === draft.items.length - 1;
  $("decisionActions").hidden = final || editing;
  $("auditNote").disabled = saving || final;
  for (const id of ["approve", "edit", "reject", "saveEdit", "cancelEdit"]) $(id).disabled = saving;
  $("drafts").disabled = saving;
  $("finalize").hidden = final;
  $("finalize").disabled = saving || editing || decided !== draft.items.length || !accepted;
  $("finalizeStatus").textContent = final
    ? audit
      ? `Owner agreement ${(draft.agreement_rate * 100).toFixed(1)}%`
      : `Dataset saved as ${draft.dataset_id}`
    : !accepted && decided === draft.items.length
      ? "Accept at least one item to finalize."
      : "";
}

function renderCrossSourceDetails(label) {
  const details = $("crossSourceDetails");
  details.replaceChildren();
  details.hidden = !isMultiSourceQA();
  if (details.hidden) return;
  for (const fact of label.required_facts) {
    const row = document.createElement("li");
    row.textContent = fact;
    details.append(row);
  }
  for (const { source_id, start, end } of label.gold_spans) {
    const source = draftSource(source_id);
    const row = document.createElement("li");
    const url = timestampUrl(source, start);
    const evidence = document.createElement(url ? "a" : "span");
    evidence.textContent = `${source.title} · ${timecode(start, true)}–${timecode(end, true)}`;
    if (url) {
      evidence.href = url;
      evidence.target = "_blank";
      evidence.rel = "noopener noreferrer";
    }
    row.append(evidence);
    details.append(row);
  }
}

function renderPriorPassages(item) {
  $("priorEvidence").hidden = !isNovelty();
  const list = $("priorPassages");
  list.replaceChildren();
  if (!isNovelty()) return;
  if (!item.prior_passages.length) {
    const row = document.createElement("li");
    row.textContent = "No prior passages were retrieved.";
    list.append(row);
    return;
  }
  for (const passage of item.prior_passages) {
    const row = document.createElement("li");
    const heading = document.createElement("strong");
    const text = document.createElement("p");
    const cited = item.prior.includes(passage.passage_id) ? " (cited)" : "";
    heading.textContent = `${passage.passage_id}${cited} · ${passage.title} · ${timecode(passage.start, true)}–${timecode(passage.end, true)}`;
    text.textContent = passage.text;
    row.append(heading, text);
    list.append(row);
  }
}

function clearReviewAudio() {
  const player = $("reviewAudio");
  player.onerror = null;
  player.hidden = $("audioNote").hidden = true;
  if (player.hasAttribute("src")) {
    player.removeAttribute("src");
    player.load();
  }
}

function renderReviewAudio(source, label) {
  clearReviewAudio();
  if (!source || !(isReference() || isLanding())) return;
  const { local, remote } = audioPlayback(source.source_id, source.audio || {}, label.start, isReference() ? label.end : null);
  const player = $("reviewAudio");
  const note = $("audioNote");
  function play(src, fallback) {
    player.hidden = !src;
    note.hidden = Boolean(src) && !fallback;
    note.textContent = src ? remoteTimingNote : "Audio is unavailable for this source.";
    if (!src) return;
    player.onerror = () => {
      if (fallback || !remote) clearReviewAudio();
      play(fallback ? "" : remote, true);
    };
    player.src = src;
  }
  play(local || remote, !local);
}

function renderItem() {
  const { draft, index } = state;
  const item = currentItem();
  const label = effectiveItem(item);
  const audit = isAudit();
  const section = isSections();
  const judgment = item.judgment;
  const multiQA = isMultiSourceQA();
  const collectionThemes = isCollectionThemes();
  const audio = isReference() || isLanding();
  const source = draftSource(item.source_id);
  $("review").hidden = false;
  $("editForm").hidden = true;
  $("itemHeading").textContent = `Item ${index + 1} of ${draft.items.length}`;
  const decision = item.review?.decision;
  $("decisionStatus").textContent = decision
    ? audit ? (decision === "approved" ? "Agree" : "Disagree") : decision[0].toUpperCase() + decision.slice(1)
    : "Pending";
  if (multiQA) {
    $("itemText").textContent = label.question;
    $("itemTiming").textContent = "Required facts and gold spans";
  } else if (collectionThemes) {
    $("itemText").textContent = label.title;
    $("itemTiming").textContent = label.source_ids.map((id) => draftSource(id).title).join(" · ");
  } else if (isNovelty()) {
    $("itemText").textContent = item.text;
    const decision = item.relation === "contradicts" ? "Approve if the claim truly contradicts the cited evidence."
      : "Approve if the claim is truly new relative to the prior evidence.";
    $("itemTiming").textContent = `${decision}${item.downgraded ? " The model's original relation had no valid citation." : ""}`;
  } else if (audio) {
    $("itemText").textContent = label.text;
    $("itemTiming").textContent = isLanding()
      ? `Sample start ${timecode(label.start, true)}. Approve if the excerpt lands within one second.${label.offset_seconds === undefined ? "" : ` Recorded offset ${label.offset_seconds} s.`}`
      : `Excerpt ${timecode(label.start, true)}–${timecode(label.end, true)}. Listen and correct the draft text as needed.`;
  } else {
    $("itemText").textContent = audit ? judgment.prediction_text : section ? label.title : label.text;
    $("itemTiming").textContent = audit
      ? `${source.title} · ${timecode(judgment.start, true)} · ${judgment.judge.model}`
      : section
        ? `Section ${timecode(label.start, true)}–${timecode(label.end, true)}`
        : `Importance ${label.importance}/3 · Onset ${timecode(label.onset, true)} · Span ${timecode(label.span_start, true)}–${timecode(label.span_end, true)}`;
  }
  renderCrossSourceDetails(label);
  renderPriorPassages(item);
  $("rationale").textContent = audit
    ? `Judge ${judgment.kind === "match" ? "match" : "faithfulness"} · ${judgment.decision ?? "No match"}`
    : isNovelty() ? item.reason : item.rationale ? `Draft rationale · ${item.rationale}` : "";
  $("rationale").hidden = !$("rationale").textContent;
  const points = judgment?.gold_points ?? [];
  $("auditGoldPoints").replaceChildren(...points.map((point) => {
    const row = document.createElement("li");
    row.textContent = `${point.id} · ${point.text}`;
    return row;
  }));
  $("auditGoldPoints").hidden = !points.length;
  $("auditNoteField").hidden = !audit;
  $("auditNote").value = item.review?.note ?? "";
  $("approve").textContent = audit ? "Agree" : "Approve";
  $("reject").textContent = audit ? "Disagree" : "Reject";
  $("edit").hidden = audit || isNovelty();
  $("edit").textContent = isLanding() ? "Record offset" : "Edit";
  $("reviewKeys").hidden = audit || isNovelty();
  $("noveltyKeys").hidden = !isNovelty();
  $("notes").textContent = draft.notes ? `Draft notes · ${draft.notes}` : "";
  $("notes").hidden = !draft.notes;
  const url = audio || audit || !source ? "" : timestampUrl(source, section || isNovelty() ? label.start : label.onset);
  $("timestamp").hidden = !url;
  if (url) $("timestamp").href = url;
  else $("timestamp").removeAttribute("href");
  renderReviewAudio(source, label);
  updateControls();
  $("contextHeading").textContent = collectionThemes ? "Transcript excerpts" : "Transcript context";
  $("contextSection").hidden = audit && judgment.kind === "match";
  if ($("contextSection").hidden) ++state.contextRequest;
  else void loadContext(draft.draft_id, item.id);
}

function move(offset) {
  const next = state.index + offset;
  if (!state.draft || state.saving || !$("editForm").hidden || next < 0 || next >= state.draft.items.length) return;
  state.index = next;
  renderItem();
  $("itemHeading").focus();
}

function startEdit() {
  if ($("edit").disabled || $("decisionActions").hidden) return;
  const item = effectiveItem(currentItem());
  const form = $("editForm");
  const section = isSections();
  const multiQA = isMultiSourceQA();
  const collectionThemes = isCollectionThemes();
  const multiple = multiQA || collectionThemes;
  const landing = isLanding();
  const reference = isReference();
  const hideSpan = multiple || landing || reference;
  $("singleSourceFields").hidden = hideSpan;
  form.elements.text.disabled = $("editTextLabel").closest("label").hidden = landing;
  form.elements.text.required = !reference;
  for (const [id, name, enabled] of [
    ["editFactsField", "required_facts", multiQA],
    ["editSpansField", "gold_spans", multiQA],
    ["editSourcesField", "source_ids", collectionThemes],
    ["editOffsetField", "offset_seconds", landing],
  ]) {
    form.elements[name].disabled = $(id).hidden = !enabled;
  }
  for (const name of ["importance", "onset", "span_start", "span_end"]) {
    form.elements[name].disabled = hideSpan;
  }
  $("editTextLabel").textContent = reference ? "Reference transcript" : section ? "Section title" : "Label text";
  $("editStartLabel").textContent = section ? "Section start (seconds)" : "Span start (seconds)";
  $("editEndLabel").textContent = section ? "Section end (seconds)" : "Span end (seconds)";
  form.elements.text.value = section ? item.title : item.text;
  form.elements.span_start.value = section ? item.start : item.span_start;
  form.elements.span_end.value = section ? item.end : item.span_end;
  for (const name of ["importance", "onset"]) {
    const field = form.elements[name];
    field.disabled = field.closest("label").hidden = section || hideSpan;
    field.value = item[name] ?? "";
  }
  if (landing) {
    form.elements.offset_seconds.value = item.offset_seconds ?? "";
    form.elements.offset_seconds.min = -item.start;
    form.elements.offset_seconds.max = draftSource(item.source_id).duration - item.start;
  } else if (multiQA) {
    $("editTextLabel").textContent = "Question";
    form.elements.text.value = item.question;
    form.elements.required_facts.value = item.required_facts.join("\n");
    form.elements.gold_spans.value = item.gold_spans.map(
      ({ source_id, start, end }) => `${source_id} ${start} ${end}`,
    ).join("\n");
    $("editSpanSources").textContent = state.draft.sources.map(
      (source) => `${source.title} (${source.source_id})`,
    ).join(" · ");
    form.elements.gold_spans.setCustomValidity("");
  } else if (collectionThemes) {
    $("editTextLabel").textContent = "Theme title";
    form.elements.text.value = item.title;
    form.elements.source_ids.replaceChildren(...state.draft.sources.map((source) =>
      new Option(source.title, source.source_id, false, item.source_ids.includes(source.source_id)),
    ));
  } else {
    const source = draftSource(item.source_id);
    for (const name of ["onset", "span_start", "span_end"]) form.elements[name].max = source.duration;
  }
  form.hidden = false;
  updateControls();
  (landing ? form.elements.offset_seconds : form.elements.text).focus();
}

async function mutate(action, body) {
  const draftID = state.draft.draft_id;
  state.saving = true;
  updateControls();
  $("reviewStatus").textContent = action === "review" ? "Saving decision…" : "Finalizing dataset…";
  try {
    const result = await api(`${draftPath(draftID)}/${action}`, body);
    if (action === "review") {
      state.draft = result.draft;
      const nextPending = state.draft.items.findIndex((item, index) => index > state.index && !item.review);
      if (nextPending !== -1) state.index = nextPending;
      $("reviewStatus").textContent = "Decision saved.";
    } else {
      state.draft = { ...state.draft, status: "finalized", dataset_id: result.dataset.dataset_id,
        agreement_rate: result.dataset.agreement_rate };
      $("reviewStatus").textContent = "Dataset finalized.";
    }
    const entry = state.drafts.find((draft) => draft.draft_id === draftID);
    const { status, dataset_id, items } = state.draft;
    if (entry) {
      Object.assign(entry, { status, dataset_id, decided_count: items.filter((item) => item.review).length });
    }
    populateDrafts(draftID);
    renderItem();
    $("itemHeading").focus();
  } catch (error) {
    $("reviewStatus").textContent = error.message;
  } finally {
    state.saving = false;
    updateControls();
  }
}

function decide(decision, edits) {
  if (!state.draft || state.saving || state.draft.status === "finalized") return;
  const row = { id: currentItem().id, decision };
  if (edits) row.edits = edits;
  if (isAudit() && decision === "rejected" && $("auditNote").value.trim()) {
    row.note = $("auditNote").value.trim();
  }
  void mutate("review", { decisions: [row] });
}

function setSourceCount() {
  const task = state.tasks.find((entry) => entry.task === $("task").value);
  const audit = task?.input === "judge_audit_sample";
  const audio = task?.input === "stt_segments";
  const digest = task?.input === "digest";
  $("sources").multiple = task?.max_sources > 1;
  $("sourceField").hidden = $("languageField").hidden = audit || digest;
  $("sources").disabled = $("language").disabled = audit || digest;
  $("digestField").hidden = $("digests").disabled = !digest;
  $("auditFileField").hidden = !audit;
  $("auditFile").disabled = !audit;
  $("auditFile").required = audit;
  $("seedField").hidden = $("seed").disabled = !audio;
  $("language").readOnly = audio;
  $("startJob").textContent = audit ? "Import audit sample" : "Start draft";
  $("startJob").disabled = state.starting || !task ||
    (digest ? !$("digests").value : !audit && !$("sources").options.length);
}

async function loadDigests() {
  const request = ++state.digestsRequest;
  $("digests").replaceChildren();
  setSourceCount();
  $("jobStatus").textContent = "Loading saved digests…";
  try {
    const { digests } = await api("/v1/digests");
    if (request !== state.digestsRequest) return;
    for (const digest of digests.filter((row) => row.reviewable_count > 0)) {
      $("digests").add(new Option(
        `${digest.created_at} · ${digest.release_titles.join(", ")}`, digest.digest_id,
      ));
    }
    $("jobStatus").textContent = $("digests").length ? "" : "No saved digest has claims to review.";
    setSourceCount();
  } catch (error) {
    if (request === state.digestsRequest) $("jobStatus").textContent = error.message;
  }
}

function populateSources() {
  const task = state.tasks.find((entry) => entry.task === $("task").value);
  const selected = new Set(Array.from($("sources").selectedOptions, (option) => option.value));
  const audio = task?.input === "stt_segments";
  const videos = audio ? state.videos.filter(({ source }) =>
    ["podcast_rss", "local_audio"].includes(source?.platform) &&
    source.stt?.engine && source.stt.model && (source.local_media_path || source.media_url),
  ) : state.videos;
  $("sources").multiple = task?.max_sources > 1;
  $("sources").replaceChildren(...videos.map((video) =>
    new Option(video.title, video.video_id, false, selected.has(video.video_id)),
  ));
  syncAudioLanguage();
}

function syncAudioLanguage() {
  const task = state.tasks.find((entry) => entry.task === $("task").value);
  if (task?.input === "stt_segments") {
    $("language").value = state.videos.find((video) => video.video_id === $("sources").value)?.language || "";
  }
}

async function pollJob(job) {
  while (["queued", "running"].includes(job.status)) {
    $("jobStatus").textContent = job.status === "queued" ? "Draft job queued…" : "Drafting labels…";
    await new Promise((resolve) => setTimeout(resolve, 600));
    ({ job } = await api(`${apiRoot}/jobs/${encodeURIComponent(job.job_id)}`));
  }
  if (job.status !== "completed") throw new Error(job.error_message || "Draft generation failed.");
  return job.draft_id;
}

$("startForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  state.starting = true;
  setSourceCount();
  try {
    const audit = $("task").value === "judge_audit";
    let draftID;
    if (audit) {
      $("jobStatus").textContent = "Importing audit sample…";
      const file = $("auditFile").files[0];
      if (file.size > 10_000_000) throw new Error("Audit sample exceeds the 10 MB limit.");
      let judgments;
      try {
        judgments = (await file.text()).split(/\r?\n/).filter((line) => line.trim()).map((line) => JSON.parse(line));
      } catch {
        throw new Error("Choose a valid judge audit sample JSONL file.");
      }
      const { draft } = await api(`${apiRoot}/judge-audits`, { judgments });
      draftID = draft.draft_id;
    } else {
      const task = state.tasks.find((entry) => entry.task === $("task").value);
      const digest = task.input === "digest";
      const sourceIds = Array.from($("sources").selectedOptions, (option) => option.value);
      if (!digest && (sourceIds.length < task.min_sources || sourceIds.length > task.max_sources)) {
        throw new Error(`Choose ${task.min_sources} to ${task.max_sources} sources.`);
      }
      $("jobStatus").textContent = "Starting draft job…";
      const { job } = await api(`${apiRoot}/jobs`, {
        task: $("task").value,
        ...(digest ? { digest_id: $("digests").value } : {
          source_ids: sourceIds,
          language: $("language").value.trim(),
        }),
        ...(task.input === "stt_segments" ? { seed: $("seed").valueAsNumber } : {}),
      });
      draftID = await pollJob(job);
    }
    await refreshDrafts();
    $("jobStatus").textContent = "New draft ready. Choose it under Saved drafts.";
    if (!$("drafts").value && !state.saving) {
      $("drafts").value = draftID;
      $("jobStatus").textContent = "New draft ready.";
      await openDraft(draftID);
    }
  } catch (error) {
    $("jobStatus").textContent = error.message;
  } finally {
    state.starting = false;
    setSourceCount();
  }
});

$("task").addEventListener("change", () => {
  ++state.digestsRequest;
  populateSources();
  setSourceCount();
  $("jobStatus").textContent = "";
  if ($("task").value === "digest_novelty") void loadDigests();
});
$("sources").addEventListener("change", syncAudioLanguage);
$("drafts").addEventListener("change", () => void openDraft($("drafts").value));
$("previous").addEventListener("click", () => move(-1));
$("next").addEventListener("click", () => move(1));
$("approve").addEventListener("click", () => decide("approved"));
$("reject").addEventListener("click", () => decide("rejected"));
$("edit").addEventListener("click", startEdit);
$("cancelEdit").addEventListener("click", () => {
  $("editForm").hidden = true;
  updateControls();
  $("edit").focus();
});
$("editForm").addEventListener("submit", (event) => {
  event.preventDefault();
  const fields = event.currentTarget.elements;
  if (isReference()) {
    decide("edited", { text: fields.text.value });
    return;
  }
  if (isLanding()) {
    decide("edited", { offset_seconds: fields.offset_seconds.valueAsNumber });
    return;
  }
  if (isMultiSourceQA()) {
    const lines = fields.gold_spans.value.trim().split(/\n/).map((line) => line.trim().split(/\s+/));
    if (lines.some((line) => line.length !== 3 || !line.slice(1).every((value) => Number.isFinite(Number(value))))) {
      fields.gold_spans.setCustomValidity("Enter a source ID followed by start and end seconds on each line.");
      fields.gold_spans.reportValidity();
      return;
    }
    decide("edited", {
      question: fields.text.value,
      required_facts: fields.required_facts.value.split(/\n/).map((line) => line.trim()).filter(Boolean),
      gold_spans: lines.map(([source_id, start, end]) => ({ source_id, start: Number(start), end: Number(end) })),
    });
    return;
  }
  if (isCollectionThemes()) {
    decide("edited", {
      title: fields.text.value,
      source_ids: Array.from(fields.source_ids.selectedOptions, (option) => option.value),
    });
    return;
  }
  if (isSections()) {
    decide("edited", {
      title: fields.text.value,
      start: fields.span_start.valueAsNumber,
      end: fields.span_end.valueAsNumber,
    });
    return;
  }
  decide("edited", {
    text: fields.text.value,
    importance: fields.importance.valueAsNumber,
    onset: fields.onset.valueAsNumber,
    span_start: fields.span_start.valueAsNumber,
    span_end: fields.span_end.valueAsNumber,
  });
});
$("editForm").elements.gold_spans.addEventListener("input", (event) => event.target.setCustomValidity(""));
$("finalize").addEventListener("click", () => void mutate("finalize", {}));
$("review").addEventListener("keydown", (event) => {
  if (
    !state.draft || state.saving || !$("editForm").hidden || event.repeat ||
    event.altKey || event.ctrlKey || event.metaKey || event.target.isContentEditable ||
    event.target.matches("input, select, textarea, audio")
  ) return;
  const buttons = { a: "approve", e: "edit", r: "reject", j: "next", k: "previous" };
  const button = $(buttons[event.key.toLowerCase()]);
  if (!button || button.disabled || button.closest("[hidden]")) return;
  event.preventDefault();
  button.click();
});

Promise.all([api(`${apiRoot}/tasks`), api("/v1/videos")])
  .then(([{ tasks }, { videos }]) => {
    state.tasks = tasks;
    state.videos = videos;
    for (const task of tasks) $("task").add(new Option(task.title, task.task));
    populateSources();
    if (tasks.length) setSourceCount();
    if ($("task").value === "digest_novelty") void loadDigests();
    else if (!videos.length) $("jobStatus").textContent = "Add a source to the Library before drafting.";
  })
  .catch((error) => { $("jobStatus").textContent = error.message; });

$("reviewStatus").textContent = "Choose a saved draft to review.";
refreshDrafts().catch((error) => { $("draftStatus").textContent = error.message; });
