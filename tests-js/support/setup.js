import { beforeEach } from "vitest";

// localStorage/sessionStorage are real jsdom globals shared by every test in a
// file — without this, an OS/theme preference or an auto-login suppression
// flag set by one test silently leaks into the next.
beforeEach(() => {
  localStorage.clear();
  sessionStorage.clear();
  window.history.replaceState({}, "", "/");
});
