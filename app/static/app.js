/* Sub Remuxer admin UI — vanilla JS, no build step. */
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
  clone: '<svg viewBox="0 0 24 24"><rect x="4" y="4" width="11" height="11" rx="2.5" fill="none" stroke="currentColor" stroke-width="2"/><path d="M9 20h9a2 2 0 0 0 2-2V9" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  bookmark: '<svg viewBox="0 0 24 24"><path d="M7 4h10a1 1 0 0 1 1 1v15l-6-4-6 4V5a1 1 0 0 1 1-1Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  edit: '<svg viewBox="0 0 24 24"><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17v3Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  sun: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="4" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 2v2.4M12 19.6V22M22 12h-2.4M4.4 12H2M19.1 4.9l-1.7 1.7M6.6 17.4l-1.7 1.7M19.1 19.1l-1.7-1.7M6.6 6.6 4.9 4.9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
  moon: '<svg viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5Z" fill="none" stroke="currentColor" stroke-width="2" stroke-linejoin="round"/></svg>',
  auto: '<svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" stroke-width="2"/><path d="M12 3a9 9 0 0 1 0 18Z" fill="currentColor"/></svg>',
  up: '<svg viewBox="0 0 24 24"><path d="m6 14 6-6 6 6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  down: '<svg viewBox="0 0 24 24"><path d="m6 10 6 6 6-6" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  close: '<svg viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
};

const HWID_RE = /^[a-zA-Z0-9=-]{10,64}$/;

/** The <button class="icon-btn"> pattern repeated across cards and rows. */
function iconButton({ icon, label, onclick, title }) {
  return h("button", { class: "icon-btn", type: "button", html: icon, "aria-label": label, title, onclick });
}

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

/**
 * Runs `action`; on failure shows the server's error as a toast and returns
 * undefined instead of throwing. For the many handlers where "show the error"
 * is the entire failure path — the caller either uses the resolved value or,
 * same as this returning undefined, does nothing further.
 */
async function attempt(action) {
  try {
    return await action();
  } catch (error) {
    toast(error.message, "error");
    return undefined;
  }
}

/** Like attempt(), for call sites that only care whether it worked. */
async function attemptOk(action) {
  try {
    await action();
    return true;
  } catch (error) {
    toast(error.message, "error");
    return false;
  }
}

/* ------------------------------------------------------------------ state */

const state = {
  authenticated: false,
  demo: false,
  // Until /api/auth/me answers, assume the narrower role: a wrong guess then
  // shows too little for a moment instead of flashing what a viewer may not see.
  role: "viewer",
  user: null,
  methods: { password: true, oidc: false },
  oidcName: "OIDC",
  autoLogin: false,
  route: "profiles",
  meta: null,
  profiles: [],
  aggregates: [],
  templates: [],
  settings: null,
  probe: null,
  logs: [],
  logsCursor: null,
  logsOnlyErrors: false,
  logsLoaded: false,
  logNodeCache: new Map(),
  probeTimer: null,
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

/**
 * Material's undo pattern: a destructive action happens immediately, and the
 * snackbar holds the escape hatch while a ring counts the window down.
 */
function toast(message, options = {}) {
  if (typeof options === "string") options = { variant: options };
  const { variant = "", action = null, duration = action ? 7000 : 3600 } = options;

  const node = $("#snackbar");
  clearTimeout(snackbarTimer);

  const children = [h("span", { class: "snackbar__text", text: message })];
  if (action) {
    children.push(
      h("button", {
        class: "snackbar__action",
        type: "button",
        text: action.label,
        onclick: () => {
          clearTimeout(snackbarTimer);
          dismissSnackbar();
          action.onClick();
        },
      }),
      countdownRing(duration)
    );
  }

  node.replaceChildren(...children);
  node.className = `snackbar${variant ? ` snackbar--${variant}` : ""}${action ? " snackbar--action" : ""}`;
  node.hidden = false;
  snackbarTimer = setTimeout(dismissSnackbar, duration);
}

function dismissSnackbar() {
  const node = $("#snackbar");
  if (node.hidden) return;
  node.classList.add("is-closing");
  // Tracked in the same snackbarTimer a following toast() clears — otherwise
  // this 200ms close outlives a toast shown right after dismissing (an Undo
  // action that itself fails and re-toasts an error, for one) and wipes it.
  snackbarTimer = setTimeout(() => {
    node.hidden = true;
    node.replaceChildren();
  }, 200);
}

/**
 * The delete-then-offer-Undo snackbar shared by profiles, templates and probe
 * captures: the action already happened, `onUndo` is what reverses it.
 */
function undoToast(message, onUndo) {
  toast(message, { duration: 7000, action: { label: "Отменить", onClick: () => attemptOk(onUndo) } });
}

function countdownRing(duration) {
  const seconds = Math.ceil(duration / 1000);
  const label = h("span", { class: "countdown__value", text: String(seconds) });
  const ring = h("span", { class: "countdown" }, label);
  ring.style.setProperty("--countdown-duration", `${duration}ms`);
  ring.insertAdjacentHTML(
    "afterbegin",
    '<svg viewBox="0 0 24 24" aria-hidden="true">' +
      '<circle class="countdown__track" cx="12" cy="12" r="10" fill="none" stroke-width="2"/>' +
      '<circle class="countdown__progress" cx="12" cy="12" r="10" fill="none" stroke-width="2"/>' +
      "</svg>"
  );

  let left = seconds;
  const tick = setInterval(() => {
    left -= 1;
    if (left <= 0) {
      clearInterval(tick);
      return;
    }
    label.textContent = String(left);
  }, 1000);
  // Stop the interval as soon as the ring leaves the DOM.
  new MutationObserver((records, observer) => {
    if (!ring.isConnected) {
      clearInterval(tick);
      observer.disconnect();
    }
  }).observe($("#snackbar"), { childList: true });

  return ring;
}

async function copyToClipboard(text, message = "Скопировано") {
  try {
    await navigator.clipboard.writeText(text);
    toast(message);
  } catch {
    // Clipboard API needs a secure context; plain HTTP deployments fall back.
    const area = h("textarea");
    area.value = text;
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.append(area);
    area.select();
    try {
      document.execCommand("copy");
      toast(message);
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
  watcher: null,
};

/**
 * CloseWatcher is what lets Android draw the predictive-back preview: it tells
 * the browser "this gesture will dismiss some UI", instead of the browser having
 * to guess from a history entry. Where it is missing we fall back to pushing a
 * history entry, which still closes the sheet — just without the preview.
 */
const HAS_CLOSE_WATCHER = typeof window.CloseWatcher === "function";

function openSheet({ title, body, footer, onClose, wide = false }) {
  sheet.node = $("#sheet");
  sheet.scrim = $("#scrim");
  sheet.node.classList.toggle("sheet--wide", wide);
  $("#sheet-title").textContent = title;
  const bodyNode = $("#sheet-body");
  const footerNode = $("#sheet-footer");
  bodyNode.replaceChildren(...[body].flat(Infinity).filter(Boolean));
  footerNode.replaceChildren(...[footer || []].flat(Infinity).filter(Boolean));
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
  if (HAS_CLOSE_WATCHER) {
    sheet.watcher = new CloseWatcher();
    sheet.watcher.onclose = () => {
      sheet.watcher = null;
      closeSheet({ fromWatcher: true });
    };
  } else {
    history.pushState({ sheet: true }, "");
    sheet.historyPushed = true;
  }

  const focusTarget = bodyNode.querySelector("input, select, textarea, button");
  if (focusTarget && window.matchMedia("(min-width: 840px)").matches) focusTarget.focus();
}

function closeSheet({ fromHistory = false, fromWatcher = false } = {}) {
  if (!sheet.open) return;
  sheet.open = false;

  if (sheet.watcher && !fromWatcher) {
    const watcher = sheet.watcher;
    sheet.watcher = null;
    watcher.destroy();
  }
  sheet.watcher = null;

  sheet.node.classList.add("is-closing");
  sheet.scrim.classList.add("is-closing");
  document.body.classList.remove("is-locked");
  setTimeout(() => {
    sheet.node.hidden = true;
    sheet.scrim.hidden = true;
    sheet.node.style.transform = "";
    $("#sheet-body").replaceChildren();
    $("#sheet-footer").replaceChildren();
  }, 240);
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
    node.style.transform = `translateY(${Math.max(0, lastY - startY)}px)`;
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

/** Every field control shares the same floating-label <label class="field"> shell. */
function wrapField(control, label) {
  return h("label", { class: "field" }, control, h("span", { class: "field__label", text: label }));
}

function field({ id, label, value = "", type = "text", inputmode, attrs = {} }) {
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
  return wrapField(input, label);
}

function textareaField({ id, label, value = "", rows = 2 }) {
  const area = h("textarea", { class: "field__textarea", id, rows, placeholder: " " });
  area.value = value ?? "";
  return wrapField(area, label);
}

function selectField({ id, label, value, options }) {
  const select = h(
    "select",
    { class: "field__select", id },
    options.map((option) => h("option", { value: option.value }, option.label))
  );
  select.value = value;
  return wrapField(select, label);
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

/* ------------------------------------------------- shared config form */

function emptyConfig() {
  return {
    hwid_mode: "override",
    hwid: "",
    device_os: "",
    device_ver: "",
    device_model: "",
    upstream_ua: "",
    filter: {
      mode: "builder",
      match: "all",
      case_sensitive: false,
      conditions: [],
      include_regex: "",
      exclude_regex: "",
    },
    protocols: [],
    output_format: "auto",
    cache_ttl: 0,
  };
}

function defaultConfig() {
  const defaults = state.meta?.default_profile || {};
  return { ...emptyConfig(), ...defaults };
}

const HWID_MODE_HINTS = {
  override: "Всегда отправлять наш HWID, даже если клиент прислал свой.",
  fallback: "Отправлять наш HWID только когда клиент не прислал свой.",
  passthrough: "Ничего не подставлять — прокидывать HWID клиента как есть.",
};

/**
 * Builds the sections shared by the profile editor and the template editor.
 * Returns the section nodes plus a `collect()` that reads the current values.
 */
function buildConfigForm(draft, { idPrefix = "cfg" } = {}) {
  const meta = state.meta;
  const id = (name) => `${idPrefix}-${name}`;

  /* --- mimicry -------------------------------------------------------- */
  const clientOptions = [
    ...meta.client_presets.map((preset) => ({ value: preset.id, label: preset.label })),
    { value: "custom", label: "Свой User-Agent" },
  ];
  const matchedClient = meta.client_presets.find((preset) => preset.user_agent === draft.upstream_ua);
  const clientSelect = selectField({
    id: id("client"),
    label: "Каким клиентом представляться панели",
    value: matchedClient ? matchedClient.id : "custom",
    options: clientOptions,
  });
  const clientHint = h("p", { class: "section__hint" });
  const clientWarning = h("p", { class: "section__warning" });
  const uaField = field({ id: id("ua"), label: "User-Agent", value: draft.upstream_ua });
  const uaInput = $("input", uaField);

  function refreshClient(presetId) {
    const preset = meta.client_presets.find((item) => item.id === presetId);
    if (preset) {
      draft.upstream_ua = preset.user_agent;
      uaInput.value = preset.user_agent;
      uaField.hidden = true;
      clientHint.textContent = `${preset.hint}. Формат ответа: ${preset.family}.`;
    } else {
      uaField.hidden = false;
      clientHint.textContent = "Панель увидит ровно эту строку User-Agent.";
    }
    // The single most common way to break a working profile: force a format the
    // real client cannot parse, and watch it import zero servers.
    const forces = preset ? preset.forces_format : Boolean(uaInput.value.trim());
    clientWarning.hidden = !forces;
    clientWarning.textContent = forces
      ? "Панель выбирает формат подписки по User-Agent. С этой подменой она будет " +
        "отвечать одним и тем же форматом всем клиентам — убедитесь, что ваш клиент " +
        "его читает, иначе он импортирует ноль серверов."
      : "";
  }
  $("select", clientSelect).addEventListener("change", (event) => refreshClient(event.target.value));
  uaInput.addEventListener("input", (event) => {
    draft.upstream_ua = event.target.value;
    clientWarning.hidden = !event.target.value.trim();
  });
  refreshClient(matchedClient ? matchedClient.id : "custom");

  const osField = field({ id: id("os"), label: "ОС устройства", value: draft.device_os });
  const verField = field({ id: id("ver"), label: "Версия ОС", value: draft.device_ver });
  const modelField = field({ id: id("model"), label: "Модель", value: draft.device_model });
  const deviceInputs = [$("input", osField), $("input", verField), $("input", modelField)];

  const matchedDevice = meta.device_presets.find(
    (preset) =>
      preset.os === draft.device_os &&
      preset.ver === draft.device_ver &&
      preset.model === draft.device_model
  );
  const deviceSelect = selectField({
    id: id("device"),
    label: "Каким устройством представляться",
    value: matchedDevice ? matchedDevice.id : "custom",
    options: [
      ...meta.device_presets.map((preset) => ({ value: preset.id, label: preset.label })),
      { value: "custom", label: "Своё устройство" },
    ],
  });
  const deviceSelectNode = $("select", deviceSelect);
  deviceSelectNode.addEventListener("change", (event) => {
    const preset = meta.device_presets.find((item) => item.id === event.target.value);
    if (!preset) return;
    deviceInputs[0].value = preset.os;
    deviceInputs[1].value = preset.ver;
    deviceInputs[2].value = preset.model;
  });
  // Typing into any device field means the choice is no longer a preset.
  deviceInputs.forEach((input) =>
    input.addEventListener("input", () => {
      const values = deviceInputs.map((node) => node.value.trim());
      const match = meta.device_presets.find(
        (preset) => preset.os === values[0] && preset.ver === values[1] && preset.model === values[2]
      );
      deviceSelectNode.value = match ? match.id : "custom";
    })
  );

  /* --- HWID ----------------------------------------------------------- */
  const hwidField = field({
    id: id("hwid"),
    label: "HWID (пусто — берётся из настроек)",
    value: draft.hwid,
  });
  const hwidInput = $("input", hwidField);
  const hwidHint = h("p", { class: "field__support" });

  function refreshHwidHint() {
    const value = hwidInput.value.trim();
    if (!value) {
      hwidHint.className = "field__support";
      const fallback = state.settings?.default_hwid;
      hwidHint.textContent = fallback
        ? `Будет использован HWID по умолчанию: ${fallback}`
        : "HWID по умолчанию не задан — заголовок клиента останется как есть.";
      return;
    }
    const valid = HWID_RE.test(value);
    hwidHint.className = `field__support${valid ? "" : " field__support--error"}`;
    hwidHint.textContent = valid
      ? "Формат подходит под проверку панели."
      : "Панель принимает 10–64 символа: латиница, цифры, «=» и «-». Иначе заголовок будет проигнорирован.";
  }
  hwidInput.addEventListener("input", refreshHwidHint);
  refreshHwidHint();

  const captures = (state.probe?.captures || []).filter((capture) => capture.hwid);
  const captureSelect = selectField({
    id: id("capture"),
    label: "Подставить из захваченного устройства",
    value: "",
    options: [
      { value: "", label: captures.length ? "— выберите устройство —" : "— захватов пока нет —" },
      ...captures.map((capture) => ({
        value: String(capture.id),
        label: `${capture.device_model || capture.user_agent || "устройство"} · ${capture.hwid}`,
      })),
    ],
  });
  $("select", captureSelect).addEventListener("change", (event) => {
    const capture = captures.find((item) => String(item.id) === event.target.value);
    if (!capture) return;
    hwidInput.value = capture.hwid;
    deviceInputs[0].value = capture.device_os || "";
    deviceInputs[1].value = capture.device_ver || "";
    deviceInputs[2].value = capture.device_model || "";
    deviceInputs[0].dispatchEvent(new Event("input", { bubbles: true }));
    refreshHwidHint();
    toast("Данные захвата подставлены");
  });

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

  /* --- filter --------------------------------------------------------- */
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
      iconButton({ icon: icons.copy, label: "Скопировать регулярку", onclick: () => copyToClipboard(regex) })
    );
  }

  function conditionRow(condition, index) {
    const opSelect = selectField({
      id: id(`cond-op-${index}`),
      label: "Условие",
      value: condition.op,
      options: Object.entries(meta.condition_ops).map(([value, label]) => ({ value, label })),
    });
    $("select", opSelect).addEventListener("change", (event) => {
      condition.op = event.target.value;
      renderRegexPreview();
    });

    const valueField = field({ id: id(`cond-val-${index}`), label: "Значение", value: condition.value });
    $("input", valueField).addEventListener("input", (event) => {
      condition.value = event.target.value;
      renderRegexPreview();
    });

    return h(
      "div",
      { class: "condition" },
      h("div", { class: "condition__fields" }, opSelect, valueField),
      iconButton({
        icon: icons.trash,
        label: "Удалить условие",
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

  const includeField = field({
    id: id("include"),
    label: "include regexp",
    value: draft.filter.include_regex,
  });
  const excludeField = field({
    id: id("exclude"),
    label: "exclude regexp",
    value: draft.filter.exclude_regex,
  });
  const includeInput = $("input", includeField);
  const excludeInput = $("input", excludeField);

  builderBox.replaceChildren(
    segmented({
      value: draft.filter.match,
      options: [
        { value: "all", label: "Все условия (И)" },
        { value: "any", label: "Любое (ИЛИ)" },
      ],
      onChange: (value) => {
        draft.filter.match = value;
        renderRegexPreview();
      },
    }),
    switchRow({
      id: id("case"),
      title: "Учитывать регистр",
      hint: "По умолчанию «lte» и «LTE» — одно и то же.",
      checked: draft.filter.case_sensitive,
      onChange: (value) => {
        draft.filter.case_sensitive = value;
        renderRegexPreview();
      },
    }),
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
    h("p", {
      class: "section__hint",
      text: "Получившееся регулярное выражение — ровно то, что применяется к именам серверов:",
    }),
    regexPreview,
    h("p", { class: "section__hint", text: "Готовые шаблоны условий:" }),
    h(
      "div",
      { class: "chipset" },
      meta.presets.map((preset) =>
        h(
          "button",
          {
            class: "chip chip--preset",
            type: "button",
            onclick: () => {
              draft.filter.match = preset.match;
              draft.filter.conditions = JSON.parse(JSON.stringify(preset.conditions));
              renderConditions();
              toast(`Шаблон «${preset.title}» применён`);
            },
          },
          h("b", { text: preset.title }),
          h("small", { text: preset.description })
        )
      )
    )
  );

  rawBox.replaceChildren(
    h("p", {
      class: "section__hint",
      text: "Сервер остаётся, если его имя подходит под include и не подходит под exclude. Пустое поле — условие не проверяется.",
    }),
    includeField,
    excludeField
  );

  function renderFilterMode() {
    builderBox.hidden = draft.filter.mode !== "builder";
    rawBox.hidden = draft.filter.mode !== "raw";
    if (draft.filter.mode === "builder") renderConditions();
  }

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
  renderFilterMode();

  /* --- protocols ------------------------------------------------------ */
  const chosenProtocols = new Set(draft.protocols);
  const protocolChips = h(
    "div",
    { class: "chipset" },
    meta.protocols.map((protocol) =>
      h("button", {
        class: `chip chip--filter${chosenProtocols.has(protocol) ? " is-selected" : ""}`,
        type: "button",
        text: protocol,
        onclick: (event) => {
          if (chosenProtocols.has(protocol)) chosenProtocols.delete(protocol);
          else chosenProtocols.add(protocol);
          event.currentTarget.classList.toggle("is-selected", chosenProtocols.has(protocol));
        },
      })
    )
  );

  /* --- advanced ------------------------------------------------------- */
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
    id: id("cache"),
    label: "Кэш ответа панели, секунд",
    value: String(draft.cache_ttl || 0),
    type: "number",
    inputmode: "numeric",
    attrs: { min: "0", max: "86400" },
  });
  const cacheInput = $("input", cacheField);

  const sections = [
    section(
      "Устройство (мимикрия)",
      "Панель считает устройства по заголовку x-hwid и подписывает их этими данными. Формата ответа они не меняют — подставлять их безопасно всегда.",
      deviceSelect,
      h("div", { class: "grid grid--3" }, osField, verField, modelField)
    ),
    section(
      "HWID",
      "Здесь решается, что отправить в x-hwid.",
      hwidMode,
      hwidModeHint,
      captureSelect,
      hwidField,
      hwidHint
    ),
    section(
      "Формат подписки",
      "Панель выбирает формат по User-Agent. По умолчанию мы прокидываем настоящий — тогда каждый клиент получает то, что умеет читать.",
      clientSelect,
      clientHint,
      clientWarning,
      uaField,
      outputToggle
    ),
    section("Фильтр по имени сервера", null, filterModeToggle, builderBox, rawBox),
    section("Протоколы", "Ничего не выбрано — проходят все протоколы.", protocolChips),
    section("Дополнительно", null, cacheField),
  ];

  function collect() {
    return {
      hwid_mode: draft.hwid_mode,
      hwid: hwidInput.value.trim(),
      device_os: deviceInputs[0].value.trim(),
      device_ver: deviceInputs[1].value.trim(),
      device_model: deviceInputs[2].value.trim(),
      upstream_ua: uaInput.value,
      filter: {
        ...draft.filter,
        include_regex: includeInput.value,
        exclude_regex: excludeInput.value,
      },
      protocols: [...chosenProtocols],
      output_format: draft.output_format,
      cache_ttl: Number(cacheInput.value || 0),
    };
  }

  return { sections, collect };
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

/** Only a real override is worth showing — it is the setting that changes format. */
function forcedFormatLabel(userAgent) {
  if (!userAgent) return null;
  const preset = state.meta?.client_presets.find((item) => item.user_agent === userAgent);
  return preset ? `формат: ${preset.family}` : "свой User-Agent";
}

function isViewer() {
  return state.role === "viewer";
}

/**
 * What a viewer gets: the name, the link, the QR code. The server does not send
 * the rest, so there is nothing here to hide — this only lays out the fields
 * that actually arrived.
 */
function viewerProfileCard(profile) {
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
        h("span", {
          class: "profile__upstream",
          text: profile.enabled ? "Подписка активна" : "Подписка выключена",
        })
      ),
      iconButton({ icon: icons.qr, label: "Ссылка и QR-код", onclick: () => showLinkSheet(profile) })
    ),
    h(
      "div",
      { class: "profile__link", onclick: () => copyToClipboard(profile.subscription_url) },
      h("span", { text: profile.subscription_url }),
      iconButton({ icon: icons.copy, label: "Скопировать ссылку" })
    ),
    h(
      "div",
      { class: "profile__actions" },
      h("button", {
        class: "btn btn--tonal btn--small",
        type: "button",
        text: "Подключить",
        onclick: () => showLinkSheet(profile),
      })
    )
  );
}

function profileCard(profile) {
  if (isViewer()) return viewerProfileCard(profile);
  const badges = [h("span", { class: "badge badge--primary", text: HWID_MODE_LABELS[profile.hwid_mode] })];

  const forced = forcedFormatLabel(profile.upstream_ua);
  if (forced) badges.push(h("span", { class: "badge badge--tertiary", text: forced }));
  if (profile.device_model) badges.push(h("span", { class: "badge", text: profile.device_model }));

  const conditions = profile.filter?.conditions?.length || 0;
  if (profile.filter?.mode === "raw") badges.push(h("span", { class: "badge", text: "своя регулярка" }));
  else if (conditions) badges.push(h("span", { class: "badge", text: `${conditions} усл.` }));
  else badges.push(h("span", { class: "badge", text: "без фильтра" }));

  if (profile.protocols.length) {
    badges.push(h("span", { class: "badge", text: profile.protocols.join(", ") }));
  }
  if (profile.cache_ttl) badges.push(h("span", { class: "badge", text: `кэш ${profile.cache_ttl}s` }));
  if (!profile.enabled) badges.push(h("span", { class: "badge badge--error", text: "выключен" }));

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
      iconButton({ icon: icons.qr, label: "Ссылка и QR-код", onclick: () => showLinkSheet(profile) })
    ),
    h("div", { class: "profile__badges" }, badges),
    h(
      "div",
      { class: "profile__link", onclick: () => copyToClipboard(profile.subscription_url) },
      h("span", { text: profile.subscription_url }),
      iconButton({ icon: icons.copy, label: "Скопировать ссылку" })
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
      iconButton({
        icon: icons.clone,
        label: "Клонировать профиль",
        title: "Клонировать",
        onclick: () => cloneProfile(profile),
      }),
      iconButton({
        icon: icons.bookmark,
        label: "Сохранить как шаблон",
        title: "Сохранить как шаблон",
        onclick: () => saveProfileAsTemplate(profile),
      }),
      iconButton({
        icon: icons.trash,
        label: "Удалить профиль",
        title: "Удалить",
        onclick: () => deleteProfile(profile),
      })
    )
  );
}

async function cloneProfile(profile) {
  await attempt(async () => {
    const clone = await api.post(`/api/profiles/${profile.id}/clone`);
    await loadProfiles();
    toast(`Создана копия «${clone.name}»`, {
      action: { label: "Настроить", onClick: () => showProfileEditor(clone) },
      duration: 6000,
    });
  });
}

/**
 * No confirmation dialog: the delete is immediately reversible, so Material's
 * guidance is to just do it and offer Undo while a ring counts down.
 */
async function deleteProfile(profile) {
  if (!(await attemptOk(() => api.del(`/api/profiles/${profile.id}`)))) return;
  state.profiles = state.profiles.filter((item) => item.id !== profile.id);
  renderProfiles();
  undoToast(`Профиль «${profile.name}» удалён`, async () => {
    await api.post(`/api/profiles/${profile.id}/restore`);
    await loadProfiles();
    toast(`Профиль «${profile.name}» восстановлен`);
  });
}

function saveProfileAsTemplate(profile) {
  const nameField = field({
    id: "tpl-from-name",
    label: "Название шаблона",
    value: `Как «${profile.name}»`,
  });
  const descField = textareaField({ id: "tpl-from-desc", label: "Описание (необязательно)" });

  openSheet({
    title: "Сохранить как шаблон",
    body: [
      section(
        "Новый шаблон",
        "В шаблон попадут мимикрия, режим HWID, фильтр, протоколы и формат вывода. Ссылка на подписку и название профиля — нет.",
        nameField,
        descField
      ),
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Отмена", onclick: () => closeSheet() }),
      h("button", {
        class: "btn btn--filled",
        type: "button",
        text: "Сохранить",
        onclick: () =>
          attemptOk(async () => {
            await api.post(`/api/templates/from-profile/${profile.id}`, {
              name: $("input", nameField).value,
              description: $("textarea", descField).value,
            });
            closeSheet();
            await loadTemplates();
            toast("Шаблон сохранён");
          }),
      }),
    ],
  });
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
  if (!state.profiles.length) {
    list.className = "list";
    list.replaceChildren(
      h(
        "div",
        { class: "empty" },
        h("p", { class: "empty__title", text: "Пока нет ни одного профиля" }),
        h("p", {
          text: isViewer()
            ? "Здесь появятся подписки, которые администратор откроет для вас."
            : "Добавьте ссылку на исходную подписку — приложение будет проксировать её, подменять HWID и отсеивать лишние серверы.",
        })
      )
    );
    return;
  }
  list.className = "list list--profiles";
  list.replaceChildren(...state.profiles.map(profileCard));
}

async function loadProfiles() {
  const list = $("#profiles-list");
  if (!state.profiles.length && !list.childElementCount) {
    list.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  }
  // A viewer has no access to the counters, and asking would only produce a 403.
  const [profiles, stats] = await Promise.all([
    api.get("/api/profiles"),
    isViewer() ? Promise.resolve(null) : api.get("/api/stats"),
  ]);
  state.profiles = profiles;
  renderProfiles();
  renderStats(stats);
}

/* ------------------------------------------------------------- aggregates */

function plural(count, forms) {
  const mod10 = count % 10;
  const mod100 = count % 100;
  if (mod10 === 1 && mod100 !== 11) return forms[0];
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1];
  return forms[2];
}

const OUTPUT_FORMAT_LABELS = {
  base64: "Base64",
  plain: "открытый список",
};

/** A viewer is told the same thing about an aggregate as about a profile. */
function viewerAggregateCard(aggregate) {
  return h(
    "article",
    { class: `profile${aggregate.enabled ? "" : " is-disabled"}` },
    h(
      "div",
      { class: "profile__head" },
      h(
        "div",
        { class: "profile__title" },
        h("h2", { class: "profile__name", text: aggregate.name }),
        h("span", {
          class: "profile__upstream",
          text: aggregate.enabled ? "Подписка активна" : "Подписка выключена",
        })
      ),
      iconButton({
        icon: icons.qr,
        label: "Ссылка и QR-код",
        onclick: () => showLinkSheet(aggregate, { kind: "aggregates" }),
      })
    ),
    h(
      "div",
      { class: "profile__link", onclick: () => copyToClipboard(aggregate.subscription_url) },
      h("span", { text: aggregate.subscription_url }),
      iconButton({ icon: icons.copy, label: "Скопировать ссылку" })
    ),
    h(
      "div",
      { class: "profile__actions" },
      h("button", {
        class: "btn btn--tonal btn--small",
        type: "button",
        text: "Подключить",
        onclick: () => showLinkSheet(aggregate, { kind: "aggregates" }),
      })
    )
  );
}

function aggregateCard(aggregate) {
  if (isViewer()) return viewerAggregateCard(aggregate);
  const sources = aggregate.sources || [];
  const badges = [
    h("span", {
      class: "badge badge--primary",
      text: `${sources.length} ${plural(sources.length, ["источник", "источника", "источников"])}`,
    }),
  ];
  if (aggregate.prefix_names) badges.push(h("span", { class: "badge", text: "с подписями" }));
  if (aggregate.dedupe) badges.push(h("span", { class: "badge", text: "без дублей" }));
  if (OUTPUT_FORMAT_LABELS[aggregate.output_format]) {
    badges.push(
      h("span", { class: "badge badge--tertiary", text: OUTPUT_FORMAT_LABELS[aggregate.output_format] })
    );
  }
  // A source whose profile was deleted is the one failure the card can catch
  // before the client does, so it is said out loud rather than left implicit.
  const missing = sources.filter((source) => source.missing).length;
  if (missing) {
    badges.push(
      h("span", {
        class: "badge badge--error",
        text: `${missing} ${plural(missing, ["источник удалён", "источника удалены", "источников удалено"])}`,
      })
    );
  }
  if (!aggregate.enabled) badges.push(h("span", { class: "badge badge--error", text: "выключена" }));

  const summary = sources.length
    ? sources.map((source) => source.name || "удалённый профиль").join(" + ")
    : "источники не выбраны";

  return h(
    "article",
    { class: `profile${aggregate.enabled ? "" : " is-disabled"}` },
    h(
      "div",
      { class: "profile__head" },
      h(
        "div",
        { class: "profile__title" },
        h("h2", { class: "profile__name", text: aggregate.name }),
        h("span", { class: "profile__upstream", text: summary })
      ),
      iconButton({
        icon: icons.qr,
        label: "Ссылка и QR-код",
        onclick: () => showLinkSheet(aggregate, { kind: "aggregates" }),
      })
    ),
    h("div", { class: "profile__badges" }, badges),
    h(
      "div",
      { class: "profile__link", onclick: () => copyToClipboard(aggregate.subscription_url) },
      h("span", { text: aggregate.subscription_url }),
      iconButton({ icon: icons.copy, label: "Скопировать ссылку" })
    ),
    h(
      "div",
      { class: "profile__actions" },
      h("button", {
        class: "btn btn--tonal btn--small",
        type: "button",
        text: "Настроить",
        onclick: () => showAggregateEditor(aggregate),
      }),
      h("span", { class: "spacer" }),
      iconButton({
        icon: icons.trash,
        label: "Удалить сборку",
        title: "Удалить",
        onclick: () => deleteAggregate(aggregate),
      })
    )
  );
}

async function deleteAggregate(aggregate) {
  if (!(await attemptOk(() => api.del(`/api/aggregates/${aggregate.id}`)))) return;
  state.aggregates = state.aggregates.filter((item) => item.id !== aggregate.id);
  renderAggregates();
  undoToast(`Сборка «${aggregate.name}» удалена`, async () => {
    await api.post(`/api/aggregates/${aggregate.id}/restore`);
    await loadAggregates();
    toast(`Сборка «${aggregate.name}» восстановлена`);
  });
}

function renderAggregates() {
  const list = $("#aggregates-list");
  if (!state.aggregates.length) {
    list.className = "list";
    list.replaceChildren(
      h(
        "div",
        { class: "empty" },
        h("p", { class: "empty__title", text: "Пока нет ни одной сборки" }),
        h("p", {
          text: isViewer()
            ? "Здесь появятся объединённые подписки, которые администратор откроет для вас."
            : "Сборка отдаёт несколько профилей по одной ссылке: каждый источник фильтруется своими правилами, а клиент получает общий список серверов.",
        })
      )
    );
    return;
  }
  list.className = "list list--profiles";
  list.replaceChildren(...state.aggregates.map(aggregateCard));
}

async function loadAggregates() {
  const list = $("#aggregates-list");
  if (!state.aggregates.length && !list.childElementCount) {
    list.replaceChildren(h("div", { class: "skeleton" }), h("div", { class: "skeleton" }));
  }
  // The editor picks sources out of the profile list, which may not have been
  // fetched yet if this page was opened straight from a bookmark.
  const [aggregates] = await Promise.all([
    api.get("/api/aggregates"),
    state.profiles.length || isViewer() ? null : loadProfiles(),
  ]);
  state.aggregates = aggregates;
  renderAggregates();
}

function showAggregateEditor(existing) {
  const draft = {
    id: existing?.id ?? null,
    enabled: existing ? existing.enabled : true,
    prefix_names: existing ? existing.prefix_names : true,
    dedupe: existing ? existing.dedupe : true,
    output_format: existing?.output_format || "auto",
    sources: (existing?.sources || []).map((source) => ({
      profile_id: source.profile_id,
      prefix: source.prefix || "",
    })),
  };

  const nameField = field({ id: "a-name", label: "Название", value: existing?.name || "" });
  const enabledSwitch = switchRow({
    id: "a-enabled",
    title: "Сборка включена",
    hint: "Выключенная отдаёт клиентам 404 и не обращается к панелям.",
    checked: draft.enabled,
    onChange: (value) => {
      draft.enabled = value;
    },
  });

  const rows = h("div", { class: "sources" });
  const picker = h("select", { class: "field__select", id: "a-add" });
  const pickerField = wrapField(picker, "Добавить профиль");
  const addButton = h("button", {
    class: "btn btn--tonal btn--small",
    type: "button",
    text: "Добавить",
    onclick: () => {
      if (!picker.value) return;
      draft.sources.push({ profile_id: Number(picker.value), prefix: "" });
      renderSources();
    },
  });

  function move(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= draft.sources.length) return;
    const [item] = draft.sources.splice(index, 1);
    draft.sources.splice(target, 0, item);
    renderSources();
  }

  function sourceRow(source, index) {
    const profile = state.profiles.find((item) => item.id === source.profile_id) || null;
    const prefixInput = h("input", {
      class: "field__input",
      placeholder: " ",
      spellcheck: "false",
      autocapitalize: "off",
    });
    prefixInput.value = source.prefix;
    prefixInput.addEventListener("input", () => {
      source.prefix = prefixInput.value;
    });

    return h(
      "div",
      { class: "source" },
      h(
        "div",
        { class: "source__head" },
        h("span", { class: "source__index", text: String(index + 1) }),
        h("span", {
          class: `source__name${profile ? "" : " source__name--missing"}`,
          text: profile ? profile.name : `Профиль #${source.profile_id} удалён`,
        }),
        profile && !profile.enabled
          ? h("span", { class: "badge badge--error", text: "выключен" })
          : null,
        h("span", { class: "spacer" }),
        iconButton({ icon: icons.up, label: "Поднять источник", onclick: () => move(index, -1) }),
        iconButton({ icon: icons.down, label: "Опустить источник", onclick: () => move(index, 1) }),
        iconButton({
          icon: icons.close,
          label: "Убрать из сборки",
          onclick: () => {
            draft.sources.splice(index, 1);
            renderSources();
          },
        })
      ),
      wrapField(prefixInput, "Подпись (пусто — название профиля)")
    );
  }

  function renderSources() {
    rows.replaceChildren(
      ...(draft.sources.length
        ? draft.sources.map(sourceRow)
        : [h("p", { class: "section__hint", text: "Пока ни одного источника." })])
    );
    const used = new Set(draft.sources.map((item) => item.profile_id));
    const available = state.profiles.filter((item) => !used.has(item.id));
    picker.replaceChildren(
      ...available.map((item) => h("option", { value: String(item.id) }, item.name))
    );
    picker.disabled = !available.length;
    addButton.disabled = !available.length;
    pickerField.hidden = !available.length;
    addButton.hidden = !available.length;
  }

  renderSources();

  async function save() {
    await attemptOk(async () => {
      const payload = {
        name: $("input", nameField).value,
        enabled: draft.enabled,
        prefix_names: draft.prefix_names,
        dedupe: draft.dedupe,
        output_format: draft.output_format,
        sources: draft.sources,
      };
      if (draft.id) await api.put(`/api/aggregates/${draft.id}`, payload);
      else await api.post("/api/aggregates", payload);
      closeSheet();
      await loadAggregates();
      toast(draft.id ? "Сборка сохранена" : "Сборка создана");
    });
  }

  openSheet({
    title: existing ? "Настройка сборки" : "Новая сборка",
    body: [
      section("Основное", null, nameField, enabledSwitch),
      section(
        "Источники",
        "Порядок здесь — порядок серверов в итоговой подписке. Формат берётся у первого источника: если панель ответит другим форматом, её серверы будут пропущены, и это попадёт в журнал.",
        rows,
        h("div", { class: "row" }, pickerField, addButton)
      ),
      section(
        "Как собирать",
        null,
        switchRow({
          id: "a-prefix",
          title: "Подписывать серверы источником",
          hint: "К имени каждого сервера добавится подпись — иначе одинаковые названия из разных панелей не отличить.",
          checked: draft.prefix_names,
          onChange: (value) => {
            draft.prefix_names = value;
          },
        }),
        switchRow({
          id: "a-dedupe",
          title: "Убирать дубликаты",
          hint: "Один и тот же адрес и порт попадут в список один раз — полезно, когда панели частично пересекаются.",
          checked: draft.dedupe,
          onChange: (value) => {
            draft.dedupe = value;
          },
        }),
        segmented({
          value: draft.output_format,
          options: [
            { value: "auto", label: "Как у источника" },
            { value: "base64", label: "Base64" },
            { value: "plain", label: "Открытый список" },
          ],
          onChange: (value) => {
            draft.output_format = value;
          },
        })
      ),
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Отмена", onclick: () => closeSheet() }),
      h("button", { class: "btn btn--filled", type: "button", text: "Сохранить", onclick: save }),
    ],
  });
}

/* ------------------------------------------------------- link & QR sheet */

const OS_OPTIONS = [
  { id: "android", label: "Android" },
  { id: "ios", label: "iOS" },
  { id: "windows", label: "Windows" },
  { id: "macos", label: "macOS" },
  { id: "linux", label: "Linux" },
];

/**
 * Import schemes per client. Only well-established ones are listed — a wrong
 * scheme silently does nothing, which is worse than not offering the button.
 */
const CLIENTS = [
  {
    id: "happ",
    label: "Happ",
    os: ["android", "ios", "macos", "windows"],
    build: (url) => `happ://add/${url}`,
  },
  {
    id: "v2raytun",
    label: "v2RayTun",
    os: ["android", "ios", "windows"],
    build: (url) => `v2raytun://import/${url}`,
  },
  {
    id: "streisand",
    label: "Streisand",
    os: ["ios", "macos"],
    build: (url) => `streisand://import/${url}`,
  },
  {
    id: "shadowrocket",
    label: "Shadowrocket",
    os: ["ios"],
    build: (url) => `shadowrocket://add/sub://${btoa(url)}`,
  },
  {
    id: "singbox",
    label: "sing-box",
    os: ["android", "ios", "macos", "windows", "linux"],
    build: (url) => `sing-box://import-remote-profile?url=${encodeURIComponent(url)}`,
  },
  {
    id: "karing",
    label: "Karing",
    os: ["android", "ios", "macos", "windows"],
    build: (url) => `karing://install-config?url=${encodeURIComponent(url)}`,
  },
  {
    id: "hiddify",
    label: "Hiddify",
    os: ["android", "ios", "macos", "windows", "linux"],
    build: (url) => `hiddify://install-config?url=${encodeURIComponent(url)}`,
  },
  {
    id: "clash",
    label: "Clash / Mihomo",
    os: ["android", "windows", "macos", "linux"],
    build: (url) => `clash://install-config?url=${encodeURIComponent(url)}`,
  },
  {
    id: "stash",
    label: "Stash",
    os: ["ios", "macos"],
    build: (url) => `stash://install-config?url=${encodeURIComponent(url)}`,
  },
  {
    id: "loon",
    label: "Loon",
    os: ["ios"],
    build: (url) => `loon://import?sub=${encodeURIComponent(url)}`,
  },
  {
    id: "surge",
    label: "Surge",
    os: ["ios", "macos"],
    build: (url) => `surge:///install-config?url=${encodeURIComponent(url)}`,
  },
  {
    id: "nekobox",
    label: "NekoBox / Throne",
    os: ["android", "windows", "linux"],
    build: (url) => `sn://subscription?url=${encodeURIComponent(url)}`,
  },
];

const OS_KEY = "subremuxer.os";

/** Best guess from the browser, overridable — the phone in your hand may differ. */
function detectOs() {
  const stored = localStorage.getItem(OS_KEY);
  if (stored && OS_OPTIONS.some((item) => item.id === stored)) return stored;

  const platform = navigator.userAgentData?.platform || "";
  const ua = `${navigator.userAgent} ${platform}`.toLowerCase();
  const touchMac = navigator.maxTouchPoints > 1 && /macintosh/.test(ua);

  if (/android/.test(ua)) return "android";
  if (/iphone|ipad|ipod/.test(ua) || touchMac) return "ios";
  if (/windows/.test(ua)) return "windows";
  if (/mac os x|macintosh/.test(ua)) return "macos";
  if (/linux|x11|cros/.test(ua)) return "linux";
  return "android";
}

function clientButtons(url, os) {
  const clients = CLIENTS.filter((client) => client.os.includes(os));
  if (!clients.length) {
    return h("p", { class: "section__hint", text: "Для этой ОС готовых ссылок нет — скопируйте ссылку вручную." });
  }
  return h(
    "div",
    { class: "chipset" },
    clients.map((client) =>
      h("a", {
        class: "chip chip--assist",
        href: client.build(url),
        text: client.label,
        rel: "noopener",
      })
    )
  );
}

/**
 * The link, its QR code and the client buttons. Profiles and aggregates are the
 * same thing from here on — a token to hand out — so `kind` only picks which
 * API path the QR, the token rotation and the reload go through.
 */
function showLinkSheet(item, { kind = "profiles" } = {}) {
  const profile = item;
  const url = profile.subscription_url;
  const reload = kind === "profiles" ? loadProfiles : loadAggregates;
  let os = detectOs();

  const clientsBox = h("div", {}, clientButtons(url, os));
  const osToggle = h(
    "div",
    { class: "chipset chipset--scroll" },
    OS_OPTIONS.map((option) =>
      h("button", {
        class: `chip chip--filter${option.id === os ? " is-selected" : ""}`,
        type: "button",
        text: option.label,
        dataset: { os: option.id },
      })
    )
  );
  osToggle.addEventListener("click", (event) => {
    const button = event.target.closest("[data-os]");
    if (!button) return;
    os = button.dataset.os;
    localStorage.setItem(OS_KEY, os);
    $$("[data-os]", osToggle).forEach((item) => item.classList.toggle("is-selected", item === button));
    clientsBox.replaceChildren(clientButtons(url, os));
  });

  const common = [
    h(
      "div",
      { class: "qr" },
      h(
        "div",
        { class: "qr__frame" },
        h("img", { src: `/api/${kind}/${profile.id}/qr.svg`, alt: "QR-код подписки" })
      ),
      h("div", { class: "qr__url", text: url })
    ),
    section(
      "Открыть в клиенте",
      "Система определена автоматически — при необходимости выберите другую. Ссылка сработает, если приложение установлено на этом устройстве.",
      osToggle,
      clientsBox
    ),
  ];

  if (isViewer()) {
    openSheet({
      title: profile.name,
      body: [
        ...common,
        section(
          "Что это за ссылка",
          "Её нужно добавить в клиент один раз. Дальше клиент обновляет список серверов сам — заново вводить ничего не потребуется.",
          h("button", {
            class: "btn btn--tonal btn--small",
            type: "button",
            text: "Скопировать ссылку",
            onclick: () => copyToClipboard(url),
          })
        ),
      ],
      footer: [
        h("button", {
          class: "btn btn--filled",
          type: "button",
          text: "Готово",
          onclick: () => closeSheet(),
        }),
      ],
    });
    return;
  }

  openSheet({
    title: profile.name,
    body: [
      ...common,
      // An aggregate has nothing of its own to export: everything that defines
      // it lives in the profiles it points at, and those export separately.
      kind === "profiles"
        ? section(
            "Конфигурация профиля",
            "Файл содержит токен подписки и HWID — храните его как секрет.",
            h(
              "div",
              { class: "card__actions" },
              h("button", {
                class: "btn btn--tonal btn--small",
                type: "button",
                text: "Экспорт YAML",
                onclick: () =>
                  downloadExport(
                    `/api/profiles/${profile.id}/export?format=yaml`,
                    `subremuxer-${profile.id}.yaml`
                  ),
              }),
              h("button", {
                class: "btn btn--tonal btn--small",
                type: "button",
                text: "Экспорт JSON",
                onclick: () =>
                  downloadExport(
                    `/api/profiles/${profile.id}/export?format=json`,
                    `subremuxer-${profile.id}.json`
                  ),
              })
            )
          )
        : null,
      section(
        "Безопасность",
        "Смена токена мгновенно ломает старую ссылку — используйте, если она куда-то утекла.",
        h("button", {
          class: "btn btn--outlined btn--danger",
          type: "button",
          text: "Сменить токен",
          onclick: () =>
            attemptOk(async () => {
              await api.post(`/api/${kind}/${profile.id}/rotate-token`);
              closeSheet();
              await reload();
              toast("Токен обновлён");
            }),
        })
      ),
    ].filter(Boolean),
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

/* ----------------------------------------------------------- export/import */

async function downloadExport(path, filename) {
  await attemptOk(async () => {
    const response = await fetch(path, { credentials: "same-origin" });
    if (!response.ok) throw new ApiError("Не удалось выгрузить конфигурацию", response.status);
    const blob = await response.blob();
    const href = URL.createObjectURL(blob);
    const anchor = h("a", { href, download: filename });
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    // Revoking immediately can cancel the download in some browsers.
    setTimeout(() => URL.revokeObjectURL(href), 10000);
    toast(`Файл ${filename} сохранён`);
  });
}

/* ------------------------------------------------------------ code editor */

const escapeHtml = (value) =>
  value.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

function highlightJson(source) {
  return escapeHtml(source).replace(
    /("(?:\\.|[^"\\])*")(\s*:)?|\b(true|false|null)\b|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)/g,
    (match, str, colon, literal, number) => {
      if (str) {
        return colon
          ? `<span class="tok-key">${str}</span>${colon}`
          : `<span class="tok-str">${str}</span>`;
      }
      if (literal) return `<span class="tok-lit">${literal}</span>`;
      return `<span class="tok-num">${number}</span>`;
    }
  );
}

function highlightYaml(source) {
  return escapeHtml(source)
    .split("\n")
    .map((line) => {
      let code = line;
      let comment = "";
      const hash = line.indexOf("#");
      // Only treat # as a comment when it is not inside a quoted scalar.
      if (hash >= 0 && !/["']/.test(line.slice(0, hash))) {
        code = line.slice(0, hash);
        comment = `<span class="tok-com">${line.slice(hash)}</span>`;
      }
      code = code
        .replace(
          /^(\s*(?:-\s+)?)([\w.$@-]+)(:)/,
          (match, indent, key, colon) => `${indent}<span class="tok-key">${key}</span>${colon}`
        )
        .replace(
          /(:\s+)('(?:[^']|'')*'|"(?:\\.|[^"\\])*")/,
          (match, prefix, str) => `${prefix}<span class="tok-str">${str}</span>`
        )
        .replace(
          /(:\s+)(true|false|null|~|-?\d+(?:\.\d+)?)(\s*)$/,
          (match, prefix, literal, tail) => `${prefix}<span class="tok-lit">${literal}</span>${tail}`
        );
      return code + comment;
    })
    .join("\n");
}

/**
 * A dependency-free editor: a transparent textarea over a highlighted <pre>,
 * with a line-number gutter. Enough for editing a config, nothing more.
 */
function codeEditor({ value = "", language = "yaml" } = {}) {
  const lines = h("div", { class: "editor__lines" });
  const gutter = h("div", { class: "editor__gutter" }, lines);
  const code = h("code", {});
  const highlight = h("pre", { class: "editor__highlight", "aria-hidden": "true" }, code);
  const input = h("textarea", {
    class: "editor__input",
    spellcheck: "false",
    autocapitalize: "off",
    autocomplete: "off",
    autocorrect: "off",
    "aria-label": "Конфигурация",
  });
  input.value = value;

  const area = h("div", { class: "editor__area" }, highlight, input);
  const node = h("div", { class: "editor" }, gutter, area);

  let lineCount = -1;
  let currentLanguage = language;

  function renderLines(count) {
    if (count === lineCount) return;
    lineCount = count;
    lines.textContent = Array.from({ length: count }, (unused, index) => index + 1).join("\n");
  }

  function render() {
    const text = input.value;
    code.innerHTML = currentLanguage === "json" ? highlightJson(text) : highlightYaml(text);
    renderLines(text.split("\n").length);
  }

  function syncScroll() {
    highlight.scrollTop = input.scrollTop;
    highlight.scrollLeft = input.scrollLeft;
    lines.style.transform = `translateY(${-input.scrollTop}px)`;
  }

  input.addEventListener("input", render);
  input.addEventListener("scroll", syncScroll, { passive: true });
  input.addEventListener("keydown", (event) => {
    if (event.key !== "Tab") return;
    event.preventDefault();
    const { selectionStart, selectionEnd, value: text } = input;
    input.value = `${text.slice(0, selectionStart)}  ${text.slice(selectionEnd)}`;
    input.selectionStart = input.selectionEnd = selectionStart + 2;
    render();
  });

  render();

  return {
    node,
    getValue: () => input.value,
    setValue(next) {
      input.value = next;
      render();
      input.scrollTop = 0;
      syncScroll();
    },
    setLanguage(next) {
      currentLanguage = next;
      render();
    },
    focus: () => input.focus(),
  };
}

async function showConfigEditor() {
  let format = "yaml";
  const loaded = await attempt(() => api.get(`/api/config?format=${format}`));
  if (!loaded) return;

  const editor = codeEditor({ value: loaded.content, language: format });
  const status = h("div", { class: "editor-status" });
  let dirty = false;
  editor.node.addEventListener("input", () => {
    dirty = true;
    status.replaceChildren();
  });

  function showStatus(kind, title, details = []) {
    status.className = `editor-status editor-status--${kind}`;
    status.replaceChildren(
      h("p", { class: "editor-status__title", text: title }),
      ...details.map((line) => h("p", { class: "editor-status__line", text: line }))
    );
  }

  function describe(summary, applied = false) {
    if (!summary) return [];
    const w = applied
      ? { created: "Создано профилей", updated: "Обновлено профилей", removed: "Удалены профили" }
      : {
          created: "Будет создано профилей",
          updated: "Будет обновлено профилей",
          removed: "Будут удалены профили",
        };
    const lines = [];
    if (summary.profiles_created) lines.push(`${w.created}: ${summary.profiles_created}`);
    if (summary.profiles_updated) lines.push(`${w.updated}: ${summary.profiles_updated}`);
    if (summary.profiles_removed.length) {
      lines.push(`${w.removed}: ${summary.profiles_removed.join(", ")}`);
    }
    if (summary.templates_created) lines.push(`Новых шаблонов: ${summary.templates_created}`);
    if (summary.templates_updated) lines.push(`Обновлённых шаблонов: ${summary.templates_updated}`);
    if (summary.templates_removed.length) {
      const verb = applied ? "Удалены шаблоны" : "Будут удалены шаблоны";
      lines.push(`${verb}: ${summary.templates_removed.join(", ")}`);
    }
    if (summary.aggregates_created) lines.push(`Новых сборок: ${summary.aggregates_created}`);
    if (summary.aggregates_updated) lines.push(`Обновлённых сборок: ${summary.aggregates_updated}`);
    if (summary.aggregates_removed?.length) {
      const verb = applied ? "Удалены сборки" : "Будут удалены сборки";
      lines.push(`${verb}: ${summary.aggregates_removed.join(", ")}`);
    }
    if (summary.settings_changed.length) {
      lines.push(applied ? "Общие настройки применены" : "Общие настройки будут применены");
    }
    if (lines.length) return lines;
    return [applied ? "Изменений не потребовалось" : "Изменений нет — конфигурация совпадает с текущей"];
  }

  async function validate() {
    try {
      const result = await api.post("/api/config/validate", { content: editor.getValue() });
      if (result.ok) showStatus("ok", "Проверка пройдена", describe(result.summary));
      else showStatus("error", "Есть ошибки", result.errors);
      return result;
    } catch (error) {
      showStatus("error", "Есть ошибки", [error.message]);
      return { ok: false };
    }
  }

  async function apply() {
    const check = await validate();
    if (!check.ok) {
      toast("Сначала исправьте ошибки", "error");
      return;
    }
    const removed = (check.summary?.profiles_removed || []).length;
    if (
      removed &&
      !window.confirm(
        `Применить? Профилей будет удалено: ${removed}. ` +
          "Их можно вернуть в течение суток через восстановление."
      )
    ) {
      return;
    }
    try {
      const result = await api.put("/api/config", { content: editor.getValue(), format });
      editor.setValue(result.content);
      dirty = false;
      showStatus("ok", "Конфигурация применена", describe(result.summary, true));
      await Promise.all([loadProfiles(), loadAggregates(), loadTemplates(), loadSettings()]);
      toast("Конфигурация применена");
    } catch (error) {
      showStatus("error", "Не удалось применить", [error.message]);
      toast(error.message, "error");
    }
  }

  async function reload(nextFormat = format) {
    if (dirty && !window.confirm("Перечитать конфигурацию с сервера? Несохранённые правки пропадут.")) {
      return false;
    }
    try {
      const data = await api.get(`/api/config?format=${nextFormat}`);
      format = nextFormat;
      editor.setLanguage(format);
      editor.setValue(data.content);
      dirty = false;
      status.replaceChildren();
      return true;
    } catch (error) {
      toast(error.message, "error");
      return false;
    }
  }

  const formatToggle = segmented({
    value: format,
    options: [
      { value: "yaml", label: "YAML" },
      { value: "json", label: "JSON" },
    ],
    onChange: async (value) => {
      if (value === format) return;
      const ok = await reload(value);
      if (!ok) {
        // Put the selector back where it was.
        $$(".segmented__item", formatToggle).forEach((item) =>
          item.classList.toggle("is-selected", item.dataset.value === format)
        );
      }
    },
  });

  const fileInput = h("input", { type: "file", accept: ".yaml,.yml,.json" });
  fileInput.style.display = "none";
  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    editor.setValue(await file.text());
    dirty = true;
    toast(`Загружен ${file.name}`);
  });

  openSheet({
    title: "Редактор конфигурации",
    wide: true,
    body: [
      h(
        "div",
        { class: "editor-toolbar" },
        formatToggle,
        h("span", { class: "spacer" }),
        h("button", {
          class: "btn btn--text btn--small",
          type: "button",
          text: "Файл",
          onclick: () => fileInput.click(),
        }),
        h("button", {
          class: "btn btn--text btn--small",
          type: "button",
          text: "Перечитать",
          onclick: () => reload(),
        }),
        fileInput
      ),
      editor.node,
      status,
      h("p", {
        class: "section__hint",
        text: "Применение приводит установку в точности к этому документу: чего здесь нет — будет удалено. Профили удаляются мягко и восстановимы сутки.",
      }),
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Проверить", onclick: validate }),
      h("button", { class: "btn btn--filled", type: "button", text: "Применить", onclick: apply }),
    ],
  });
}

function showImportSheet() {
  const fileInput = h("input", { type: "file", accept: ".yaml,.yml,.json,text/yaml,application/json" });
  fileInput.style.display = "none";
  const area = textareaField({ id: "import-content", label: "Содержимое файла (YAML или JSON)", rows: 8 });
  const areaNode = $("textarea", area);

  fileInput.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    areaNode.value = await file.text();
    areaNode.dispatchEvent(new Event("input", { bubbles: true }));
    toast(`Загружен ${file.name}`);
  });

  let keepTokens = false;
  let withSettings = true;

  openSheet({
    title: "Импорт конфигурации",
    body: [
      section(
        "Файл",
        "Принимаются оба формата — и полный слепок, и экспорт одного профиля. Импорт только добавляет: существующие профили не трогаются, конфликтующие имена получают суффикс.",
        h("button", {
          class: "btn btn--tonal",
          type: "button",
          text: "Выбрать файл",
          onclick: () => fileInput.click(),
        }),
        fileInput,
        area
      ),
      section(
        "Параметры",
        null,
        switchRow({
          id: "import-keep-tokens",
          title: "Сохранить токены подписок",
          hint: "Ссылки из файла продолжат работать. Без этого каждому профилю выдаётся новый токен.",
          checked: keepTokens,
          onChange: (value) => {
            keepTokens = value;
          },
        }),
        switchRow({
          id: "import-with-settings",
          title: "Применить общие настройки",
          hint: "HWID по умолчанию, данные устройства и ссылка захвата из файла.",
          checked: withSettings,
          onChange: (value) => {
            withSettings = value;
          },
        })
      ),
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Отмена", onclick: () => closeSheet() }),
      h("button", {
        class: "btn btn--filled",
        type: "button",
        text: "Импортировать",
        onclick: () => {
          if (!areaNode.value.trim()) {
            toast("Выберите файл или вставьте содержимое", "error");
            return;
          }
          attemptOk(async () => {
            const result = await api.post("/api/import", {
              content: areaNode.value,
              keep_tokens: keepTokens,
              with_settings: withSettings,
            });
            closeSheet();
            await Promise.all([loadProfiles(), loadAggregates(), loadTemplates(), loadSettings()]);
            const parts = [];
            if (result.profiles_created) parts.push(`профилей: ${result.profiles_created}`);
            if (result.templates_created) parts.push(`шаблонов: ${result.templates_created}`);
            if (result.aggregates_created) parts.push(`сборок: ${result.aggregates_created}`);
            if (result.settings_applied.length) parts.push("настройки применены");
            toast(parts.length ? `Импортировано — ${parts.join(", ")}` : "Импортировать было нечего");
            if (result.errors.length) {
              setTimeout(() => toast(result.errors[0], "error"), 800);
            }
          });
        },
      }),
    ],
  });
}

/* -------------------------------------------------------- profile editor */

/** The FAB opens this first: start from a template, or from nothing. */
async function showNewProfileChooser() {
  // The chooser can be the first thing opened in a session, so make sure the
  // lists it depends on are there before drawing it.
  await Promise.all([
    state.templates.length ? null : loadTemplates().catch(() => {}),
    state.probe ? null : loadProbe().catch(() => {}),
  ]);

  const cards = state.templates.map((template) =>
    h(
      "button",
      {
        class: "template-card",
        type: "button",
        onclick: () => {
          closeSheet();
          setTimeout(() => showProfileEditor(null, { template }), 260);
        },
      },
      h("span", { class: "template-card__name", text: template.name }),
      template.description ? h("span", { class: "template-card__desc", text: template.description }) : null,
      h("span", { class: "template-card__badges" }, templateBadges(template))
    )
  );

  openSheet({
    title: "Новый профиль",
    body: [
      section(
        "Из шаблона",
        "Шаблон заполнит мимикрию, HWID и фильтр — останется указать название и ссылку.",
        cards.length
          ? h("div", { class: "template-grid" }, cards)
          : h("p", { class: "section__hint", text: "Шаблонов пока нет." })
      ),
      section(
        "С нуля",
        "Пустой профиль с настройками по умолчанию.",
        h("button", {
          class: "btn btn--outlined",
          type: "button",
          text: "Создать с нуля",
          onclick: () => {
            closeSheet();
            setTimeout(() => showProfileEditor(null, { blank: true }), 260);
          },
        })
      ),
    ],
  });
}

function templateBadges(template) {
  const payload = template.payload || {};
  const badges = [];
  const forced = forcedFormatLabel(payload.upstream_ua);
  if (forced) badges.push(h("span", { class: "badge badge--tertiary", text: forced }));
  badges.push(h("span", { class: "badge", text: HWID_MODE_LABELS[payload.hwid_mode] || "HWID" }));
  const conditions = payload.filter?.conditions?.length || 0;
  if (conditions) badges.push(h("span", { class: "badge", text: `${conditions} усл.` }));
  if (payload.device_model) badges.push(h("span", { class: "badge", text: payload.device_model }));
  return badges;
}

function showProfileEditor(existing, { runTest = false, template = null, blank = false } = {}) {
  let base;
  if (existing) base = { ...emptyConfig(), ...existing, filter: { ...emptyConfig().filter, ...existing.filter } };
  else if (template) base = { ...emptyConfig(), ...template.payload };
  else if (blank) base = emptyConfig();
  else base = defaultConfig();

  const draft = JSON.parse(JSON.stringify(base));
  draft.id = existing?.id ?? null;
  draft.enabled = existing ? existing.enabled : true;

  const form = buildConfigForm(draft, { idPrefix: "p" });

  const nameField = field({ id: "p-name", label: "Название", value: existing?.name || template?.name || "" });
  const urlField = field({
    id: "p-url",
    label: "Ссылка на исходную подписку",
    value: existing?.upstream_url || "",
    inputmode: "url",
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

  const testResults = h("div", { class: "section" });
  testResults.hidden = true;

  function collect() {
    return {
      ...form.collect(),
      name: $("input", nameField).value,
      upstream_url: $("input", urlField).value,
      enabled: draft.enabled,
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
      renderTestResult(await api.post("/api/filter/test", { ...payload, profile_id: draft.id }));
    } catch (error) {
      testResults.replaceChildren(
        h("h3", { class: "section__title", text: "Результат теста" }),
        h("p", { class: "field__support field__support--error", text: error.message })
      );
    }
  }

  function renderTestResult(result) {
    const children = [
      h("h3", { class: "section__title", text: "Результат теста" }),
      h(
        "div",
        { class: "test-summary" },
        h("span", { class: "badge badge--success", text: `оставлено ${result.kept} из ${result.total}` }),
        result.format_label ? h("span", { class: "badge badge--primary", text: result.format_label }) : null,
        h("span", { class: "badge", text: `${result.upstream_ms} мс` }),
        result.hwid_sent ? h("span", { class: "badge", text: `HWID: ${result.hwid_sent}` }) : null
      ),
    ];
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
    await attemptOk(async () => {
      const payload = collect();
      if (draft.id) await api.put(`/api/profiles/${draft.id}`, payload);
      else await api.post("/api/profiles", payload);
      closeSheet();
      await loadProfiles();
      toast(draft.id ? "Профиль сохранён" : "Профиль создан");
    });
  }

  openSheet({
    title: existing ? "Настройка профиля" : template ? `Новый профиль · ${template.name}` : "Новый профиль",
    body: [section("Основное", null, nameField, urlField, enabledSwitch), form.sections, testResults],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Тест", onclick: runFilterTest }),
      h("button", { class: "btn btn--filled", type: "button", text: "Сохранить", onclick: save }),
    ],
  });

  if (runTest) setTimeout(runFilterTest, 300);
}

/* ------------------------------------------------------- template editor */

function showTemplateEditor(existing) {
  const draft = JSON.parse(JSON.stringify(existing ? { ...emptyConfig(), ...existing.payload } : defaultConfig()));
  const form = buildConfigForm(draft, { idPrefix: "t" });

  const nameField = field({ id: "t-name", label: "Название шаблона", value: existing?.name || "" });
  const descField = textareaField({
    id: "t-desc",
    label: "Описание",
    value: existing?.description || "",
  });

  async function save() {
    const payload = {
      name: $("input", nameField).value,
      description: $("textarea", descField).value,
      payload: form.collect(),
    };
    await attemptOk(async () => {
      if (existing) await api.put(`/api/templates/${existing.id}`, payload);
      else await api.post("/api/templates", payload);
      closeSheet();
      await loadTemplates();
      toast(existing ? "Шаблон сохранён" : "Шаблон создан");
    });
  }

  openSheet({
    title: existing ? "Настройка шаблона" : "Новый шаблон",
    body: [
      section(
        "Описание",
        "Шаблон не хранит ссылку на подписку и название профиля — только то, как с панелью разговаривать и что оставлять.",
        nameField,
        descField
      ),
      form.sections,
    ],
    footer: [
      h("button", { class: "btn btn--outlined", type: "button", text: "Отмена", onclick: () => closeSheet() }),
      h("button", { class: "btn btn--filled", type: "button", text: "Сохранить", onclick: save }),
    ],
  });
}

function renderTemplates() {
  const list = $("#templates-list");
  if (!state.templates.length) {
    list.replaceChildren(h("p", { class: "section__hint", text: "Шаблонов нет." }));
    return;
  }
  list.replaceChildren(
    ...state.templates.map((template) =>
      h(
        "div",
        { class: "template-row" },
        h(
          "div",
          { class: "template-row__text" },
          h("span", { class: "template-row__name", text: template.name }),
          template.description
            ? h("span", { class: "template-row__desc", text: template.description })
            : null,
          h("span", { class: "template-row__badges" }, templateBadges(template))
        ),
        iconButton({ icon: icons.edit, label: "Редактировать шаблон", onclick: () => showTemplateEditor(template) }),
        iconButton({ icon: icons.trash, label: "Удалить шаблон", onclick: () => deleteTemplate(template) })
      )
    )
  );
}

async function deleteTemplate(template) {
  const snapshot = { ...template };
  if (!(await attemptOk(() => api.del(`/api/templates/${template.id}`)))) return;
  state.templates = state.templates.filter((item) => item.id !== template.id);
  renderTemplates();
  undoToast(`Шаблон «${template.name}» удалён`, async () => {
    await api.post("/api/templates", {
      name: snapshot.name,
      description: snapshot.description,
      payload: snapshot.payload,
    });
    await loadTemplates();
    toast("Шаблон восстановлен");
  });
}

async function loadTemplates() {
  state.templates = await api.get("/api/templates");
  renderTemplates();
}

/* ------------------------------------------------------------ probe page */

function captureCard(capture) {
  const rows = [
    ["HWID", capture.hwid || "не прислан"],
    ["Устройство", [capture.device_os, capture.device_ver, capture.device_model].filter(Boolean).join(" ") || "—"],
    ["User-Agent", capture.user_agent || "—"],
    ["Адрес", capture.client_ip || "—"],
    ["Запросов", `${capture.seen_count}, последний ${formatTime(capture.last_ts)}`],
  ];

  const valid = capture.hwid && HWID_RE.test(capture.hwid);

  return h(
    "article",
    { class: "capture" },
    h(
      "div",
      { class: "capture__head" },
      h(
        "div",
        { class: "capture__title" },
        h("h3", {
          class: "capture__name",
          text: capture.device_model || capture.user_agent || "Неизвестное устройство",
        }),
        h("span", {
          class: "capture__meta",
          text: [capture.device_os, capture.device_ver].filter(Boolean).join(" ") || "—",
        })
      ),
      capture.hwid
        ? h("span", { class: `badge ${valid ? "badge--success" : "badge--error"}`, text: valid ? "HWID есть" : "HWID неформатный" })
        : h("span", { class: "badge badge--error", text: "без HWID" })
    ),
    h("dl", { class: "kv" }, rows.map(([key, value]) => [h("dt", { text: key }), h("dd", { text: value })])),
    h(
      "div",
      { class: "capture__actions" },
      capture.hwid
        ? h("button", {
            class: "btn btn--tonal btn--small",
            type: "button",
            text: "Скопировать HWID",
            onclick: () => copyToClipboard(capture.hwid, "HWID скопирован"),
          })
        : null,
      h("button", {
        class: "btn btn--outlined btn--small",
        type: "button",
        text: "Создать профиль",
        onclick: () => {
          const config = defaultConfig();
          config.hwid = capture.hwid || "";
          config.device_os = capture.device_os || "";
          config.device_ver = capture.device_ver || "";
          config.device_model = capture.device_model || "";
          showProfileEditor(null, { template: { name: "", payload: config } });
        },
      }),
      h("span", { class: "spacer" }),
      iconButton({
        icon: icons.trash,
        label: "Удалить захват",
        onclick: () =>
          deleteCaptures([capture], {
            message: `Захват «${capture.device_model || capture.user_agent || "устройство"}» удалён`,
            request: () => api.del(`/api/probe/captures?capture_id=${capture.id}`),
          }),
      })
    )
  );
}

/**
 * Same undo pattern as profiles. A capture is only the headers a client sent, so
 * undo recreates it from the copy the page still holds instead of the server
 * having to keep a tombstone around.
 */
async function deleteCaptures(rows, { message, request }) {
  const snapshot = JSON.parse(JSON.stringify(rows));
  if (!(await attemptOk(request))) return;

  const removed = new Set(snapshot.map((item) => item.id));
  if (state.probe) {
    state.probe.captures = state.probe.captures.filter((item) => !removed.has(item.id));
    renderProbe();
  }

  undoToast(message, async () => {
    const result = await api.post("/api/probe/captures/restore", { captures: snapshot });
    await loadProbe();
    toast(
      result.restored === snapshot.length
        ? "Восстановлено"
        : `Восстановлено ${result.restored} из ${snapshot.length} — остальные успели появиться заново`
    );
  });
}

function renderProbe() {
  const data = state.probe;
  if (!data) return;
  $("#probe-url").textContent = data.url;
  const qr = $("#probe-qr");
  if (qr.dataset.token !== data.token) {
    qr.dataset.token = data.token;
    qr.src = `/api/probe/qr.svg?v=${encodeURIComponent(data.token)}`;
  }

  const list = $("#probe-list");
  if (!data.captures.length) {
    list.replaceChildren(
      h(
        "div",
        { class: "empty" },
        h("p", { class: "empty__title", text: "Пока никто не подключался" }),
        h("p", {
          text: "Добавьте ссылку выше в клиент как подписку — устройство появится здесь через пару секунд.",
        })
      )
    );
    return;
  }
  list.replaceChildren(...data.captures.map(captureCard));
}

async function loadProbe() {
  state.probe = await api.get("/api/probe");
  renderProbe();
}

function startProbePolling() {
  stopProbePolling();
  // Cheap: one small request while the page is actually being looked at.
  state.probeTimer = setInterval(() => {
    if (state.route !== "probe" || document.visibilityState !== "visible" || sheet.open) return;
    loadProbe().catch(() => {});
  }, 5000);
}

function stopProbePolling() {
  if (state.probeTimer) clearInterval(state.probeTimer);
  state.probeTimer = null;
}

/* ------------------------------------------------------------------- logs */

function formatTime(ts) {
  return new Date(ts * 1000).toLocaleString("ru-RU", {
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
      // An aggregate's summary row talks to no panel of its own — the HWID it
      // sent lives in each source's own entry, so showing empty rows here would
      // read as "nothing was sent" rather than "ask the source".
      ...(entry.hwid_action
        ? [
            ["HWID клиента", entry.hwid_in || "не прислан"],
            ["HWID отправлен", `${entry.hwid_sent || "—"} (${entry.hwid_action || "—"})`],
          ]
        : []),
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
  // Skeletons only before anything has ever been drawn. Swapping a rendered list
  // for placeholders on every filter toggle is what made the panel flicker.
  if (!append && !state.logsLoaded) {
    list.replaceChildren(
      h("div", { class: "skeleton skeleton--log" }),
      h("div", { class: "skeleton skeleton--log" }),
      h("div", { class: "skeleton skeleton--log" })
    );
  }
  list.setAttribute("aria-busy", "true");

  const params = new URLSearchParams({ limit: "30" });
  if (state.logsOnlyErrors) params.set("only_errors", "true");
  if (append && state.logsCursor) params.set("before_id", String(state.logsCursor));

  try {
    const data = await api.get(`/api/logs?${params}`);
    state.logs = append ? state.logs.concat(data.entries) : data.entries;
    state.logsCursor = data.next_before_id;
    state.logsLoaded = true;
    renderLogs();
    $("#logs-more").hidden = data.entries.length < 30;
  } finally {
    list.removeAttribute("aria-busy");
  }
}

/* --------------------------------------------------------------- settings */

async function loadSettings() {
  state.settings = await api.get("/api/settings");
  $("#default-hwid").value = state.settings.default_hwid || "";
  $("#default-device-os").value = state.settings.default_device_os || "";
  $("#default-device-ver").value = state.settings.default_device_ver || "";
  $("#default-device-model").value = state.settings.default_device_model || "";
  refreshDefaultHwidHint();
  await loadTemplates();
}

function refreshDefaultHwidHint() {
  const value = $("#default-hwid").value.trim();
  const hint = $("#default-hwid-hint");
  if (!value) {
    hint.className = "field__support";
    hint.textContent = "10–64 символа: латиница, цифры, «=» и «-».";
    return;
  }
  const valid = HWID_RE.test(value);
  hint.className = `field__support${valid ? "" : " field__support--error"}`;
  hint.textContent = valid
    ? "Формат подходит под проверку панели."
    : "Панель проигнорирует такой HWID: нужно 10–64 символа из латиницы, цифр, «=» и «-».";
}

async function saveSettings() {
  await attemptOk(async () => {
    state.settings = await api.put("/api/settings", {
      default_hwid: $("#default-hwid").value.trim(),
      default_device_os: $("#default-device-os").value.trim(),
      default_device_ver: $("#default-device-ver").value.trim(),
      default_device_model: $("#default-device-model").value.trim(),
    });
    toast("Настройки сохранены");
    refreshDefaultHwidHint();
  });
}

/* ------------------------------------------------------------------ help */

const DEMO_NAMES = [
  "🇳🇱 NL-1 LTE",
  "🇷🇺 RU-1 LTE",
  "🇷🇺 RU-2 Home",
  "🇩🇪 DE-1 LTE",
  "🇸🇪 SE-1",
  "Трафик: осталось 42 ГБ",
].join("\n");

/** A live playground wired to the same dry-run endpoint the profile editor uses. */
function filterDemo() {
  const names = textareaField({ id: "demo-names", label: "Имена серверов", value: DEMO_NAMES, rows: 6 });
  const contains = field({ id: "demo-contains", label: "содержит", value: "LTE" });
  const notContains = field({ id: "demo-not-contains", label: "не содержит", value: "RU" });
  const regexBox = h("div", { class: "regex-preview" });
  const results = h("div", { class: "test-list" });

  const inputs = [$("textarea", names), $("input", contains), $("input", notContains)];
  let timer = null;

  async function run() {
    const conditions = [];
    if (inputs[1].value.trim()) conditions.push({ op: "contains", value: inputs[1].value.trim() });
    if (inputs[2].value.trim()) {
      conditions.push({ op: "not_contains", value: inputs[2].value.trim() });
    }
    const list = inputs[0].value.split("\n").map((line) => line.trim()).filter(Boolean);

    try {
      const data = await api.post("/api/filter/dry-run", {
        filter: { mode: "builder", match: "all", conditions },
        names: list,
      });
      regexBox.className = data.regex ? "regex-preview" : "regex-preview regex-preview--empty";
      regexBox.textContent = data.regex || "Условий нет — пройдут все серверы.";
      results.replaceChildren(
        ...data.results.map((item) =>
          h(
            "div",
            { class: `node${item.kept ? "" : " node--dropped"}` },
            h("span", { class: "node__mark", text: item.kept ? "✓" : "✕" }),
            h(
              "span",
              { class: "node__text" },
              h("span", { class: "node__name", text: item.name }),
              item.kept ? null : h("span", { class: "node__reason", text: item.detail || item.reason })
            )
          )
        )
      );
    } catch (error) {
      regexBox.className = "regex-preview regex-preview--empty";
      regexBox.textContent = error.message;
    }
  }

  inputs.forEach((input) =>
    input.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(run, 350);
    })
  );
  run();

  return h(
    "div",
    { class: "demo" },
    h("p", { class: "demo__title", text: "Попробуйте прямо здесь" }),
    names,
    h("div", { class: "grid grid--2" }, contains, notContains),
    regexBox,
    results
  );
}

function helpBlock(block) {
  if (block.p) return h("p", { class: "help__p", text: block.p });
  if (block.note) return h("p", { class: "help__note", text: block.note });
  if (block.warn) return h("p", { class: "help__warn", text: block.warn });
  if (block.demo === "filter") return filterDemo();

  if (block.steps) {
    return h(
      "ol",
      { class: "help__steps" },
      block.steps.map((step) => h("li", { text: step }))
    );
  }
  if (block.list) {
    return h(
      "ul",
      { class: "help__list" },
      block.list.map((item) => h("li", { text: item }))
    );
  }
  if (block.code) {
    return h(
      "figure",
      { class: "help__figure" },
      h("pre", { class: "help__code" }, h("code", { text: block.code })),
      block.caption ? h("figcaption", { text: block.caption }) : null
    );
  }
  if (block.table) {
    return h(
      "div",
      { class: "help__tablewrap" },
      h(
        "table",
        { class: "help__table" },
        h("thead", {}, h("tr", {}, block.table.head.map((cell) => h("th", { text: cell })))),
        h(
          "tbody",
          {},
          block.table.rows.map((row) => h("tr", {}, row.map((cell) => h("td", { text: cell }))))
        )
      )
    );
  }
  return null;
}

function showHelp(sectionId = null) {
  // A viewer gets its own text rather than the administrator's with parts cut
  // out: half an explanation of a setting they cannot see is worse than none.
  const sections = (isViewer() ? window.HELP_SECTIONS_VIEWER : window.HELP_SECTIONS) || [];
  const bodies = sections.map((section) =>
    h(
      "section",
      { class: "help__section", id: `help-${section.id}` },
      h("h3", { class: "help__title", text: section.title }),
      ...section.blocks.map(helpBlock).filter(Boolean)
    )
  );

  const toc = h(
    "div",
    { class: "chipset chipset--scroll help__toc" },
    sections.map((section) =>
      h("button", {
        class: "chip",
        type: "button",
        text: section.title,
        onclick: () => {
          const target = $(`#help-${section.id}`);
          target?.scrollIntoView({ behavior: "smooth", block: "start" });
        },
      })
    )
  );

  openSheet({
    title: "Справка",
    wide: true,
    body: [toc, ...bodies],
    footer: [
      h("button", { class: "btn btn--filled", type: "button", text: "Понятно", onclick: () => closeSheet() }),
    ],
  });

  if (sectionId) {
    setTimeout(() => $(`#help-${sectionId}`)?.scrollIntoView({ block: "start" }), 60);
  }
}

/* ----------------------------------------------------------------- router */

const PAGES = {
  profiles: { title: "Профили", subtitle: "Прокси-подписки, которые отдаёт это приложение" },
  // The same page, described for someone who only consumes what is on it.
  profilesViewer: { title: "Подписки", subtitle: "Ссылки для ваших клиентов" },
  aggregates: { title: "Сборки", subtitle: "Несколько профилей под одной ссылкой" },
  aggregatesViewer: { title: "Сборки", subtitle: "Объединённые ссылки для ваших клиентов" },
  probe: { title: "Захват", subtitle: "Узнать HWID и данные устройства прямо из клиента" },
  logs: { title: "Логи", subtitle: "Кто, когда и что получил" },
  settings: { title: "Настройки", subtitle: "Значения по умолчанию, шаблоны и оформление" },
};

//: Pages that hold links to hand out — the only ones a viewer may open.
const VIEWER_ROUTES = ["profiles", "aggregates"];

//: What the floating button creates, per page. Absent means no button.
const FAB_LABELS = { profiles: "Профиль", aggregates: "Сборка" };

async function navigate(route, { silent = false } = {}) {
  if (!PAGES[route]) route = "profiles";
  // A stale bookmark to #/logs should land a viewer somewhere useful rather
  // than on a 403.
  if (isViewer() && !VIEWER_ROUTES.includes(route)) route = "profiles";
  state.route = route;

  $$(".page").forEach((page) => {
    page.hidden = page.dataset.page !== route;
  });
  $$("[data-route]").forEach((item) => item.classList.toggle("is-active", item.dataset.route === route));
  const page = PAGES[isViewer() && VIEWER_ROUTES.includes(route) ? `${route}Viewer` : route];
  $("#page-title").textContent = page.title;
  $("#page-subtitle").textContent = page.subtitle;
  const fab = $("#fab-add");
  fab.classList.toggle("is-hidden", !FAB_LABELS[route]);
  if (FAB_LABELS[route]) {
    $(".fab__label", fab).textContent = FAB_LABELS[route];
    fab.setAttribute("aria-label", `Добавить: ${FAB_LABELS[route].toLowerCase()}`);
  }
  if (!silent) location.hash = `#/${route}`;

  try {
    if (route === "profiles") await loadProfiles();
    else if (route === "aggregates") await loadAggregates();
    else if (route === "probe") await loadProbe();
    else if (route === "logs") await loadLogs();
    else if (route === "settings") await loadSettings();
  } catch (error) {
    if (error.status !== 401) toast(error.message, "error");
  }
}

/* -------------------------------------------------------------------- pwa */

let installPrompt = null;

function initPwa() {
  if ("serviceWorker" in navigator) {
    // A worker only registers over HTTPS or on localhost; elsewhere the app just
    // runs without offline support instead of throwing.
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event;
    $("#install-card").hidden = false;
  });

  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    $("#install-card").hidden = true;
    toast("Приложение установлено");
  });

  $("#install-app").addEventListener("click", async () => {
    if (!installPrompt) {
      toast("Установка недоступна в этом браузере", "error");
      return;
    }
    installPrompt.prompt();
    const { outcome } = await installPrompt.userChoice;
    installPrompt = null;
    $("#install-card").hidden = true;
    if (outcome !== "accepted") toast("Установка отменена");
  });
}

/* -------------------------------------------------------------- auth flow */

/**
 * Remembers that automatic sign-in must not fire in this tab.
 *
 * Set by the `?disableAutoLogin=true` escape hatch — the same spelling Grafana
 * uses — and by signing out, which would otherwise bounce straight back to the
 * provider and sign the same person in again.
 */
const NO_AUTO_LOGIN_KEY = "subremuxer.noAutoLogin";

function suppressAutoLogin() {
  try {
    sessionStorage.setItem(NO_AUTO_LOGIN_KEY, "1");
  } catch {
    /* private mode: the worst case is one extra redirect */
  }
}

function autoLoginSuppressed() {
  const asked = new URLSearchParams(location.search).get("disableAutoLogin");
  if (asked && asked !== "false") {
    suppressAutoLogin();
    return true;
  }
  try {
    return sessionStorage.getItem(NO_AUTO_LOGIN_KEY) === "1";
  } catch {
    return false;
  }
}

function renderLoginMethods() {
  const { password, oidc } = state.methods;
  $("#login-oidc").hidden = !oidc;
  $("#login-oidc-label").textContent = `Войти через ${state.oidcName || "OIDC"}`;
  $("#login-form").hidden = !password;
  $("#login-divider").hidden = !(oidc && password);
  // Whichever method is offered alone is the primary one; when both are, the
  // provider leads and the password is the fallback it is meant to be.
  $("#login-submit").className = oidc
    ? "btn btn--tonal btn--full"
    : "btn btn--filled btn--full";

  const note = $("#login-note");
  if (!password && !oidc) {
    note.textContent =
      "Ни один способ входа не настроен. Проверьте переменные окружения приложения.";
    note.hidden = false;
  } else if (!password) {
    // Phrased about the installation rather than about "the administrator",
    // who is quite often the person reading it.
    note.textContent = "На этой установке вход возможен только через провайдера.";
    note.hidden = false;
  } else {
    note.hidden = true;
  }
}

function showLogin() {
  stopProbePolling();
  $("#shell").hidden = true;
  $("#login").hidden = false;
  document.body.classList.remove("is-locked");
  renderLoginMethods();
  if (state.methods.password) setTimeout(() => $("#login-password")?.focus(), 100);
}

function applyRoleToChrome() {
  const viewer = isViewer();
  document.body.classList.toggle("is-viewer", viewer);
  // Rail and bottom bar are separate elements with the same data-route values.
  $$("[data-route]").forEach((item) => {
    if (!VIEWER_ROUTES.includes(item.dataset.route)) item.hidden = viewer;
  });
  $("#fab-add").hidden = viewer;
  const account = $("#topbar-account");
  if (state.user) {
    account.textContent = state.user;
    account.title = viewer ? "Вы вошли как читатель" : "Вы вошли как администратор";
    account.hidden = false;
  } else {
    account.hidden = true;
  }
}

async function showApp() {
  $("#login").hidden = true;
  $("#shell").hidden = false;
  // In demo mode there is no session to end, so the exit affordances only get in
  // the way — but the banner has to stay visible the whole time.
  $("#demo-banner").hidden = !state.demo;
  $("#topbar-logout").hidden = state.demo;
  $("#settings-logout").closest(".card").hidden = state.demo;
  applyRoleToChrome();

  if (!isViewer()) {
    state.meta = await api.get("/api/meta");
    state.settings = await api.get("/api/settings");
    // Templates and captures feed pickers on other pages, so fetch them up front.
    await Promise.all([loadTemplates().catch(() => {}), loadProbe().catch(() => {})]);
    startProbePolling();
  }
  const route = (location.hash.replace("#/", "") || "profiles").split("?")[0];
  await navigate(route, { silent: true });
}

async function boot() {
  applyTheme(localStorage.getItem(THEME_KEY) || "auto");
  initSheetDrag();
  initPwa();

  $("#login-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = $("#login-submit");
    const errorNode = $("#login-error");
    errorNode.hidden = true;
    button.disabled = true;
    try {
      await api.post("/api/auth/login", { password: $("#login-password").value });
      $("#login-password").value = "";
      applyAuthState(await api.get("/api/auth/me"));
      await showApp();
    } catch (error) {
      errorNode.textContent = error.message;
      errorNode.hidden = false;
    } finally {
      button.disabled = false;
    }
  });

  $("#login-oidc").addEventListener("click", () => startOidcLogin());

  $$("[data-route]").forEach((item) => {
    item.addEventListener("click", () => navigate(item.dataset.route));
  });

  $("#fab-add").addEventListener("click", () =>
    state.route === "aggregates" ? showAggregateEditor(null) : showNewProfileChooser()
  );
  $("#rail-theme").addEventListener("click", cycleTheme);
  $("#topbar-theme").addEventListener("click", cycleTheme);
  $("#sheet-close").addEventListener("click", () => closeSheet());
  $("#scrim").addEventListener("click", () => closeSheet());
  $("#settings-save").addEventListener("click", saveSettings);
  $("#default-hwid").addEventListener("input", refreshDefaultHwidHint);
  $("#logs-more").addEventListener("click", () => loadLogs({ append: true }));
  $("#template-new").addEventListener("click", () => showTemplateEditor(null));
  $("#config-editor").addEventListener("click", () => showConfigEditor());
  $("#topbar-help").addEventListener("click", () => showHelp());
  $("#settings-help").addEventListener("click", () => showHelp());
  $("#import-open").addEventListener("click", () => showImportSheet());
  $("#export-yaml").addEventListener("click", () =>
    downloadExport("/api/export?format=yaml", "subremuxer-config.yaml")
  );
  $("#export-json").addEventListener("click", () =>
    downloadExport("/api/export?format=json", "subremuxer-config.json")
  );

  $("#templates-restore").addEventListener("click", () =>
    attemptOk(async () => {
      await api.post("/api/templates/restore-builtins");
      await loadTemplates();
      toast("Встроенные шаблоны восстановлены");
    })
  );

  $("#probe-copy").addEventListener("click", () => copyToClipboard(state.probe?.url || ""));

  $("#probe-rotate").addEventListener("click", () => {
    if (!window.confirm("Сменить ссылку для захвата? Старая перестанет работать.")) return;
    attemptOk(async () => {
      await api.post("/api/probe/rotate");
      await loadProbe();
      toast("Ссылка обновлена");
    });
  });

  $("#probe-clear").addEventListener("click", () => {
    const captures = state.probe?.captures || [];
    if (!captures.length) {
      toast("Список и так пуст");
      return;
    }
    deleteCaptures(captures, {
      message: `Удалено захватов: ${captures.length}`,
      request: () => api.del("/api/probe/captures"),
    });
  });

  $("#theme-segmented").addEventListener("click", (event) => {
    const button = event.target.closest("[data-theme]");
    if (button) applyTheme(button.dataset.theme);
  });

  $("#log-filters").addEventListener("click", (event) => {
    const button = event.target.closest("[data-log-filter]");
    if (!button) return;
    const onlyErrors = button.dataset.logFilter === "errors";
    if (onlyErrors === state.logsOnlyErrors) return;
    $$("[data-log-filter]").forEach((item) => item.classList.toggle("is-selected", item === button));
    state.logsOnlyErrors = onlyErrors;
    loadLogs();
  });

  $("#logs-clear").addEventListener("click", () => {
    if (!window.confirm("Удалить все записи журнала?")) return;
    attemptOk(async () => {
      await api.del("/api/logs");
      state.logNodeCache.clear();
      toast("Журнал очищен");
      await loadLogs();
    });
  });

  for (const id of ["topbar-logout", "settings-logout"]) {
    $(`#${id}`).addEventListener("click", async () => {
      await api.post("/api/auth/logout").catch(() => {});
      state.authenticated = false;
      // Without this, automatic sign-in would send the browser back to the
      // provider and return with the very session that was just ended.
      suppressAutoLogin();
      showLogin();
    });
  }

  document.addEventListener("keydown", (event) => {
    // CloseWatcher already answers Escape itself; handling it twice would close
    // the sheet and then immediately swallow the next one.
    if (event.key === "Escape" && sheet.open && !sheet.watcher) closeSheet();
  });

  window.addEventListener("popstate", () => {
    if (sheet.open && sheet.historyPushed) {
      closeSheet({ fromHistory: true });
      return;
    }
    const route = (location.hash.replace("#/", "") || "profiles").split("?")[0];
    navigate(route, { silent: true });
  });

  const topbar = $(".topbar");
  window.addEventListener(
    "scroll",
    () => topbar?.classList.toggle("is-stuck", window.scrollY > 4),
    { passive: true }
  );

  try {
    const me = await api.get("/api/auth/me");
    applyAuthState(me);
    if (me.authenticated) {
      await showApp();
    } else if (me.auto_login && me.methods.oidc && !autoLoginSuppressed()) {
      // Nothing to choose between: go straight to the provider.
      startOidcLogin();
    } else {
      showLogin();
    }
  } catch {
    showLogin();
  }
}

function applyAuthState(me) {
  state.authenticated = Boolean(me.authenticated);
  state.demo = Boolean(me.demo);
  state.role = me.role || "viewer";
  state.user = me.user || null;
  state.methods = me.methods || { password: true, oidc: false };
  state.oidcName = me.oidc_name || "OIDC";
  state.autoLogin = Boolean(me.auto_login);
}

function startOidcLogin() {
  const route = location.hash.replace("#/", "").split("?")[0];
  const next = route ? `?next=${encodeURIComponent(`/#/${route}`)}` : "";
  location.href = `/auth/oidc/login${next}`;
}

document.addEventListener("DOMContentLoaded", boot);
