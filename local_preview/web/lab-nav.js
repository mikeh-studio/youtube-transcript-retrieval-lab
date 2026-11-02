// Single source of truth for the Retrieval Lab navigation. The React shell
// imports these definitions; the standalone pages call renderLabNav().

export const LAB_BRAND = {
  "en-US": { title: "Retrieval Lab", subtitle: "YouTube transcripts" },
  "ja-JP": { title: "Retrieval Lab", subtitle: "YouTube 文字起こし" },
};

// Ordered by the lab workflow: discover -> index -> retrieve -> measure.
export const LAB_NAV_PRIMARY = [
  {
    key: "library",
    href: "/index.html#/ingest",
    route: "/ingest",
    icon: "/icons/icon-library.svg",
    labels: { "en-US": "Library", "ja-JP": "ライブラリ" },
  },
  {
    key: "search",
    href: "/index.html#/qa",
    route: "/qa",
    icon: "/icons/icon-search.svg",
    labels: { "en-US": "Search", "ja-JP": "検索" },
  },
  {
    key: "evaluate",
    href: "/evaluation.html",
    icon: "/icons/icon-jobs.svg",
    labels: { "en-US": "Evaluation", "ja-JP": "評価" },
  },
  {
    key: "settings",
    href: "/index.html#/settings",
    route: "/settings",
    icon: "/icons/icon-jobs.svg",
    labels: { "en-US": "Settings", "ja-JP": "設定" },
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
    .replaceAll('"', "&quot;");
}

function linkHtml(item, locale, active, linkClass, iconClass, labelClass) {
  const current = active ? ' aria-current="page"' : "";
  return (
    `<a class="${linkClass}${active ? " active" : ""}" href="${item.href}" data-nav-key="${item.key}"${current}>` +
    `<img class="${iconClass}" src="${item.icon}" alt="" aria-hidden="true" />` +
    `<span class="${labelClass}">${escapeHtml(labNavLabel(item, locale))}</span></a>`
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
    desktop.innerHTML = LAB_NAV_PRIMARY.map((item) =>
      linkHtml(
        item,
        locale,
        item.key === active,
        "app-nav-link",
        "nav-icon",
        "nav-label nav-label-text",
      ),
    ).join("");
  }
  const mobile = document.querySelector(".bottom-tabbar");
  if (mobile) {
    mobile.innerHTML = LAB_NAV_PRIMARY.map((item) =>
      linkHtml(
        item,
        locale,
        item.key === active,
        "bottom-tab-link",
        "bottom-tab-icon",
        "bottom-tab-label",
      ),
    ).join("");
  }
}
