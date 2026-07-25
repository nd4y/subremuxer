/* subremuxer admin UI — vanilla JS, no build step. */
"use strict";

/* ------------------------------------------------------------------ utils */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/** Minimal hyperscript. Children may be nodes, strings, or nested arrays. */
function h(tag, props = null, ...children) {
  const node = document.createElement(tag);
  if (props) {
    for (const [key, value] of Object.entries(props)) {
      if (value === null || value === undefined || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "text") node.textContent = value;
      else if (key === "html") node.innerHTML = value;
      else if (key === "dataset") Object.assign(node.dataset, value);
      else if (key.startsWith("on")) node.addEventListener(key.slice(2).toLowerCase(), value);
      else if (value === true) node.setAttribute(key, "");
      else node.setAttribute(key, value);
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

const icons = {
  copy: '<svg viewBox="0 0 24 24"><rect x="9" y="9" width="11" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M5 15V6a2 2 0 0 1 2-2h9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  qr: '<svg viewBox="0 0 24 24"><rect x="3.5" y="3.5" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="13.5" y="3.5" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><rect x="3.5" y="13.5" width="7" height="7" rx="1.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M14 14h3v3h-3zM19 19h2v2h-2zM14 19h2v2h-2zM19 14h2v2h-2z" fill="currentColor"/></svg>',
  chevron: '<svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  trash: '<svg viewBox="0 0 24 24"><path d="M5 7h14M10 7V5h4v2M7 7l1 12h8l1-12" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2.4M12 19.6V22M22 12h-2.4M4.4 12H2M19.1 4.9l-1.7 1.7M6.6 17.4l-1.7 1.7M19.1 19.1l-1.7-1.7M6.6 6.6 4.9 4.9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  auto: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor"/></svg>',
};

/* -------------------------------------------------------------------- api */

class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(method, path, body) {
  const options = { method, headers: {}, credentials: "same-origin" };
  if (body !== undefined) {
    options.headers["content-type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  const response = await fetch(path, options);
  if (response.status === 401) {
    state.authenticated = false;
    showLogin();
    throw new ApiError("Требуется авторизация", 401);
  }
  const text = await response.text();
  let payload = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = text;
    }
  }
  if (!response.ok) {
    const detail =
      payload && typeof payload === "object" && payload.detail ? payload.detail : response.statusText;
    throw new ApiError(String(detail), response.status);
  }
  return payload;
}

const api = {
  get: (path) => request("GET", path),
  post: (path, body) => request("POST", path, body),
  put: (path, body) => request("PUT", path, body),
  del: (path) => request("DELETE", path),
};

/* ------------------------------------------------------------------ state */

const state = {
  authenticated: false,
  route: "profiles",
  meta: null,
  profiles: [],
  settings: null,
  logs: [],
  logsCursor: null,
  logsOnlyErrors: false,
  logNodeCache: new Map(),
};

/* ------------------------------------------------------------------ theme */

const THEME_KEY = "subremuxer.theme";

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem(THEME_KEY, theme);
  const glyph = theme === "light" ? icons.sun : theme === "dark" ? icons.moon : icons.auto;
  const themeIcon = $("#theme-icon");
  if (themeIcon) themeIcon.innerHTML = glyph;
  const topbarTheme = $("#topbar-theme");
  if (topbarTheme) topbarTheme.innerHTML = glyph;
  $$("#theme-segmented .segmented__item").forEach((item) =>
    item.classList.toggle("is-selected", item.dataset.theme === theme)
  );
}

function cycleTheme() {
  const order = ["auto", "light", "dark"];
  const current = localStorage.getItem(THEME_KEY) || "auto";
  applyTheme(order[(order.indexOf(current) + 1) % order.length]);
}

/* --------------------------------------------------------------- snackbar */

let snackbarTimer = null;

function toast(message, variant = "") {
  const node = $("#snackbar");
  node.textContent = message;
  node.className = `snackbar${variant ? ` snackbar--${variant}` : ""}`;
  node.hidden = false;
  clearTimeout(snackbarTimer);
  snackbarTimer = setTimeout(() => {
    node.classList.add("is-closing");
    setTimeout(() => {
      node.hidden = true;
    }, 200);
  }, 3600);
}

async function copyToClipboard(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("Скопировано");
  } catch {
    // Clipboard API needs a secure context; plain HTTP deployments fall back.
    const area = h("textarea", { class: "mono" });
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    try {
      document.execCommand("copy");
      toast("Скопировано");
    } catch {
      toast("Не удалось скопировать", "error");
    }
    area.remove();
  }
}

/* ------------------------------------------------------------------ sheet */

const sheet = {
  node: null,
  scrim: null,
  open: false,
  onClose: null,
  historyPushed: false,
};

function openSheet({ title, body, footer, onClose }) {
  sheet.node = $("#sheet");
  sheet.scrim = $("#scrim");
  $("#sheet-title").textContent = title;
  const bodyNode = $("#sheet-body");
  const footerNode = $("#sheet-footer");
  bodyNode.replaceChildren(...[body].flat().filter(Boolean));
  footerNode.replaceChildren(...[footer || []].flat().filter(Boolean));
  bodyNode.scrollTop = 0;

  sheet.node.style.transform = "";
  sheet.node.classList.remove("is-closing", "is-dragging");
  sheet.scrim.classList.remove("is-closing");
  sheet.node.hidden = false;
  sheet.scrim.hidden = false;
  document.body.classList.add("is-locked");
  sheet.open = true;
  sheet.onClose = onClose || null;

  // Android's back gesture should dismiss the sheet, not leave the app.
  history.pushState({ sheet: true }, "");
  sheet.historyPushed = true;

  const focusTarget = bodyNode.querySelector("input, select, textarea, button");
  if (focusTarget && window.matchMedia("(min-width: 840px)").matches) focusTarget.focus();
}

function closeSheet({ fromHistory = false } = {}) {
  if (!sheet.open) return;
  sheet.open = false;
  sheet.node.classList.add("is-closing");
  sheet.scrim.classList.add("is-closing");
  document.body.classList.remove("is-locked");
  const finish = () => {
    sheet.node.hidden = true;
    sheet.scrim.hidden = true;
    sheet.node.style.transform = "";
    $("#sheet-body").replaceChildren();
    $("#sheet-footer").replaceChildren();
  };
  setTimeout(finish, 240);
  if (sheet.onClose) sheet.onClose();
  sheet.onClose = null;
  if (sheet.historyPushed && !fromHistory) {
    sheet.historyPushed = false;
    history.back();
  } else {
    sheet.historyPushed = false;
  }
}

/** Drag-to-dismiss, mobile only — on desktop the sheet is a centred dialog. */
function initSheetDrag() {
  const handle = $("#sheet-handle");
  const node = $("#sheet");
  let startY = 0;
  let lastY = 0;
  let startTime = 0;
  let dragging = false;

  handle.addEventListener("pointerdown", (event) => {
    if (window.matchMedia("(min-width: 840px)").matches) return;
    dragging = true;
    startY = lastY = event.clientY;
    startTime = performance.now();
    node.classList.add("is-dragging");
    handle.setPointerCapture(event.pointerId);
  });

  handle.addEventListener("pointermove", (event) => {
    if (!dragging) return;
    lastY = event.clientY;
    const delta = Math.max(0, lastY - startY);
    node.style.transform = `translateY(${delta}px)`;
  });

  const release = () => {
    if (!dragging) return;
    dragging = false;
    node.classList.remove("is-dragging");
    const distance = lastY - startY;
    const velocity = distance / Math.max(1, performance.now() - startTime);
    if (distance > 120 || velocity > 0.6) {
      node.style.transform = "";
      closeSheet();
    } else {
      node.style.transition = "transform 240ms cubic-bezier(0.32, 1.5, 0.44, 1)";
      node.style.transform = "";
      setTimeout(() => {
        node.style.transition = "";
      }, 260);
    }
  };

  handle.addEventListener("pointerup", release);
  handle.addEventListener("pointercancel", release);
}

/* ------------------------------------------------------------ form pieces */

function field({ id, label, value = "", type = "text", inputmode, support, attrs = {} }) {
  const input = h("input", {
    class: "field__input",
    id,
    type,
    placeholder: " ",
    spellcheck: "false",
    autocapitalize: "off",
    autocomplete: "off",
    inputmode,
    ...attrs,
  });
  input.value = value ?? "";
  const wrap = h("label", { class: "field" }, input, h("span", { class: "field__label", text: label }));
  if (!support) return wrap;
  return h("div", {}, wrap, h("p", { class: "field__support", text: support }));
}

function selectField({ id, label, value, options }) {
  const select = h(
    "select",
    { class: "field__select", id },
    options.map((option) =>
      h("option", { value: option.value, selected: option.value === value ? true : null }, option.label)
    )
  );
  select.value = value;
  return h("label", { class: "field" }, select, h("span", { class: "field__label", text: label }));
}

function segmented({ value, options, onChange }) {
  const node = h(
    "div",
    { class: "segmented", role: "group" },
    options.map((option) =>
      h("button", {
        class: `segmented__item${option.value === value ? " is-selected" : ""}`,
        type: "button",
        text: option.label,
        dataset: { value: option.value },
      })
    )
  );
  node.addEventListener("click", (event) => {
    const button = event.target.closest(".segmented__item");
    if (!button) return;
    $$(".segmented__item", node).forEach((item) => item.classList.toggle("is-selected", item === button));
    onChange(button.dataset.value);
  });
  return node;
}

function switchRow({ id, title, hint, checked, onChange }) {
  const input = h("input", { type: "checkbox", id, checked: checked ? true : null });
  input.addEventListener("change", () => onChange(input.checked));
  return h(
    "label",
    { class: "switch" },
    input,
    h("span", { class: "switch__track" }, h("span", { class: "switch__thumb" })),
    h(
      "span",
      { class: "switch__text" },
      h("span", { class: "switch__title", text: title }),
      hint ? h("span", { class: "switch__hint", text: hint }) : null
    )
  );
}

function section(title, hint, ...children) {
  return h(
    "section",
    { class: "section" },
    h("h3", { class: "section__title", text: title }),
    hint ? h("p", { class: "section__hint", text: hint }) : null,
    ...children
  );
}

/* --------------------------------------------------------------- profiles */

function hostOf(url) {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

const HWID_MODE_LABELS = {
  override: "HWID подменяется",
  fallback: "HWID добавляется",
  passthrough: "HWID клиента",
};

function profileCard(profile) {
  const badges = [
    h("span", { class: "badge badge--primary", text: HWID_MODE_LABELS[profile.hwid_mode] }),
  ];
  const conditions = profile.filter?.conditions?.length || 0;
  if (profile.filter?.mode === "raw") {
    badges.push(h("span", { class: "badge", text: "своя регулярка" }));
  } else if (conditions) {
    badges.push(h("span", { class: "badge", text: `${conditions} усл.` }));
  } else {
    badges.push(h("span", { class: "badge", text: "без фильтра" }));
  }
  if (profile.protocols.length) {
    badges.push(h("span", { class: "badge badge--tertiary", text: profile.protocols.join(", ") }));
  }
  if (profile.cache_ttl) {
    badges.push(h("span", { class: "badge", text: `кэш ${profile.cache_ttl}s` }));
  }
  if (!profile.enabled) {
    badges.push(h("span", { class: "badge badge--error", text: "выключен" }));
  }

  return h(
    "article",
    { class: `profile${profile.enabled ? "" : " is-disabled"}` },
    h(
      "div",
      { class: "profile__head" },
      h(
        "div",
        { class: "profile__title" },
        h("h2", { class: "profile__name", text: profile.name }),
        h("span", { class: "profile__upstream", text: hostOf(profile.upstream_url) })
      ),
      h("button", {
        class: "icon-btn",
        type: "button",
        html: icons.qr,
        "aria-label": "Ссылка и QR-код",
        onclick: () => showLinkSheet(profile),
      })
    ),
    h("div", { class: "profile__badges" }, badges),
    h(
      "div",
      { class: "profile__link", onclick: () => copyToClipboard(profile.subscription_url) },
      h("span", { text: profile.subscription_url }),
      h("button", {
        class: "icon-btn",
        type: "button",
        html: icons.copy,
        "aria-label": "Скопировать ссылку",
      })
    ),
    h(
      "div",
      { class: "profile__actions" },
      h("button", {
        class: "btn btn--tonal btn--small",
        type: "button",
        text: "Настроить",
        onclick: () => showProfileEditor(profile),
      }),
      h("button", {
        class: "btn btn--outlined btn--small",
        type: "button",
        text: "Тест",
        onclick: () => showProfileEditor(profile, { runTest: true }),
      }),
      h("span", { class: "spacer" }),
      h("button", {
        class: "icon-btn",
        type: "button",
        html: icons.trash,
        "aria-label": "Удалить профиль",
        onclick: () => deleteProfile(profile),
      })
    )
  );
}

async function deleteProfile(profile) {
  if (!window.confirm(`Удалить профиль «${profile.name}»? Ссылка перестанет работать.`)) return;
  try {
    await api.del(`/api/profiles/${profile.id}`);
    toast("Профиль удалён");
    await loadProfiles();
  } catch (error) {
    toast(error.message, "error");
  }
}

function renderStats(stats) {
  const node = $("#stats");
  if (!stats) {
    node.replaceChildren();
    return;
  }
  const cards = [
    { label: "Профилей активно", value: `${stats.profiles_enabled}/${stats.profiles_total}`, accent: true },
    { label: "Запросов за сутки", value: stats.requests_24h },
    { label: "Отдано серверов", value: stats.nodes_served_24h },
    { label: "Ошибок за сутки", value: stats.errors_24h },
  ];
  node.replaceChildren(
    ...cards.map((card) =>
      h(
        "div",
        { class: `stat${card.accent ? " stat--accent" : ""}` },
        h("span", { class: "stat__value", text: String(card.value) }),
        h("span", { class: "stat__label", text: card.label })
      )
    )
  );
}

function renderProfiles() {
  const list = $("#profiles-list");
  list.className = "list list--profiles";
  if (!state.profiles.length) {
    list.className = "list";
    list.replaceChildren(
      h(
        "div",
        { class: "empty" },
        h("p", { class: "empty__title", text: "Пока нет ни одного профиля" }),
        h("p", {
          text: "Добавьте ссылку на исходную подписку — приложение будет проксировать её, подменять HWID и отсеивать лишние серверы.",
        })
      )
    );
    return;
  }
  list.replaceChildren(...state.profiles.map(profileCard));
}

async function loadProfiles() {
  const list = $("#profiles-list");
  if (!state.profiles.length) {
    list.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  }
  const [profiles, stats] = await Promise.all([api.get("/api/profiles"), api.get("/api/stats")]);
  state.profiles = profiles;
  renderProfiles();
  renderStats(stats);
}

/* ------------------------------------------------------- link & QR sheet */

const CLIENT_LINKS = [
  { label: "Happ", build: (url) => `happ://add/${url}` },
  { label: "v2RayTun", build: (url) => `v2raytun://import/${url}` },
  { label: "sing-box", build: (url) => `sing-box://import-remote-profile?url=${encodeURIComponent(url)}` },
  { label: "Clash", build: (url) => `clash://install-config?url=${encodeURIComponent(url)}` },
];

function showLinkSheet(profile) {
  const url = profile.subscription_url;
  const body = [
    h(
      "div",
      { class: "qr" },
      h("div", { class: "qr__frame" }, h("img", { src: `/api/profiles/${profile.id}/qr.svg`, alt: "QR-код подписки" })),
      h("div", { class: "qr__url", text: url })
    ),
    section(
      "Открыть в клиенте",
      "Ссылки-схемы работают, если приложение установлено на этом устройстве.",
      h(
        "div",
        { class: "chipset" },
        CLIENT_LINKS.map((client) =>
          h("a", { class: "chip chip--assist", href: client.build(url), text: client.label })
        )
      )
    ),
    section(
      "Безопасность",
      "Смена токена мгновенно ломает старую ссылку — используйте, если она куда-то утекла.",
      h("button", {
        class: "btn btn--outlined btn--danger",
        type: "button",
        text: "Сменить токен",
        onclick: async () => {
          if (!window.confirm("Сменить токен? Старая ссылка перестанет работать во всех клиентах.")) return;
          try {
            await api.post(`/api/profiles/${profile.id}/rotate-token`);
            toast("Токен обновлён");
            closeSheet();
            await loadProfiles();
          } catch (error) {
            toast(error.message, "error");
          }
        },
      })
    ),
  ];

  openSheet({
    title: profile.name,
    body,
    footer: [
      h("button", {
        class: "btn btn--tonal",
        type: "button",
        text: "Скопировать ссылку",
        onclick: () => copyToClipboard(url),
      }),
      h("button", { class: "btn btn--filled", type: "button", text: "Готово", onclick: () => closeSheet() }),
    ],
  });
}

/* -------------------------------------------------------- profile editor */

function emptyProfile() {
  return {
    id: null,
    name: "",
    upstream_url: "",
    enabled: true,
    hwid_mode: "override",
    hwid: "",
    device_os: "",
    device_ver: "",
    device_model: "",
    filter: { mode: "builder", match: "all", case_sensitive: false, conditions: [], include_regex: "", exclude_regex: "" },
    protocols: [],
    output_format: "auto",
    upstream_ua: "",
    cache_ttl: 0,
  };
}

function showProfileEditor(existing, { runTest = false } = {}) {
  const meta = state.meta;
  const draft = existing
    ? JSON.parse(JSON.stringify({ ...emptyProfile(), ...existing, filter: { ...emptyProfile().filter, ...existing.filter } }))
    : emptyProfile();
  draft.hwid = draft.hwid || "";
  draft.upstream_ua = draft.upstream_ua || "";

  /* --- basics ---------------------------------------------------------- */
  const nameField = field({ id: "p-name", label: "Название", value: draft.name });
  const urlField = field({
    id: "p-url",
    label: "Ссылка на исходную подписку",
    value: draft.upstream_url,
    inputmode: "url",
    attrs: { autocomplete: "url" },
  });
  const enabledSwitch = switchRow({
    id: "p-enabled",
    title: "Профиль включён",
    hint: "Выключенный отдаёт клиентам 404 и не обращается к панели.",
    checked: draft.enabled,
    onChange: (value) => {
      draft.enabled = value;
    },
  });

  /* --- HWID ------------------------------------------------------------ */
  const hwidInput = field({
    id: "p-hwid",
    label: "HWID (пусто — берётся из настроек)",
    value: draft.hwid,
  });
  const hwidHint = h("p", { class: "field__support", text: "" });
  const hwidInputNode = $("input", hwidInput);

  function refreshHwidHint() {
    const value = hwidInputNode.value.trim();
    if (!value) {
      hwidHint.className = "field__support";
      const fallback = state.settings?.default_hwid;
      hwidHint.textContent = fallback
        ? `Будет использован HWID по умолчанию: ${fallback}`
        : "HWID по умолчанию не задан — заголовок клиента останется как есть.";
      return;
    }
    const valid = /^[a-zA-Z0-9=-]{10,64}$/.test(value);
    hwidHint.className = `field__support${valid ? "" : " field__support--error"}`;
    hwidHint.textContent = valid
      ? "Формат подходит под проверку панели."
      : "Панель принимает 10–64 символа: латиница, цифры, «=» и «-». Иначе заголовок будет проигнорирован.";
  }
  hwidInputNode.addEventListener("input", refreshHwidHint);
  refreshHwidHint();

  const HWID_MODE_HINTS = {
    override: "Всегда отправлять наш HWID, даже если клиент прислал свой.",
    fallback: "Отправлять наш HWID только когда клиент не прислал свой.",
    passthrough: "Ничего не подставлять — прокидывать HWID клиента как есть.",
  };
  const hwidModeHint = h("p", { class: "section__hint", text: HWID_MODE_HINTS[draft.hwid_mode] });
  const hwidMode = segmented({
    value: draft.hwid_mode,
    options: [
      { value: "override", label: "Подменять" },
      { value: "fallback", label: "Добавлять" },
      { value: "passthrough", label: "Не трогать" },
    ],
    onChange: (value) => {
      draft.hwid_mode = value;
      hwidModeHint.textContent = HWID_MODE_HINTS[value];
    },
  });

  const deviceGrid = h(
    "div",
    { class: "grid grid--3" },
    field({ id: "p-os", label: "ОС устройства", value: draft.device_os }),
    field({ id: "p-ver", label: "Версия ОС", value: draft.device_ver }),
    field({ id: "p-model", label: "Модель", value: draft.device_model })
  );

  /* --- filter builder --------------------------------------------------- */
  const conditionsBox = h("div", { class: "conditions" });
  const regexPreview = h("div", { class: "regex-preview" });
  const builderBox = h("div", { class: "section" });
  const rawBox = h("div", { class: "section" });

  function renderRegexPreview() {
    const regex = buildRegexLocally(draft.filter);
    regexPreview.replaceChildren();
    if (!regex) {
      regexPreview.className = "regex-preview regex-preview--empty";
      regexPreview.textContent = "Условий нет — пройдут все серверы.";
      return;
    }
    regexPreview.className = "regex-preview";
    regexPreview.append(
      h("code", { text: regex }),
      h("button", {
        class: "icon-btn",
        type: "button",
        html: icons.copy,
        "aria-label": "Скопировать регулярку",
        onclick: () => copyToClipboard(regex),
      })
    );
  }

  function conditionRow(condition, index) {
    const opSelect = selectField({
      id: `cond-op-${index}`,
      label: "Условие",
      value: condition.op,
      options: Object.entries(meta.condition_ops).map(([value, label]) => ({ value, label })),
    });
    $("select", opSelect).addEventListener("change", (event) => {
      condition.op = event.target.value;
      renderRegexPreview();
    });

    const valueField = field({ id: `cond-val-${index}`, label: "Значение", value: condition.value });
    $("input", valueField).addEventListener("input", (event) => {
      condition.value = event.target.value;
      renderRegexPreview();
    });

    return h(
      "div",
      { class: "condition" },
      h("div", { class: "condition__fields" }, opSelect, valueField),
      h("button", {
        class: "icon-btn",
        type: "button",
        html: icons.trash,
        "aria-label": "Удалить условие",
        onclick: () => {
          draft.filter.conditions.splice(index, 1);
          renderConditions();
        },
      })
    );
  }

  function renderConditions() {
    conditionsBox.replaceChildren(...draft.filter.conditions.map(conditionRow));
    if (!draft.filter.conditions.length) {
      conditionsBox.append(
        h("p", { class: "section__hint", text: "Условий нет — в подписку попадут все серверы." })
      );
    }
    renderRegexPreview();
  }

  const matchToggle = segmented({
    value: draft.filter.match,
    options: [
      { value: "all", label: "Все условия (И)" },
      { value: "any", label: "Любое (ИЛИ)" },
    ],
    onChange: (value) => {
      draft.filter.match = value;
      renderRegexPreview();
    },
  });

  const caseSwitch = switchRow({
    id: "p-case",
    title: "Учитывать регистр",
    hint: "По умолчанию «lte» и «LTE» — одно и то же.",
    checked: draft.filter.case_sensitive,
    onChange: (value) => {
      draft.filter.case_sensitive = value;
      renderRegexPreview();
    },
  });

  const presetChips = h(
    "div",
    { class: "chipset" },
    meta.presets.map((preset) =>
      h(
        "button",
        {
          class: "chip chip--preset",
          type: "button",
          onclick: () => {
            draft.filter.mode = "builder";
            draft.filter.match = preset.match;
            draft.filter.conditions = JSON.parse(JSON.stringify(preset.conditions));
            renderFilterMode();
            toast(`Шаблон «${preset.title}» применён`);
          },
        },
        h("b", { text: preset.title }),
        h("small", { text: preset.description })
      )
    )
  );

  const includeField = field({ id: "p-include", label: "include regexp", value: draft.filter.include_regex });
  const excludeField = field({ id: "p-exclude", label: "exclude regexp", value: draft.filter.exclude_regex });
  $("input", includeField).addEventListener("input", (event) => {
    draft.filter.include_regex = event.target.value;
  });
  $("input", excludeField).addEventListener("input", (event) => {
    draft.filter.exclude_regex = event.target.value;
  });

  builderBox.replaceChildren(
    matchToggle,
    conditionsBox,
    h("button", {
      class: "btn btn--tonal btn--small",
      type: "button",
      text: "+ Добавить условие",
      onclick: () => {
        draft.filter.conditions.push({ op: "contains", value: "" });
        renderConditions();
      },
    }),
    h("p", { class: "section__hint", text: "Получившееся регулярное выражение — ровно то, что применяется к именам серверов:" }),
    regexPreview,
    h("p", { class: "section__hint", text: "Готовые шаблоны:" }),
    presetChips
  );

  rawBox.replaceChildren(
    h("p", {
      class: "section__hint",
      text: "Сервер остаётся, если его имя подходит под include и не подходит под exclude. Пустое поле — условие не проверяется.",
    }),
    includeField,
    excludeField
  );

  const filterModeToggle = segmented({
    value: draft.filter.mode,
    options: [
      { value: "builder", label: "Конструктор" },
      { value: "raw", label: "Своя регулярка" },
    ],
    onChange: (value) => {
      draft.filter.mode = value;
      renderFilterMode();
    },
  });

  function renderFilterMode() {
    builderBox.hidden = draft.filter.mode !== "builder";
    rawBox.hidden = draft.filter.mode !== "raw";
    if (draft.filter.mode === "builder") renderConditions();
  }

  /* --- protocols -------------------------------------------------------- */
  const protocolChips = h(
    "div",
    { class: "chipset" },
    meta.protocols.map((protocol) =>
      h("button", {
        class: `chip chip--filter${draft.protocols.includes(protocol) ? " is-selected" : ""}`,
        type: "button",
        text: protocol,
        onclick: (event) => {
          const index = draft.protocols.indexOf(protocol);
          if (index >= 0) draft.protocols.splice(index, 1);
          else draft.protocols.push(protocol);
          event.currentTarget.classList.toggle("is-selected", index < 0);
        },
      })
    )
  );

  /* --- advanced --------------------------------------------------------- */
  const uaPresetSelect = selectField({
    id: "p-ua-preset",
    label: "Чем представляться панели",
    value: meta.user_agent_presets.find((preset) => preset.value === draft.upstream_ua)?.id || "custom",
    options: [
      ...meta.user_agent_presets.map((preset) => ({ value: preset.id, label: preset.label })),
      { value: "custom", label: "Свой User-Agent" },
    ],
  });
  const uaCustomField = field({ id: "p-ua", label: "User-Agent", value: draft.upstream_ua });
  const uaCustomInput = $("input", uaCustomField);
  uaCustomField.hidden = Boolean(meta.user_agent_presets.find((preset) => preset.value === draft.upstream_ua));
  $("select", uaPresetSelect).addEventListener("change", (event) => {
    const preset = meta.user_agent_presets.find((item) => item.id === event.target.value);
    if (preset) {
      draft.upstream_ua = preset.value;
      uaCustomInput.value = preset.value;
      uaCustomField.hidden = true;
    } else {
      uaCustomField.hidden = false;
      uaCustomInput.focus();
    }
  });
  uaCustomInput.addEventListener("input", (event) => {
    draft.upstream_ua = event.target.value;
  });

  const outputToggle = segmented({
    value: draft.output_format,
    options: [
      { value: "auto", label: "Как у панели" },
      { value: "base64", label: "Base64" },
      { value: "plain", label: "Открытый список" },
    ],
    onChange: (value) => {
      draft.output_format = value;
    },
  });

  const cacheField = field({
    id: "p-cache",
    label: "Кэш ответа панели, секунд",
    value: String(draft.cache_ttl || 0),
    type: "number",
    inputmode: "numeric",
    attrs: { min: "0", max: "86400" },
  });

  /* --- test panel ------------------------------------------------------- */
  const testResults = h("div", { class: "section" });
  testResults.hidden = true;

  function collect() {
    return {
      name: $("input", nameField).value,
      upstream_url: $("input", urlField).value,
      enabled: draft.enabled,
      hwid_mode: draft.hwid_mode,
      hwid: hwidInputNode.value.trim(),
      device_os: $("#p-os").value,
      device_ver: $("#p-ver").value,
      device_model: $("#p-model").value,
      filter: {
        ...draft.filter,
        include_regex: $("#p-include").value,
        exclude_regex: $("#p-exclude").value,
      },
      protocols: draft.protocols.slice(),
      output_format: draft.output_format,
      upstream_ua: draft.upstream_ua,
      cache_ttl: Number($("#p-cache").value || 0),
    };
  }

  async function runFilterTest() {
    const payload = collect();
    if (!payload.upstream_url) {
      toast("Сначала укажите ссылку на подписку", "error");
      return;
    }
    testResults.hidden = false;
    testResults.replaceChildren(
      h("h3", { class: "section__title", text: "Результат теста" }),
      h("div", { class: "skeleton skeleton--log" })
    );
    testResults.scrollIntoView({ behavior: "smooth", block: "nearest" });
    try {
      const result = await api.post("/api/filter/test", {
        profile_id: draft.id,
        upstream_url: payload.upstream_url,
        hwid: payload.hwid,
        hwid_mode: payload.hwid_mode,
        device_os: payload.device_os,
        device_ver: payload.device_ver,
        device_model: payload.device_model,
        upstream_ua: payload.upstream_ua,
        filter: payload.filter,
        protocols: payload.protocols,
      });
      renderTestResult(result);
    } catch (error) {
      testResults.replaceChildren(
        h("h3", { class: "section__title", text: "Результат теста" }),
        h("p", { class: "field__support field__support--error", text: error.message })
      );
    }
  }

  function renderTestResult(result) {
    const summary = h(
      "div",
      { class: "test-summary" },
      h("span", { class: "badge badge--success", text: `оставлено ${result.kept} из ${result.total}` }),
      result.format_label ? h("span", { class: "badge badge--primary", text: result.format_label }) : null,
      h("span", { class: "badge", text: `${result.upstream_ms} мс` }),
      result.hwid_sent ? h("span", { class: "badge", text: `HWID: ${result.hwid_sent}` }) : null
    );

    const children = [h("h3", { class: "section__title", text: "Результат теста" }), summary];
    if (result.error) {
      children.push(h("p", { class: "field__support field__support--error", text: result.error }));
      if (result.body_preview) {
        children.push(h("pre", { class: "regex-preview", text: result.body_preview.slice(0, 600) }));
      }
    }
    if (result.nodes.length) {
      children.push(
        h(
          "div",
          { class: "test-list" },
          result.nodes.map((node) =>
            h(
              "div",
              { class: `node${node.kept ? "" : " node--dropped"}` },
              h("span", { class: "node__mark", text: node.kept ? "✓" : "✕" }),
              h(
                "span",
                { class: "node__text" },
                h("span", { class: "node__name", text: node.name }),
                h("span", {
                  class: "node__reason",
                  text: node.kept ? node.protocol : `${node.protocol} — ${node.detail || node.reason}`,
                })
              )
            )
          )
        )
      );
    }
    testResults.replaceChildren(...children.filter(Boolean));
  }

  async function save() {
    const payload = collect();
    try {
      if (draft.id) await api.put(`/api/profiles/${draft.id}`, payload);
      else await api.post("/api/profiles", payload);
      toast(draft.id ? "Профиль сохранён" : "Профиль создан");
      closeSheet();
      await loadProfiles();
    } catch (error) {
      toast(error.message, "error");
    }
  }

  renderFilterMode();

  openSheet({
    title: draft.id ? "Настройка профиля" : "Новый профиль",
    body: [
      section("Основное", null, nameField, urlField, enabledSwitch),
      section(
        "HWID",
        "Панель считает устройства по заголовку x-hwid. Здесь решается, что мы ей отправим.",
        hwidMode,
        hwidModeHint,
        hwidInput,
        hwidHint,
        deviceGrid
      ),
      section("Фильтр по имени сервера", null, filterModeToggle, builderBox, rawBox),
      section(
        "Протоколы",
        "Ничего не выбрано — проходят все протоколы.",
        protocolChips
      ),
      section(
        "Дополнительно",
        "Формат подписки выбирает панель по User-Agent — здесь можно заставить её отдавать нужный.",
        uaPresetSelect,
        uaCustomField,
        outputToggle,
        cacheField
      ),
      testResults,
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Тест", onclick: runFilterTest }),
      h("button", { class: "btn btn--filled", type: "button", text: "Сохранить", onclick: save }),
    ],
  });

  if (runTest) setTimeout(runFilterTest, 300);
}

/** Mirrors app/filtering.py::build_regex so the preview needs no round-trip. */
function buildRegexLocally(filter) {
  if (filter.mode === "raw") return "";
  const escape = (value) => value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const fragment = (condition) => {
    const literal = escape(condition.value);
    const raw = `(?:${condition.value})`;
    switch (condition.op) {
      case "contains":
        return `(?=.*${literal})`;
      case "not_contains":
        return `(?!.*${literal})`;
      case "starts_with":
        return `(?=${literal})`;
      case "not_starts_with":
        return `(?!${literal})`;
      case "ends_with":
        return `(?=.*${literal}$)`;
      case "not_ends_with":
        return `(?!.*${literal}$)`;
      case "equals":
        return `(?=${literal}$)`;
      case "not_equals":
        return `(?!${literal}$)`;
      case "regex":
        return `(?=.*${raw})`;
      case "not_regex":
        return `(?!.*${raw})`;
      default:
        return "";
    }
  };
  const conditions = filter.conditions.filter((condition) => condition.value);
  if (!conditions.length) return "";
  const fragments = conditions.map(fragment);
  const flag = filter.case_sensitive ? "" : "(?i)";
  if (filter.match === "all") return `${flag}^${fragments.join("")}.*$`;
  return `${flag}^(?:${fragments.map((part) => `(?:${part}.*)`).join("|")})$`;
}

/* ------------------------------------------------------------------- logs */

function formatTime(ts) {
  const date = new Date(ts * 1000);
  return date.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function logEntry(entry) {
  const failed = Boolean(entry.error) || (entry.status_code || 0) >= 400;
  const warned = !failed && entry.nodes_total > 0 && entry.nodes_kept === 0;
  const node = h(
    "article",
    { class: "log" },
    h(
      "button",
      { class: "log__head", type: "button" },
      h("span", { class: `log__dot${failed ? " log__dot--error" : warned ? " log__dot--warn" : ""}` }),
      h(
        "span",
        { class: "log__text" },
        h("span", { class: "log__title", text: entry.profile_name || "—" }),
        h("span", {
          class: "log__meta",
          text: `${formatTime(entry.ts)} · ${entry.client_ip || "?"} · ${entry.user_agent || "без UA"}`,
        })
      ),
      h("span", { class: "log__count", text: `${entry.nodes_kept}/${entry.nodes_total}` }),
      h("span", { class: "log__chevron", html: icons.chevron })
    ),
    h("div", { class: "log__body" }, h("div", { class: "log__inner" }, h("div", { class: "log__detail" })))
  );

  const detail = $(".log__detail", node);
  $(".log__head", node).addEventListener("click", async () => {
    const opening = !node.classList.contains("is-open");
    node.classList.toggle("is-open", opening);
    if (!opening || detail.childElementCount) return;

    detail.replaceChildren(h("div", { class: "skeleton skeleton--log" }));
    const rows = [
      ["Время", formatTime(entry.ts)],
      ["Клиент", `${entry.client_ip || "?"} · ${entry.user_agent || "без UA"}`],
      ["Апстрим", `${entry.upstream_url || "—"} → ${entry.upstream_status ?? "—"} (${entry.upstream_ms ?? 0} мс)`],
      ["HWID клиента", entry.hwid_in || "не прислан"],
      ["HWID отправлен", `${entry.hwid_sent || "—"} (${entry.hwid_action || "—"})`],
      ["Формат", `${entry.detected_format || "—"} → ${entry.output_format || "—"}`],
      ["Серверов", `получено ${entry.nodes_total}, отдано ${entry.nodes_kept}`],
      ["Размер", `${entry.bytes_in} B → ${entry.bytes_out} B`],
      ["Ответ клиенту", String(entry.status_code ?? "—")],
    ];
    if (entry.error) rows.push(["Ошибка", entry.error]);

    let nodes = state.logNodeCache.get(entry.id);
    if (!nodes) {
      try {
        nodes = (await api.get(`/api/logs/${entry.id}/nodes`)).nodes;
        state.logNodeCache.set(entry.id, nodes);
      } catch {
        nodes = [];
      }
    }

    detail.replaceChildren(
      h("dl", { class: "kv" }, rows.map(([key, value]) => [h("dt", { text: key }), h("dd", { text: value })])),
      nodes.length
        ? h(
            "div",
            { class: "test-list" },
            nodes.map((item) =>
              h(
                "div",
                { class: `node${item.kept ? "" : " node--dropped"}` },
                h("span", { class: "node__mark", text: item.kept ? "✓" : "✕" }),
                h(
                  "span",
                  { class: "node__text" },
                  h("span", { class: "node__name", text: item.name }),
                  h("span", {
                    class: "node__reason",
                    text: item.kept ? item.protocol : `${item.protocol} — ${item.reason}`,
                  })
                )
              )
            )
          )
        : null
    );
  });

  return node;
}

function renderLogs() {
  const list = $("#logs-list");
  if (!state.logs.length) {
    list.replaceChildren(
      h(
        "div",
        { class: "empty" },
        h("p", { class: "empty__title", text: "Логов пока нет" }),
        h("p", { text: "Здесь появится каждый запрос клиента: что пришло от панели, что отфильтровано и почему." })
      )
    );
    $("#logs-more").hidden = true;
    return;
  }
  list.replaceChildren(...state.logs.map(logEntry));
}

async function loadLogs({ append = false } = {}) {
  const list = $("#logs-list");
  if (!append) {
    state.logs = [];
    state.logsCursor = null;
    list.replaceChildren(
      h("div", { class: "skeleton skeleton--log" }),
      h("div", { class: "skeleton skeleton--log" }),
      h("div", { class: "skeleton skeleton--log" })
    );
  }
  const params = new URLSearchParams({ limit: "30" });
  if (state.logsOnlyErrors) params.set("only_errors", "true");
  if (append && state.logsCursor) params.set("before_id", String(state.logsCursor));

  const data = await api.get(`/api/logs?${params}`);
  state.logs = append ? state.logs.concat(data.entries) : data.entries;
  state.logsCursor = data.next_before_id;
  renderLogs();
  $("#logs-more").hidden = data.entries.length < 30;
}

/* --------------------------------------------------------------- settings */

async function loadSettings() {
  state.settings = await api.get("/api/settings");
  $("#default-hwid").value = state.settings.default_hwid || "";
  $("#default-device-os").value = state.settings.default_device_os || "";
  $("#default-device-ver").value = state.settings.default_device_ver || "";
  $("#default-device-model").value = state.settings.default_device_model || "";
  refreshDefaultHwidHint();
}

function refreshDefaultHwidHint() {
  const value = $("#default-hwid").value.trim();
  const hint = $("#default-hwid-hint");
  if (!value) {
    hint.className = "field__support";
    hint.textContent = "10–64 символа: латиница, цифры, «=» и «-».";
    return;
  }
  const valid = /^[a-zA-Z0-9=-]{10,64}$/.test(value);
  hint.className = `field__support${valid ? "" : " field__support--error"}`;
  hint.textContent = valid
    ? "Формат подходит под проверку панели."
    : "Панель проигнорирует такой HWID: нужно 10–64 символа из латиницы, цифр, «=» и «-».";
}

async function saveSettings() {
  try {
    state.settings = await api.put("/api/settings", {
      default_hwid: $("#default-hwid").value.trim(),
      default_device_os: $("#default-device-os").value.trim(),
      default_device_ver: $("#default-device-ver").value.trim(),
      default_device_model: $("#default-device-model").value.trim(),
    });
    toast("Настройки сохранены");
    refreshDefaultHwidHint();
  } catch (error) {
    toast(error.message, "error");
  }
}

/* ----------------------------------------------------------------- router */

const PAGES = {
  profiles: { title: "Профили", subtitle: "Прокси-подписки, которые отдаёт это приложение" },
  logs: { title: "Логи", subtitle: "Кто, когда и что получил" },
  settings: { title: "Настройки", subtitle: "Значения по умолчанию и оформление" },
};

async function navigate(route, { silent = false } = {}) {
  if (!PAGES[route]) route = "profiles";
  state.route = route;

  $$(".page").forEach((page) => {
    page.hidden = page.dataset.page !== route;
  });
  $$("[data-route]").forEach((item) => item.classList.toggle("is-active", item.dataset.route === route));
  $("#page-title").textContent = PAGES[route].title;
  $("#page-subtitle").textContent = PAGES[route].subtitle;
  $("#fab-add").classList.toggle("is-hidden", route !== "profiles");
  if (!silent) location.hash = `#/${route}`;

  try {
    if (route === "profiles") await loadProfiles();
    else if (route === "logs") await loadLogs();
    else if (route === "settings") await loadSettings();
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error");
  }
}

/* -------------------------------------------------------------- auth flow */

function showLogin() {
  $("#shell").hidden = true;
  $("#login").hidden = false;
  document.body.classList.remove("is-locked");
  setTimeout(() => $("#login-password")?.focus(), 100);
}

async function showApp() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  state.meta = await api.get("/api/meta");
  state.settings = await api.get("/api/settings");
  const route = (location.hash.replace("#/", "") || "profiles").split("?")[0];
  await navigate(route, { silent: true });
}

async function boot() {
  applyTheme(localStorage.getItem(THEME_KEY) || "auto");
  initSheetDrag();

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#login-submit");
    const errorNode = $("#login-error");
    errorNode.hidden = true;
    button.disabled = true;
    try {
      await api.post("/api/auth/login", { password: $("#login-password").value });
      state.authenticated = true;
      $("#login-password").value = "";
      await showApp();
    } catch (error) {
      errorNode.textContent = error.message;
      errorNode.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  $$("[data-route]").forEach((item) => {
    item.addEventListener("click", () => navigate(item.dataset.route));
  });

  $("#fab-add").addEventListener("click", () => showProfileEditor(null));
  $("#rail-theme").addEventListener("click", cycleTheme);
  $("#topbar-theme").addEventListener("click", cycleTheme);
  $("#sheet-close").addEventListener("click", () => closeSheet());
  $("#scrim").addEventListener("click", () => closeSheet());
  $("#settings-save").addEventListener("click", saveSettings);
  $("#default-hwid").addEventListener("input", refreshDefaultHwidHint);
  $("#logs-more").addEventListener("click", () => loadLogs({ append: true }));

  $("#theme-segmented").addEventListener("click", (event) => {
    const button = event.target.closest("[data-theme]");
    if (button) applyTheme(button.dataset.theme);
  });

  $("#log-filters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-log-filter]");
    if (!button) return;
    $$("[data-log-filter]").forEach((item) => item.classList.toggle("is-selected", item === button));
    state.logsOnlyErrors = button.dataset.logFilter === "errors";
    loadLogs();
  });

  $("#logs-clear").addEventListener("click", async () => {
    if (!window.confirm("Удалить все записи журнала?")) return;
    try {
      await api.del("/api/logs");
      state.logNodeCache.clear();
      toast("Журнал очищен");
      await loadLogs();
    } catch (error) {
      toast(error.message, "error");
    }
  });

  for (const id of ["topbar-logout", "settings-logout"]) {
    $(`#${id}`).addEventListener("click", async () => {
      await api.post("/api/auth/logout").catch(() => {});
      state.authenticated = false;
      showLogin();
    });
  }

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && sheet.open) closeSheet();
  });

  window.addEventListener("popstate", () => {
    if (sheet.open) {
      closeSheet({ fromHistory: true });
      return;
    }
    const route = (location.hash.replace("#/", "") || "profiles").split("?")[0];
    navigate(route, { silent: true });
  });

  // A subtle divider appears under the app bar once the page scrolls.
  const topbar = $(".topbar");
  window.addEventListener(
    "scroll",
    () => topbar?.classList.toggle("is-stuck", window.scrollY > 4),
    { passive: true }
  );

  try {
    const me = await api.get("/api/auth/me");
    if (me.authenticated) {
      state.authenticated = true;
      await showApp();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
}

document.addEventListener("DOMContentLoaded", boot);
