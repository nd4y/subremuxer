import { beforeEach } from "vitest";

// jsdom ships no matchMedia at all, and the sheet asks it whether there is room
// for a desktop layout. Answering "no" is the honest reading of a headless DOM
// with no viewport, and it keeps the code under test unchanged.
if (typeof window.matchMedia !== "function") {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener() {},
    removeEventListener() {},
    addListener() {},
    removeListener() {},
    dispatchEvent: () => false,
  });
}

// localStorage/sessionStorage are real jsdom globals shared by every test in a
// file — without this, an OS/theme preference or an auto-login suppression
// flag set by one test silently leaks into the next.
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});
