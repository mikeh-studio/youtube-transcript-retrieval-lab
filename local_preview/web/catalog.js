(() => {
  "use strict";
  const $ = id => document.getElementById(id);
  const form = $("filters"), list = $("videos"), dialog = $("detail");
  let offset = 0, generation = 0, timer, controller, detailGeneration = 0;
  const limit = 12;
  const node = (tag, text, cls) => { const e = document.createElement(tag); if (text !== undefined) e.textContent = text; if (cls) e.className = cls; return e; };
  const value = (item, key) => item.metadata[key]?.value ?? null;
  const friendly = text => text.replaceAll("_", " ");
  const duration = ms => ms === null ? "Duration unknown" : `${Math.floor(ms / 60000)}:${String(Math.floor(ms / 1000) % 60).padStart(2, "0")}`;
  const fmt = s => s ? new Date(s).toLocaleString() : "Unknown";
  function clearDisplay() { list.replaceChildren(); $("detail-content").replaceChildren(); if (dialog.open) dialog.close(); $("total").textContent = "—"; $("freshness").textContent = ""; }
  function expiryDeadline(data) {
    const items = data.items || [data.item];
    const dates = items.flatMap(i => [...Object.values(i.metadata).map(f => f.expires_at), i.readiness.expires_at].filter(Boolean).map(Date.parse));
    return dates.length ? data.requestStarted + Math.min(...dates) - Date.parse(data.generated_at) : Infinity;
  }
  function armExpiry(deadline) {
    clearTimeout(timer);
    if (!Number.isFinite(deadline)) return;
    timer = setTimeout(() => { clearDisplay(); load(); }, Math.min(Math.max(0, deadline - performance.now()), 2147483647));
  }
  async function get(path, signal) {
    const requestStarted = performance.now();
    const response = await fetch(path, {cache: "no-store", signal});
    if (!response.ok) throw new Error(response.status === 422 ? "These filters are not available. Check that library/v1 has been imported." : "Catalog unavailable. Check the private service connection, then try again.");
    const data = await response.json();
    data.requestStarted = requestStarted;
    if (expiryDeadline(data) <= performance.now()) throw new Error("Catalog snapshot expired in transit. Try again.");
    return data;
  }
  function showDetail(item) {
    const content = $("detail-content"); content.replaceChildren(node("p", "VIDEO DETAILS", "eyebrow"), node("h2", value(item, "title") || "Title unknown"));
    const link = node("a", "Open on YouTube ↗"); link.href = item.url; link.target = "_blank"; link.rel = "noopener noreferrer"; content.append(link);
    content.append(node("h3", "Review readiness"), node("p", friendly(item.readiness.state)));
    for (const blocker of item.readiness.blockers) content.append(node("p", friendly(blocker)));
    content.append(node("p", "Searchability: not checked. Meeting review preconditions does not start ingestion."));
    content.append(node("h3", "Metadata and provenance")); const dl = node("dl");
    const names = ["title", "duration_ms", "published_at", "channel_name", "primary_audio_language", "metadata_language", "platform_category_id", "broadcast_status", "description"];
    for (const name of names) {
      dl.append(node("dt", friendly(name)));
      const field = item.metadata[name]; const dd = node("dd");
      if (field) { dd.append(node("div", name === "duration_ms" ? duration(field.value) : String(field.value)), node("div", `${friendly(field.provider)} · Observed ${fmt(field.observed_at)}${field.expires_at ? ` · Expires ${fmt(field.expires_at)}` : ""}`)); }
      else dd.textContent = friendly(item.missing_fields[name] || "unknown");
      dl.append(dd);
    }
    content.append(dl, node("h3", "Approved classifications"));
    for (const label of item.labels) content.append(node("p", `${label.en} / ${label.ja} · ${label.namespace} · ${label.vocabulary}/${label.version}`));
    if (!item.labels.length) content.append(node("p", "No reviewed classifications yet."));
    content.append(node("p", `Retained sightings: ${fmt(item.first_seen)} — ${fmt(item.last_seen)}. These dates cover retained discovery records only.`));
    if (!dialog.open) dialog.showModal();
  }
  async function details(id, epoch) {
    const detailEpoch = ++detailGeneration;
    try { const result = await get(`/v1/catalog/items/${id}`, controller.signal); if (generation !== epoch || detailEpoch !== detailGeneration) return; showDetail(result.item); armExpiry(Math.min(listDeadline, expiryDeadline(result))); }
    catch (error) { if (error.name !== "AbortError" && generation === epoch) $("status").textContent = error.message; }
  }
  let listDeadline = Infinity;
  async function load() {
    const epoch = ++generation; controller?.abort(); controller = new AbortController(); clearTimeout(timer); clearDisplay();
    $("status").textContent = "Loading catalog…"; $("previous").disabled = true; $("next").disabled = true; $("page").textContent = "";
    const params = new URLSearchParams({limit: String(limit), offset: String(offset)});
    for (const [key, val] of new FormData(form)) if (val) params.set(key, val);
    if (params.has("term")) { params.set("vocabulary", "library"); params.set("version", "v1"); params.set("descendants", "true"); }
    try {
      const data = await get(`/v1/catalog/items?${params}`, controller.signal); if (epoch !== generation) return;
      listDeadline = expiryDeadline(data); $("total").textContent = data.total; $("freshness").textContent = `Snapshot ${fmt(data.generated_at)}`;
      $("status").textContent = data.items.length ? "Metadata may be incomplete. Open a video to inspect its sources and review blockers." : "No videos match these filters.";
      for (const item of data.items) {
        const card = node("article", undefined, "card"); card.append(node("h3", value(item, "title") || `Title unknown · ${item.external_id}`));
        card.append(node("p", `${value(item, "channel_name") || "Channel unknown"} · ${duration(value(item, "duration_ms"))}`), node("p", `Audio: ${value(item, "primary_audio_language") || "Unknown"} · Published: ${fmt(value(item, "published_at"))}`));
        const tags = node("div", undefined, "pills"); tags.append(node("span", friendly(item.qualification), "pill hold"));
        for (const label of item.labels) tags.append(node("span", label.en, "pill")); card.append(tags);
        const button = node("button", "View details", "secondary"); button.addEventListener("click", () => details(item.id, epoch)); card.append(button); list.append(card);
      }
      $("page").textContent = data.items.length ? `${offset + 1}–${offset + data.items.length} of ${data.total}` : "0 videos";
      $("previous").disabled = offset === 0; $("next").disabled = offset + limit >= data.total || offset + limit > 10000;
      armExpiry(listDeadline);
    } catch (error) { if (error.name !== "AbortError" && epoch === generation) $("status").textContent = error.message; }
  }
  form.addEventListener("submit", event => {event.preventDefault(); offset = 0; load();});
  $("reset-filters").addEventListener("click", () => {form.reset(); offset = 0; load();});
  $("previous").addEventListener("click", () => {offset = Math.max(0, offset - limit); load();});
  $("next").addEventListener("click", () => {offset += limit; load();});
  $("close").addEventListener("click", () => dialog.close());
  load();
})();
