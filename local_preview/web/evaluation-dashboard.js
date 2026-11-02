import { renderLabNav } from "./lab-nav.js";

renderLabNav({ active: "evaluate", locale: "en-US" });
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const show = (value, missing = "Not calculated") =>
  value == null
    ? missing
    : typeof value === "object"
      ? esc(JSON.stringify(value))
      : esc(value);
const state = {
  reports: [],
  report: null,
  profile: 0,
  tab: "overview",
  mode: "workflows",
  baseline: "",
  versionArm: "direct",
  methodA: "",
  methodB: "",
  stage: "initial_retrieval",
  cutoff: "5",
  filter: "",
  search: "",
  filterIds: null,
  notice: "",
};
const labels = {
  video_selection: "Video selection",
  assumption_first_handling: "Assumption-first handling",
  answer_quality: "Answer quality (Good / graded)",
  supporting_passage_recall: "Supporting-passage recall",
  citation_relevance: "Citation relevance",
  citation_completeness: "Citation completeness",
  exact_quote_fidelity: "Exact-quote fidelity",
  timestamp_authenticity: "Timestamp authenticity",
  unnecessary_clarification: "Unnecessary clarification",
  attribution_errors: "Speaker/event attribution errors",
};
const formatNumber = (n) =>
  Number.isInteger(n)
    ? n.toLocaleString("en-US")
    : n.toLocaleString("en-US", { maximumFractionDigits: 4 });
const activeProfile = () => state.report?.profiles?.[state.profile] || {};
const arms = () => activeProfile().arms || {};
const arm = (key) => arms()[key] || {};
const metric = (a, key) =>
  a.metrics?.[key] ?? a.grades?.[key] ?? a.graded_status;
function metricValue(v, fallback = "Awaiting grades") {
  if (v == null) return fallback;
  if (typeof v === "object") {
    const distribution = v.distribution ?? v;
    const counts = ["good", "partial", "poor"].map(
      (k) => distribution[k] ?? distribution[k[0].toUpperCase() + k.slice(1)],
    );
    if (counts.every((n) => typeof n === "number" && Number.isFinite(n)))
      return `Good ${formatNumber(counts[0])} / Partial ${formatNumber(counts[1])} / Poor ${formatNumber(counts[2])}`;
    return v.value != null
      ? `${typeof v.value === "number" ? formatNumber(v.value) : show(v.value)}${v.graded != null ? ` (${show(v.graded)} graded${v.total != null ? ` / ${show(v.total)}` : ""})` : ""}`
      : v.numerator != null && v.denominator != null
        ? `${show(v.numerator)} / ${show(v.denominator)}${v.total != null ? ` (${show(v.graded ?? v.denominator)} graded / ${show(v.total)} expected)` : ""}`
        : fallback;
  }
  return typeof v === "number" ? formatNumber(v) : show(v);
}
function row(label, key, a, b, fallback = "Awaiting grades", target = "") {
  const scalar = (v) =>
    typeof v === "number" && Number.isFinite(v)
      ? v
      : typeof v?.value === "number" && Number.isFinite(v.value)
        ? v.value
        : null;
  const av = scalar(a),
    bv = scalar(b);
  const delta =
    av === null || bv === null
      ? "—"
      : key.endsWith("_usd")
        ? `${bv - av < 0 ? "−" : ""}$${Math.abs(bv - av).toFixed(6)}`
        : formatNumber(bv - av);
  const display = (v) =>
    key.endsWith("_usd") && typeof v === "number"
      ? `$${v.toFixed(6)}`
      : metricValue(v, fallback);
  return `<tr><td><button class="metric-button" data-filter="${esc(key)}">› &nbsp; ${esc(label)}</button></td>${target ? `<td>${esc(target)}</td>` : ""}<td>${state.mode === "versions" && !state.baseline ? "—" : display(a)}</td><td>${display(b)}</td><td>${esc(delta)}</td></tr>`;
}
function table(headers, rows) {
  return `<div class="table-wrap"><table class="eval-table"><thead><tr>${headers.map((h) => `<th>${esc(h)}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}
function section(title, content, note = "") {
  return `<section class="eval-section"><h2>${esc(title)}${note ? `<span>${esc(note)}</span>` : ""}</h2>${content}</section>`;
}
function safeUrl(url) {
  try {
    const u = new URL(url);
    return ["https:", "http:"].includes(u.protocol) &&
      !u.username &&
      !u.password
      ? u.href
      : "";
  } catch {
    return "";
  }
}
function cases() {
  return activeProfile().cases || state.report?.cases || [];
}
function compatibilityReasons(a, b) {
  if (!a || !b) return ["Report unavailable"];
  const reasons = [];
  for (const key of [
    "dataset_hash",
    "corpus_hash",
    "queryset_hash",
    "criteria_hash",
    "stage",
    "cutoff",
  ]) {
    if (a[key] == null || b[key] == null || a[key] === "" || b[key] === "")
      reasons.push(`${key}: not recorded`);
    else if (String(a[key]) !== String(b[key])) reasons.push(`${key}: differs`);
  }
  if (a.stage != null && a.stage !== state.stage)
    reasons.push("selected stage differs");
  if (a.cutoff != null && String(a.cutoff) !== state.cutoff)
    reasons.push("selected cutoff differs");
  const current = activeProfile();
  if (!current.model || !current.effort)
    reasons.push("current model or effort not recorded");
  const candidate = b.profiles?.find(
    (p) =>
      p.model &&
      p.effort &&
      p.model === current.model &&
      p.effort === current.effort,
  );
  if (!candidate) reasons.push("matching model and effort not recorded");
  else if (
    !candidate.arms?.[state.versionArm] ||
    !current.arms?.[state.versionArm]
  )
    reasons.push(`workflow ${state.versionArm} not recorded`);
  if (candidate) {
    for (const [label, obj] of [
      ["current profile", current],
      ["baseline profile", candidate],
      ["current workflow", current.arms?.[state.versionArm]],
      ["baseline workflow", candidate.arms?.[state.versionArm]],
    ]) {
      if (!obj) continue;
      for (const [key, expected] of [
        ["model", current.model],
        ["effort", current.effort],
        ["stage", state.stage],
        ["cutoff", state.cutoff],
        ...[
          "dataset_hash",
          "corpus_hash",
          "queryset_hash",
          "criteria_hash",
        ].map((key) => [key, a[key]]),
      ]) {
        if (obj[key] != null && String(obj[key]) !== String(expected))
          reasons.push(`${label} ${key}: differs`);
      }
    }
  }
  return reasons;
}
function compatible(a, b) {
  return compatibilityReasons(a, b).length === 0;
}
function clearFilter() {
  state.filter = "";
  state.filterIds = null;
  state.notice = "";
}
function caseId(c) {
  return c.question_id ?? c.case_id ?? c.id;
}
function isFailure(c) {
  return (
    c.failed ||
    c.error ||
    c.validation_failure ||
    c.failures?.length ||
    [
      "failed",
      "unattempted",
      "not_attempted",
      "answer_validation_failed",
      "model_contract_failed",
      "rejected",
    ].includes(c.status)
  );
}
function contributorIds(key) {
  const selected =
    state.mode === "versions"
      ? [arm(state.versionArm)]
      : state.report?.source === "Offline retrieval benchmark"
        ? [arm(state.methodA), arm(state.methodB)]
        : [arm("direct"), arm("mcp")];
  const ids = new Set();
  for (const a of selected) {
    const workflow = Object.entries(arms()).find(
      ([, value]) => value === a,
    )?.[0];
    const value =
      a.ranking?.[state.stage]?.[key] ??
      a.metrics?.[state.stage]?.[key] ??
      metric(a, key);
    for (const source of [value]) {
      if (!source || typeof source !== "object") continue;
      const entries =
        source.contributor_ids ??
        source.case_ids ??
        source.question_ids ??
        source.contributors;
      if (!Array.isArray(entries)) continue;
      for (const entry of entries) {
        const id =
          typeof entry === "object"
            ? (entry.question_id ?? entry.case_id ?? entry.id)
            : entry;
        if (id != null && workflow) ids.add(`${workflow}\u0000${String(id)}`);
      }
    }
  }
  const available = new Set(
    cases()
      .filter((c) => caseId(c) != null && c.workflow)
      .map((c) => `${c.workflow}\u0000${String(caseId(c))}`),
  );
  return [...ids].filter((id) => available.has(id));
}
function costGate() {
  if (state.mode !== "workflows")
    return "MCP ≤ 1.5× Direct: not assessed in version comparison.";
  const directArm = arm("direct"),
    mcpArm = arm("mcp");
  const directBasis = directArm.cost_basis ?? directArm.cost?.cost_basis,
    mcpBasis = mcpArm.cost_basis ?? mcpArm.cost?.cost_basis;
  const directFailures =
      directArm.includes_failed ?? directArm.cost?.includes_failed,
    mcpFailures = mcpArm.includes_failed ?? mcpArm.cost?.includes_failed;
  if (
    !directBasis ||
    directBasis !== mcpBasis ||
    typeof directFailures !== "boolean" ||
    directFailures !== mcpFailures
  )
    return "MCP ≤ 1.5× Direct: not assessed — matching cost and failed-attempt bases are unavailable.";
  const allCases = cases();
  const directTotal = arm("direct").total,
    mcpTotal = arm("mcp").total;
  if (
    !Number.isInteger(directTotal) ||
    directTotal <= 0 ||
    directTotal !== mcpTotal ||
    allCases.length !== directTotal * 2 ||
    allCases.some((c) => !caseId(c) || !["direct", "mcp"].includes(c.workflow))
  )
    return "MCP ≤ 1.5× Direct: not assessed — complete matching question coverage is unavailable.";
  const paired = new Map();
  for (const c of allCases) {
    if (!caseId(c) || !["direct", "mcp"].includes(c.workflow)) continue;
    const id = String(caseId(c));
    const entry = paired.get(id) || {};
    if (entry[c.workflow])
      return "MCP ≤ 1.5× Direct: not assessed — duplicate workflow attempts lack a defined cost basis.";
    entry[c.workflow] = c;
    paired.set(id, entry);
  }
  const entries = [...paired.values()];
  const cost = (c) => c?.cost?.api_equivalent_usd;
  // Metadata or pending review is not a recorded semantic outcome.
  const hasGrade = (c) =>
    !["pending", "ungraded"].includes(
      String(c?.grades?.status || "").toLowerCase(),
    ) &&
    typeof c?.grades?.quality === "string" &&
    ["good", "partial", "poor"].includes(c.grades.quality.trim().toLowerCase());
  if (
    !entries.length ||
    entries.some(
      (e) =>
        !e.direct ||
        !e.mcp ||
        !hasGrade(e.direct) ||
        !hasGrade(e.mcp) ||
        typeof cost(e.direct) !== "number" ||
        !Number.isFinite(cost(e.direct)) ||
        cost(e.direct) < 0 ||
        typeof cost(e.mcp) !== "number" ||
        !Number.isFinite(cost(e.mcp)) ||
        cost(e.mcp) < 0,
    )
  )
    return "MCP ≤ 1.5× Direct: not assessed — matched graded-case costs are incomplete or unavailable.";
  const direct = entries.reduce((sum, e) => sum + cost(e.direct), 0),
    mcp = entries.reduce((sum, e) => sum + cost(e.mcp), 0);
  if (direct <= 0)
    return "MCP ≤ 1.5× Direct: not assessed — matched Direct cost is zero.";
  return `MCP ≤ 1.5× Direct: ${mcp / direct <= 1.5 ? "within threshold" : "above threshold"} (${formatNumber(mcp / direct)}× across ${entries.length} matched graded questions).`;
}
function timestamp(e) {
  if (e.timestamp != null && e.timestamp !== "") return String(e.timestamp);
  let seconds = e.timestamp_seconds;
  if (seconds == null) {
    try {
      const url = new URL(e.url || e.canonical_url);
      const host = url.hostname.toLowerCase();
      if (
        [
          "youtube.com",
          "www.youtube.com",
          "m.youtube.com",
          "youtu.be",
        ].includes(host)
      ) {
        const raw = url.searchParams.get("t") ?? url.searchParams.get("start");
        if (raw && /^\d+s?$/.test(raw)) seconds = Number(raw.replace(/s$/, ""));
      }
    } catch {
      /* Missing or invalid source URL has no inferred timestamp. */
    }
  }
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0)
    return "";
  const n = Math.floor(seconds),
    h = Math.floor(n / 3600),
    m = Math.floor((n % 3600) / 60),
    sec = n % 60;
  return h
    ? `${h}:${String(m).padStart(2, "0")}:${String(sec).padStart(2, "0")}`
    : `${m}:${String(sec).padStart(2, "0")}`;
}
function evidenceCards(items, title) {
  if (!Array.isArray(items) || !items.length) return "";
  return `<h3>${title}</h3>${items
    .map((e) => {
      const url = safeUrl(e.url || e.canonical_url);
      const source =
        e.video_title || e.title || e.source_id || "Source title not recorded";
      const reference = e.passage_id || e.reference || e.document_id;
      const compactReference =
        reference && String(reference).length > 64
          ? `${String(reference).slice(0, 32)}…${String(reference).slice(-16)}`
          : reference;
      const details = [e.rank != null ? `Rank ${e.rank}` : "", timestamp(e)]
        .filter(Boolean)
        .map(esc)
        .join(" · ");
      const linkLabel = [source, reference, details]
        .filter(Boolean)
        .map(esc)
        .join(" · ");
      return `<div class="evidence-card"><strong>${esc(source)}</strong>${reference ? `<div class="evidence-reference" title="${esc(reference)}">Reference: ${esc(compactReference)}</div>${String(reference).length > 64 ? `<details><summary>Full reference</summary><code>${esc(reference)}</code></details>` : ""}` : ""}${details ? `<div>${details}</div>` : ""}${e.original_text != null || e.text != null || e.quote != null ? `<blockquote>${esc(e.original_text ?? e.text ?? e.quote)}</blockquote>` : ""}${url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">Open source<span class="sr-only">: ${linkLabel}</span></a>` : ""}</div>`;
    })
    .join("")}`;
}
function attempt(c) {
  const visible = [
    ["Answer", c.answer],
    ["Validation failure", c.validation_failure],
    ["Error", c.error === c.validation_failure ? null : c.error],
    ["Failures", c.failures],
    ["Grades", c.grades],
  ]
    .map(([label, value]) =>
      value != null ? `<h3>${label}</h3><pre>${show(value)}</pre>` : "",
    )
    .join("");
  const rejected =
    c.attempted_output != null
      ? `<p><strong>Attempted output: rejected</strong></p><details><summary>Show rejected attempted output</summary><pre>${show(c.attempted_output)}</pre></details>`
      : "";
  const hits = (c.trace?.retrieval_payloads || []).flatMap((payload) =>
    Array.isArray(payload.hits) ? payload.hits : [],
  );
  const overview = hits.map((hit) =>
    [
      hit.rank != null ? `Rank ${hit.rank}` : "Rank not recorded",
      hit.title || hit.document_id || "Source not recorded",
    ].join(" · "),
  );
  const count = c.trace?.tool_calls;
  const trace =
    c.trace != null
      ? `<p class="detail-note">${count != null ? `${show(count)} tool calls` : "Tool-call count not recorded"}${overview.length ? ` · ${overview.map(esc).join("; ")}` : ""}</p><details><summary>Show detailed trace</summary><pre>${show(c.trace)}</pre></details>`
      : "";
  return `<div class="attempt"><h3>${esc(c.workflow || "Workflow not recorded")} · ${esc(c.status || "Status not recorded")}</h3>${visible}${rejected}${trace}${evidenceCards(c.citations, "Cited evidence")}${evidenceCards(c.evidence, "Retrieved evidence")}</div>`;
}
function renderList() {
  $("runList").innerHTML = state.reports
    .filter((r) =>
      `${r.run_id} ${r.title || ""}`
        .toLowerCase()
        .includes(state.search.toLowerCase()),
    )
    .map(
      (r) =>
        `<button aria-pressed="${r.id === state.report?.id}" class="run-item ${r.id === state.report?.id ? "active" : ""}" data-run="${esc(r.id)}"><strong>${esc(r.title || r.run_id)}</strong><small>${esc(r.status || "Saved report")}</small></button>`,
    )
    .join("");
}
function render() {
  renderList();
  const r = state.report;
  $("runTitle").textContent = r ? r.title || r.run_id : "Evaluation";
  $("runSubtitle").textContent = r
    ? [
        r.source || r.source_type || "Source unavailable",
        activeProfile().model,
        activeProfile().effort,
        r.description,
      ]
        .filter(Boolean)
        .join(" · ")
    : "Saved reports and review history";
  if (!r) {
    $("dashboard").innerHTML =
      '<div class="empty-state"><h2>No saved reports yet</h2><p>Import a local evaluation report to review its metrics, coverage, and evidence. Reports are saved as immutable snapshots.</p><p><a href="./evaluation-workbench.html">Open query sets and dataset management →</a></p></div>';
    return;
  }
  const p = activeProfile();
  if (r.source === "Offline retrieval benchmark") state.mode = "workflows";
  const benchmark = r.source === "Offline retrieval benchmark";
  const methods = Object.keys(p.arms || {});
  if (benchmark && !methods.includes(state.methodA))
    state.methodA = methods.includes("hybrid_baseline")
      ? "hybrid_baseline"
      : methods[0];
  if (benchmark && !methods.includes(state.methodB))
    state.methodB = methods.includes("optimized_hybrid")
      ? "optimized_hybrid"
      : methods[1] || methods[0];
  const d = arm(benchmark ? state.methodA : "direct");
  const m = arm(benchmark ? state.methodB : "mcp");
  const profileTabs = `<div class="tabs" aria-label="Report profiles">${r.profiles.map((v, i) => `<button data-profile="${i}" aria-pressed="${i === state.profile}" class="${i === state.profile ? "active" : ""}">${esc(v.id || v.model || `Profile ${i + 1}`)}${v.status ? ` · ${esc(v.status)}` : ""}</button>`).join("")}</div>`;
  const profiles = r.profiles || [];
  const incomplete = profiles.some(
    (profile) =>
      ["stopped", "partial", "failed"].includes(profile.status) ||
      Object.values(profile.arms || {}).some(
        (a) => (a.failed ?? 0) > 0 || (a.unattempted ?? 0) > 0,
      ),
  );
  const overall = incomplete
    ? "Partial run"
    : r.status
      ? `Run ${r.status}`
      : profiles.every((profile) => profile.status === "completed")
        ? "Run completed"
        : "Run status unavailable";
  const profileStatus =
    p.status === "completed"
      ? "Generation finished"
      : p.status
        ? `Generation ${p.status}`
        : "Generation status unavailable";
  const hasGrades = Object.values(p.arms || {}).some(
    (a) => a.grades != null || a.graded_status != null,
  );
  const visibleArms =
    state.mode === "versions" ? [arm(state.versionArm)] : [d, m];
  const validationCounts = visibleArms.map((a) => a.validation_rejections);
  const validationCount = validationCounts.every((n) => typeof n === "number")
    ? validationCounts.reduce((a, b) => a + b, 0)
    : null;
  const failureCounts = visibleArms.map((a) => a.failed);
  const failureCount = failureCounts.every((n) => typeof n === "number")
    ? failureCounts.reduce((a, b) => a + b, 0)
    : null;
  const selectedFailures = cases().filter(
    (c) => c.workflow === state.versionArm && isFailure(c),
  ).length;
  const failureLabel =
    state.mode === "versions"
      ? `Inspect ${selectedFailures} recorded failure or unattempted attempts · ${state.versionArm.toUpperCase()} workflow`
      : failureCount == null
        ? "Inspect failures"
        : `Inspect ${failureCount} ${failureCount === 1 ? "failure" : "failures"}${validationCount == null ? "" : ` (${validationCount} validation)`}${state.mode === "versions" ? ` · ${state.versionArm.toUpperCase()} workflow` : ""}`;
  const status = benchmark
    ? `<div class="status-bar">Mechanical retrieval benchmark · ${esc(r.description || "Query count not recorded")} · Semantic grades not applicable</div>`
    : `<div class="status-bar">${esc(overall)} · ${esc(profileStatus)} · ${hasGrades ? "Semantic grades recorded" : "Semantic grades unavailable"} · Direct: ${show(d.accepted, "Unknown")}/${show(d.total, "?")} contract-accepted · MCP: ${show(m.accepted, "Unknown")}/${show(m.total, "?")} contract-accepted · <button class="inline-button" data-tab="failures">${failureLabel}</button></div>`;
  const candidates = state.reports.filter(
    (x) => x.id !== r.id && compatible(r, x),
  );
  const toolbar = `<div class="toolbar"><button data-mode="workflows" aria-pressed="${state.mode === "workflows"}" class="${state.mode === "workflows" ? "active" : ""}">Compare workflows</button>${benchmark ? "" : `<button data-mode="versions" aria-pressed="${state.mode === "versions"}" class="${state.mode === "versions" ? "active" : ""}">Compare versions</button>`}${!benchmark && state.mode === "versions" ? `<label>Workflow <select id="versionArm"><option value="direct" ${state.versionArm === "direct" ? "selected" : ""}>Direct</option><option value="mcp" ${state.versionArm === "mcp" ? "selected" : ""}>MCP</option></select></label><label>Compatible baseline <select id="baseline"><option value="">None selected</option>${candidates.map((x) => `<option value="${esc(x.id)}" ${x.id === state.baseline ? "selected" : ""}>${esc(x.title || x.run_id)}</option>`).join("")}</select></label><span class="muted">${candidates.length ? "" : "No compatible baseline; unknown identifiers cannot certify a match."}</span>` : ""}<button class="inline-button history-link" data-tab="provenance">Changes & review history</button></div><div class="tabs">${[
    ["overview", "Overview"],
    ["questions", "Questions"],
    ["failures", "Failures & traces"],
    ["provenance", "Provenance"],
  ]
    .map(
      ([id, label]) =>
        `<button data-tab="${id}" aria-pressed="${state.tab === id}" class="${state.tab === id ? "active" : ""}">${label}</button>`,
    )
    .join("")}</div>`;
  const rejectedCandidates = state.reports.filter(
    (x) => x.id !== r.id && !compatible(r, x),
  );
  let body = state.notice
    ? `<p class="detail-note" role="status">${esc(state.notice)}</p>`
    : "";
  if (state.tab === "overview") {
    const methodControls = benchmark
      ? `<div class="toolbar"><label>Method A <select id="methodA">${methods.map((name) => `<option value="${esc(name)}" ${name === state.methodA ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label><label>Method B <select id="methodB">${methods.map((name) => `<option value="${esc(name)}" ${name === state.methodB ? "selected" : ""}>${esc(name)}</option>`).join("")}</select></label></div>`
      : "";
    body += methodControls;
    if (state.mode === "versions" && rejectedCandidates.length)
      body += `<details><summary>Why other runs are unavailable</summary>${rejectedCandidates.map((x) => `<p>${esc(x.title || x.run_id)}: ${esc(compatibilityReasons(r, x).join("; "))}</p>`).join("")}</details>`;
    const da =
      state.mode === "versions"
        ? state.reports
            .find((x) => x.id === state.baseline)
            ?.profiles?.find(
              (x) => x.model === p.model && x.effort === p.effort,
            )?.arms?.[state.versionArm] || {}
        : d;
    const ma = state.mode === "versions" ? arm(state.versionArm) : m;
    const heads =
      state.mode === "versions"
        ? [
            "Metric",
            `Baseline ${state.versionArm.toUpperCase()}`,
            `Current ${state.versionArm.toUpperCase()}`,
            "Δ (current − baseline)",
          ]
        : benchmark
          ? ["Metric", state.methodA, state.methodB, "Δ (B − A)"]
          : [
              "Metric",
              p.direct_label || "Direct",
              p.mcp_label || "MCP",
              "Δ (MCP − Direct)",
            ];
    const target = (key) => {
      const gate = r.gates?.[key];
      if (!gate || gate.denominator == null || gate.minimum_passes == null)
        return "Target not configured";
      return `${gate.target != null ? `≥${Math.round(gate.target * 100)}% · ` : ""}${gate.minimum_passes} of ${gate.denominator} required`;
    };
    if (!benchmark)
      body += section(
        "Acceptance targets",
        table(
          ["Metric", "Target", heads[1], heads[2], heads[3]],
          row(
            labels.video_selection,
            "video_selection",
            metric(da, "video_selection"),
            metric(ma, "video_selection"),
            "Awaiting grades",
            target("source_selection"),
          ) +
            row(
              labels.assumption_first_handling,
              "assumption_first_handling",
              metric(da, "assumption_first_handling"),
              metric(ma, "assumption_first_handling"),
              "Awaiting grades",
              target("underspecification"),
            ),
        ),
        "Required counts are targets, not observed results.",
      );
    const ranking = (a, k) =>
      k === "auc" && !a.ranking?.[state.stage]?.auc_contract
        ? null
        : (a.ranking?.[state.stage]?.[k] ?? a.metrics?.[state.stage]?.[k]);
    const rankingKeys = [
      ["nDCG@10", "ndcg_at_10"],
      [`Precision@${state.cutoff}`, `precision_at_${state.cutoff}`],
      [`Recall@${state.cutoff}`, `recall_at_${state.cutoff}`],
      ["MRR@10", "mrr_at_10"],
      ["ROC-AUC", "auc"],
    ];
    const controls = `<div class="toolbar"><span>Stage:</span><button data-stage="initial_retrieval" aria-pressed="${state.stage === "initial_retrieval"}" class="${state.stage === "initial_retrieval" ? "active" : ""}">Initial retrieval</button><button data-stage="final_evidence" aria-pressed="${state.stage === "final_evidence"}" class="${state.stage === "final_evidence" ? "active" : ""}">Final evidence selection</button><label>Cutoff: <select id="cutoff">${["1", "5", "10"].map((k) => `<option ${k === state.cutoff ? "selected" : ""}>${k}</option>`).join("")}</select></label></div>`;
    body +=
      `<section class="eval-section"><div class="ranking-heading"><h2>Retrieval ranking</h2>${controls}</div>` +
      table(
        heads,
        rankingKeys
          .map(([l, k]) =>
            row(
              l,
              k,
              ranking(da, k),
              ranking(ma, k),
              k === "auc" ? "Not configured" : "Not calculated",
            ),
          )
          .join(""),
      ) +
      '<p class="detail-note">AUC requires an explicit labeled candidate-pool contract. Initial retrieval and final evidence are separate stages.</p></section>';
    if (!benchmark)
      body += section(
        "Answer & evidence quality",
        table(
          heads,
          Object.entries(labels)
            .slice(2)
            .map(
              ([k, l]) =>
                (k === "unnecessary_clarification"
                  ? `<tr class="group-divider"><th colspan="4">Interaction measures</th></tr>`
                  : "") + row(l, k, metric(da, k), metric(ma, k)),
            )
            .join(""),
        ) +
          `<p class="detail-note">Authentic citations can still be irrelevant. Pending grades are not failed processing. ${esc(r.grading_note || "")}</p>`,
      );
    const renderUsage = (rows) =>
      rows
        .map(([l, k]) =>
          row(
            l,
            k,
            da[k] ?? da.usage?.[k] ?? da.cost?.[k],
            ma[k] ?? ma.usage?.[k] ?? ma.cost?.[k],
            "Unknown",
          ),
        )
        .join("");
    const primaryUsage = renderUsage([
      ["Input tokens", "input_tokens"],
      ["Output tokens", "output_tokens"],
      ["Reasoning tokens (included in output)", "reasoning_output_tokens"],
      ["API-equivalent estimate", "api_equivalent_usd"],
    ]);
    const secondaryUsage = renderUsage([
      ["Failed attempts", "failed"],
      ["Validation rejections", "validation_rejections"],
      ["Unattempted", "unattempted"],
      ["Cached input tokens", "cached_input_tokens"],
      ["Latency (ms)", "latency_ms"],
      ["Tool calls", "tool_calls"],
      ["Raw total cost", "raw_total_usd"],
      ["Billed cost", "billed_usd"],
    ]);
    const coverage = (a) =>
      `${show(a.accepted, "Unknown")}/${show(a.total, "?")} accepted; ${show(a.failed, "unknown")} failed`;
    const costBasis = (a) =>
      [
        a.cost_basis || a.cost?.cost_basis,
        (a.includes_failed ?? a.cost?.includes_failed) === true
          ? "failed attempts included"
          : (a.includes_failed ?? a.cost?.includes_failed) === false
            ? "failed attempts excluded"
            : "failed-attempt cost basis unknown",
      ]
        .filter(Boolean)
        .join("; ");
    if (!benchmark)
      body += section(
        "Execution & cost",
        `<p class="detail-note">${esc(heads[1])}: ${coverage(da)} · ${esc(heads[2])}: ${coverage(ma)}. ${esc(r.cost_note || `${heads[1]}: ${costBasis(da)}; ${heads[2]}: ${costBasis(ma)}.`)}</p>` +
          table(heads, primaryUsage) +
          `<details class="usage-details"><summary>Usage details <span class="muted">(failures, cached tokens, latency and tool calls)</span></summary>${table(heads, secondaryUsage)}</details>` +
          `<p class="detail-note">${esc(costGate())} CLI API-equivalent estimates are not subscription charges.</p>`,
      );
    if (benchmark)
      body += `<p class="detail-note">Acceptance targets, semantic answer and citation grades, and the MCP cost gate are not applicable to this mechanical retrieval benchmark.</p>`;
  } else if (state.tab === "provenance") {
    body = section(
      "Run provenance",
      `<div class="case-card">${["run_id", "source", "status", "parent_run_id", "continuation_of", "corpus_hash", "queryset_hash", "criteria_hash", "created_at", "completed_at", "reviewed_at", "review_duration", "changes", "grading_note", "workflow_note", "cost_note"].map((k) => `<p><strong>${esc(k.replaceAll("_", " "))}:</strong> ${show(r[k], "Not recorded")}</p>`).join("")}</div>`,
    );
  } else {
    let list = cases();
    if (state.mode === "versions")
      list = list.filter((c) => c.workflow === state.versionArm);
    if (state.filterIds)
      list = list.filter((c) =>
        state.filterIds.includes(`${c.workflow}\u0000${String(caseId(c))}`),
      );
    if (state.tab === "failures") list = list.filter(isFailure);
    const grouped = new Map();
    for (const c of list) {
      const key = caseId(c) ?? c.question ?? c.query ?? Symbol();
      if (!grouped.has(key)) grouped.set(key, []);
      grouped.get(key).push(c);
    }
    body += section(
      state.tab === "failures" ? "Failures & traces" : "Questions",
      `<p class="muted">${state.filter ? `Metric contributors: ${esc(labels[state.filter] || state.filter)}. ` : ""}${grouped.size} recorded questions · ${list.length} attempts${state.mode === "versions" ? ` · ${esc(state.versionArm)} workflow` : ""}. Select a question for answer and evidence.</p><div class="case-list">${[...grouped.values()].map((group, i) => `<article class="case-card"><details><summary>${esc(group[0].question || group[0].query || caseId(group[0]) || `Question ${i + 1}`)} · ${group.length} ${group.length === 1 ? "attempt" : "attempts"}</summary>${group.map(attempt).join("")}</details></article>`).join("") || '<p class="muted">No recorded questions in this view.</p>'}</div>`,
    );
  }
  $("dashboard").innerHTML = profileTabs + status + toolbar + body;
}
async function refresh(selectId) {
  const res = await fetch("/v1/evaluation/reports");
  if (!res.ok) throw new Error(`Could not load reports (${res.status})`);
  const data = await res.json();
  state.reports = data.reports || [];
  state.report =
    state.reports.find((r) => r.id === selectId) || state.reports[0] || null;
  render();
}
$("runSearch").addEventListener("input", (e) => {
  state.search = e.target.value;
  renderList();
});
$("runList").addEventListener("click", (e) => {
  const id = e.target.closest("[data-run]")?.dataset.run;
  if (id) {
    state.report = state.reports.find((r) => r.id === id);
    state.profile = 0;
    state.methodA = "";
    state.methodB = "";
    state.baseline = "";
    state.tab = "overview";
    clearFilter();
    render();
    $("runList")
      .querySelector(`[data-run="${CSS.escape(id)}"]`)
      ?.focus();
  }
});
$("dashboard").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  let focusSelector =
    b.dataset.profile != null
      ? `[data-profile="${b.dataset.profile}"]`
      : b.dataset.tab
        ? `${b.closest(".tabs") ? ".tabs " : b.closest(".status-bar") ? ".status-bar " : ".toolbar "}[data-tab="${b.dataset.tab}"]`
        : b.dataset.mode
          ? `[data-mode="${b.dataset.mode}"]`
          : b.dataset.stage
            ? `[data-stage="${b.dataset.stage}"]`
            : b.dataset.filter
              ? `[data-filter="${b.dataset.filter}"]`
              : null;
  if (b.dataset.profile != null) {
    clearFilter();
    state.profile = Number(b.dataset.profile);
    state.baseline = "";
  }
  if (b.dataset.tab) {
    state.tab = b.dataset.tab;
    clearFilter();
  }
  if (b.dataset.mode) {
    state.mode = b.dataset.mode;
    state.baseline = "";
    clearFilter();
  }
  if (b.dataset.stage) {
    clearFilter();
    state.stage = b.dataset.stage;
    state.baseline = "";
  }
  if (b.dataset.filter) {
    const ids = contributorIds(b.dataset.filter);
    if (ids.length) {
      state.filter = b.dataset.filter;
      state.filterIds = ids;
      state.notice = "";
      state.tab = "questions";
      focusSelector = '[data-tab="questions"]';
    } else {
      clearFilter();
      state.notice = `Question-level contributors for ${labels[b.dataset.filter] || b.dataset.filter} are unavailable in this report; no drilldown is available.`;
    }
  }
  render();
  if (focusSelector) $("dashboard").querySelector(focusSelector)?.focus();
});
$("dashboard").addEventListener("change", (e) => {
  const controlId = e.target.id;
  clearFilter();
  if (e.target.id === "cutoff") {
    state.cutoff = e.target.value;
    state.baseline = "";
  }
  if (e.target.id === "baseline") state.baseline = e.target.value;
  if (e.target.id === "versionArm") {
    state.versionArm = e.target.value;
    state.baseline = "";
  }
  if (e.target.id === "methodA") state.methodA = e.target.value;
  if (e.target.id === "methodB") state.methodB = e.target.value;
  render();
  if (controlId) $(controlId)?.focus();
});
$("importReport").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    if (file.size > 10000000) throw new Error("Report exceeds 10 MB");
    const payload = JSON.parse(await file.text());
    const res = await fetch("/v1/evaluation/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok)
      throw new Error(
        data.error?.message || data.error || "Report import failed",
      );
    await refresh(data.report.id);
    $("notice").textContent = "Report imported as an immutable snapshot.";
    $("notice").className = "";
  } catch (err) {
    $("notice").textContent = `Import failed: ${err.message}`;
    $("notice").className = "error";
  } finally {
    e.target.value = "";
  }
});
refresh().catch((err) => {
  $("notice").textContent = err.message;
  $("notice").className = "error";
  render();
});
