import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const STATIC_DIR = path.join(here, "../../app/static");
const APP_JS_SOURCE = fs.readFileSync(path.join(STATIC_DIR, "app.js"), "utf8");
const INDEX_HTML = fs.readFileSync(path.join(STATIC_DIR, "index.html"), "utf8");
const HELP_JS_SOURCE = fs.readFileSync(path.join(STATIC_DIR, "help.js"), "utf8");

/**
 * app.js is a classic (non-module) script loaded via `<script src>` — that is
 * a deliberate choice (see its header comment), not an oversight, so tests must
 * not force a build step or an export statement onto it. Every top-level
 * `function`/`class`/`const`/`let` name is picked up automatically instead, the
 * same way a browser would expose a top-level function declaration on `window`
 * — so a new helper becomes testable the moment it is written, with nothing to
 * keep in sync by hand.
 */
const TOP_LEVEL_NAME = /^(?:async\s+function|function|class)\s+([A-Za-z_$][\w$]*)|^(?:const|let)\s+([A-Za-z_$][\w$]*)/gm;

function topLevelNames(source) {
  const names = new Set();
  for (const match of source.matchAll(TOP_LEVEL_NAME)) {
    names.add(match[1] || match[2]);
  }
  return [...names];
}

const EXPORT_NAMES = topLevelNames(APP_JS_SOURCE);

/**
 * Evaluate app.js in its own function scope and hand back every top-level
 * binding it declared. A fresh call re-runs the whole script, so mutable
 * module state (`state`, `sheet`, `snackbarTimer`, …) never leaks between
 * tests — call this in `beforeEach`, not once at module load.
 */
export function loadApp() {
  const factory = new Function(`${APP_JS_SOURCE}\nreturn { ${EXPORT_NAMES.join(", ")} };`);
  return factory();
}

/**
 * help.js is text rather than logic: it only assigns `window.HELP_SECTIONS` and
 * `window.HELP_SECTIONS_VIEWER`. Running it makes the real help content
 * available to tests instead of a fixture that could drift from it.
 */
export function loadHelp() {
  new Function(HELP_JS_SOURCE)();
}

/** The real app shell markup, for functions that look up elements by id. */
export function mountShell() {
  const body = INDEX_HTML.match(/<body[^>]*>([\s\S]*)<\/body>/i)?.[1] ?? "";
  document.body.innerHTML = body;
}
