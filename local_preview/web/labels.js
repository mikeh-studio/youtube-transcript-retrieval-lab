import { renderLabNav } from "./lab-nav.js";

renderLabNav({ active: "evaluate" });

const $ = (id) => document.getElementById(id);
const state = {
  tasks: [],
  drafts: [],
  draft: null,
  index: 0,
  saving: false,
  starting: false,
  listRequest: 0,
  draftRequest: 0,
  contextRequest: 0,
};
const apiRoot = "/v1/labels";
const draftPath = (id) => `${apiRoot}/drafts/${encodeURIComponent(id)}`;
const currentItem = () => state.draft?.items[state.index];
const effectiveItem = (item) => ({ ...item, ...item.review?.edits });
const isAudit = () => state.draft?.task === "judge_audit";
const isSections = () => state.draft?.task === "sections";

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
  try {
    const url = new URL(source);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return "";
    url.searchParams.set("t", `${Math.floor(onset)}s`);
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

async function loadContext(draftID, itemID) {
  const request = ++state.contextRequest;
  $("context").replaceChildren();
  $("context").hidden = true;
  $("contextStatus").textContent = "Loading transcript context…";
  try {
    const { context } = await api(`${draftPath(draftID)}/context?item=${encodeURIComponent(itemID)}`);
    if (request !== state.contextRequest || state.draft?.draft_id !== draftID) return;
    const rows = document.createDocumentFragment();
    for (const line of context.lines) {
      const row = document.createElement("li");
      const time = document.createElement("time");
      const text = document.createElement("span");
      time.textContent = `[${String(line.line).padStart(4, "0")}] ${timecode(line.start)}`;
      text.textContent = line.text;
      row.append(time, text);
      rows.append(row);
    }
    $("context").replaceChildren(rows);
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
  const accepted = audit || draft.items.some((item) => item.review && item.review.decision !== "rejected");
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

function renderItem() {
  const { draft, index } = state;
  const item = currentItem();
  const label = effectiveItem(item);
  const audit = isAudit();
  const section = isSections();
  const judgment = item.judgment;
  const source = draft.sources.find((entry) => entry.source_id === item.source_id);
  $("review").hidden = false;
  $("editForm").hidden = true;
  $("itemHeading").textContent = `Item ${index + 1} of ${draft.items.length}`;
  const decision = item.review?.decision;
  $("decisionStatus").textContent = decision
    ? audit ? (decision === "approved" ? "Agree" : "Disagree") : decision[0].toUpperCase() + decision.slice(1)
    : "Pending";
  $("itemText").textContent = audit ? judgment.prediction_text : section ? label.title : label.text;
  $("itemTiming").textContent = audit
    ? `${source.title} · ${timecode(judgment.start, true)} · ${judgment.judge.model}`
    : section
      ? `Section ${timecode(label.start, true)}–${timecode(label.end, true)}`
      : `Importance ${label.importance}/3 · Onset ${timecode(label.onset, true)} · Span ${timecode(label.span_start, true)}–${timecode(label.span_end, true)}`;
  $("rationale").textContent = audit
    ? `Judge ${judgment.kind === "match" ? "match" : "faithfulness"} · ${judgment.decision ?? "No match"}`
    : item.rationale ? `Draft rationale · ${item.rationale}` : "";
  $("rationale").hidden = !audit && !item.rationale;
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
  $("edit").hidden = audit;
  $("reviewKeys").hidden = audit;
  $("notes").textContent = draft.notes ? `Draft notes · ${draft.notes}` : "";
  $("notes").hidden = !draft.notes;
  const url = audit ? "" : timestampUrl(source?.url, section ? label.start : label.onset);
  $("timestamp").hidden = !url;
  if (url) $("timestamp").href = url;
  else $("timestamp").removeAttribute("href");
  updateControls();
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
  $("editTextLabel").textContent = section ? "Section title" : "Label text";
  $("editStartLabel").textContent = section ? "Section start (seconds)" : "Span start (seconds)";
  $("editEndLabel").textContent = section ? "Section end (seconds)" : "Span end (seconds)";
  form.elements.text.value = section ? item.title : item.text;
  form.elements.span_start.value = section ? item.start : item.span_start;
  form.elements.span_end.value = section ? item.end : item.span_end;
  for (const name of ["importance", "onset"]) {
    const field = form.elements[name];
    field.disabled = field.closest("label").hidden = section;
    field.value = item[name] ?? "";
  }
  const source = state.draft.sources.find((entry) => entry.source_id === item.source_id);
  for (const name of ["onset", "span_start", "span_end"]) form.elements[name].max = source.duration;
  form.hidden = false;
  updateControls();
  form.elements.text.focus();
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
  $("sources").multiple = task?.max_sources > 1;
  $("sourceField").hidden = $("languageField").hidden = audit;
  $("sources").disabled = $("language").disabled = audit;
  $("auditFileField").hidden = !audit;
  $("auditFile").disabled = !audit;
  $("auditFile").required = audit;
  $("startJob").textContent = audit ? "Import audit sample" : "Start draft";
  $("startJob").disabled = state.starting || !task || (!audit && !$("sources").options.length);
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
      $("jobStatus").textContent = "Starting draft job…";
      const { job } = await api(`${apiRoot}/jobs`, {
        task: $("task").value,
        source_ids: Array.from($("sources").selectedOptions, (option) => option.value),
        language: $("language").value.trim(),
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
  setSourceCount();
  $("jobStatus").textContent = "";
});
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
$("finalize").addEventListener("click", () => void mutate("finalize", {}));
$("review").addEventListener("keydown", (event) => {
  if (
    !state.draft || state.saving || !$("editForm").hidden || event.repeat ||
    event.altKey || event.ctrlKey || event.metaKey || event.target.isContentEditable ||
    event.target.matches("input, select, textarea")
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
    for (const task of tasks) $("task").add(new Option(task.title, task.task));
    for (const video of videos) $("sources").add(new Option(video.title, video.video_id));
    if (tasks.length) setSourceCount();
    if (!videos.length) $("jobStatus").textContent = "Add a video to the Library before drafting.";
  })
  .catch((error) => { $("jobStatus").textContent = error.message; });

$("reviewStatus").textContent = "Choose a saved draft to review.";
refreshDrafts().catch((error) => { $("draftStatus").textContent = error.message; });
