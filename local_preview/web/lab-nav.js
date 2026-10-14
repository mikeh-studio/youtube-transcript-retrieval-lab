// Single source of truth for the Retrieval Lab navigation. The React shell
// imports these definitions; the standalone pages call renderLabNav().

export const LAB_BRAND = {
  "en-US": { title: "Retrieval Lab", subtitle: "YouTube transcripts" },
  "ja-JP": { title: "Retrieval Lab", subtitle: "YouTube 文字起こし" },
};

// Ordered by the lab workflow: discover -> index -> retrieve -> measure.
export const LAB_NAV_PRIMARY = [
  {
    key: "catalog",
    href: "/catalog.html",
    icon: "/icons/icon-library.svg",
    labels: { "en-US": "Catalog", "ja-JP": "カタログ" },
  },
  {
    key: "library",
    href: "/index.html#/ingest",
    route: "/ingest",
    icon: "/icons/icon-upload.svg",
    labels: { "en-US": "Library", "ja-JP": "ライブラリ" },
  },
  {
    key: "search",
    href: "/index.html#/qa",
    route: "/qa",
    icon: "/icons/icon-search.svg",
    labels: { "en-US": "Search", "ja-JP": "検索" },
    requiresUnlock: true,
  },
  {
    key: "evaluate",
    href: "/evaluation.html",
    icon: "/icons/icon-jobs.svg",
    labels: { "en-US": "Evaluate", "ja-JP": "評価" },
    requiresUnlock: true,
  },
];

export const LAB_NAV_TOOLS_MENU = {
  key: "tools",
  icon: "/icons/icon-jobs.svg",
  labels: { "en-US": "Tools", "ja-JP": "ツール" },
};

// Lab tools first; the study tool is secondary and listed last.
export const LAB_NAV_TOOLS = [
  {
    key: "chunking",
    href: "/chunking.html",
    icon: "/icons/icon-jobs.svg",
    labels: { "en-US": "Chunking", "ja-JP": "チャンキング" },
    requiresUnlock: true,
  },
  {
    key: "data-quality",
    href: "/evidence.html",
    icon: "/icons/icon-library.svg",
    labels: { "en-US": "Data quality", "ja-JP": "データ品質" },
    requiresUnlock: true,
  },
  {
    key: "feedback",
    href: "/reviews.html",
    icon: "/icons/icon-library.svg",
    labels: { "en-US": "Search feedback", "ja-JP": "検索フィードバック" },
    requiresUnlock: true,
  },
  {
    key: "local-video",
    href: "/index.html#/local-video",
    route: "/local-video",
    icon: "/icons/icon-search.svg",
    labels: { "en-US": "Local video", "ja-JP": "ローカル動画" },
  },
  {
    key: "study",
    href: "/index.html#/study",
    route: "/study",
    activeRoutes: ["/study", "/tldr"],
    icon: "/icons/icon-chat.svg",
    labels: { "en-US": "Study", "ja-JP": "学習" },
    requiresUnlock: true,
  },
];

export function labNavLabel(item, locale) {
  return item.labels[locale] || item.labels["en-US"];
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll("\"", "&quot;");
}

function linkHtml(item, locale, active, linkClass, iconClass, labelClass) {
  const current = active ? ' aria-current="page"' : "";
  return (
    `<a class="${linkClass}${active ? " active" : ""}" href="${item.href}" data-nav-key="${item.key}"${current}>`
    + `<img class="${iconClass}" src="${item.icon}" alt="" aria-hidden="true" />`
    + `<span class="${labelClass}">${escapeHtml(labNavLabel(item, locale))}</span></a>`
  );
}

function menuHtml(locale, active, mobile) {
  const menuActive = LAB_NAV_TOOLS.some((item) => item.key === active);
  const menu = LAB_NAV_TOOLS_MENU;
  const label = escapeHtml(labNavLabel(menu, locale));
  const links = LAB_NAV_TOOLS.map((item) => (
    mobile
      ? linkHtml(item, locale, item.key === active, "bottom-tools-link", "bottom-tab-icon", "nav-label-text")
      : linkHtml(item, locale, item.key === active, "nav-tools-link", "nav-icon", "nav-label-text")
  )).join("");
  if (mobile) {
    return (
      `<details class="bottom-tools-menu" data-nav-group="${menu.key}">`
      + `<summary class="bottom-tab-link${menuActive ? " active" : ""}">`
      + `<img class="bottom-tab-icon" src="${menu.icon}" alt="" aria-hidden="true" />`
      + `<span class="bottom-tab-label">${label}</span></summary>`
      + `<div class="bottom-tools-panel">${links}</div></details>`
    );
  }
  return (
    `<details class="nav-tools-menu" data-nav-group="${menu.key}">`
    + `<summary class="app-nav-link nav-btn nav-tools-summary${menuActive ? " active" : ""}">`
    + `<img class="nav-icon" src="${menu.icon}" alt="" aria-hidden="true" />`
    + `<span class="nav-label">${label}</span></summary>`
    + `<div class="nav-tools-panel">${links}</div></details>`
  );
}

// Renders the shared header and mobile navigation on a standalone page.
// `active` is the key of the current page's nav item.
export function renderLabNav({ active, locale }) {
  const brand = LAB_BRAND[locale] || LAB_BRAND["en-US"];
  const eyebrow = document.getElementById("eyebrowText");
  if (eyebrow) {
    eyebrow.textContent = brand.title;
  }
  const brandSub = document.querySelector(".appbar .brand-sub");
  if (brandSub) {
    brandSub.textContent = brand.subtitle;
  }

  const desktop = document.querySelector(".appbar-nav");
  if (desktop) {
    desktop.innerHTML = LAB_NAV_PRIMARY
      .map((item) => linkHtml(item, locale, item.key === active, "app-nav-link", "nav-icon", "nav-label nav-label-text"))
      .join("") + menuHtml(locale, active, false);
  }
  const mobile = document.querySelector(".bottom-tabbar");
  if (mobile) {
    mobile.innerHTML = LAB_NAV_PRIMARY
      .map((item) => linkHtml(item, locale, item.key === active, "bottom-tab-link", "bottom-tab-icon", "bottom-tab-label"))
      .join("") + menuHtml(locale, active, true);
  }
}
