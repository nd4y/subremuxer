/**
 * The login screen and the role-based chrome. This is exactly the area that
 * changed when the master password was switched off in production — a wrong
 * `methods`/`autoLogin` combination here means either a stranded admin or a
 * password box nobody was supposed to see.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { loadApp, mountShell } from "./support/loadApp.js";

let app;

beforeEach(() => {
  mountShell();
  app = loadApp();
});

describe("renderLoginMethods", () => {
  it("shows only the password form when OIDC is not configured", () => {
    app.state.methods = { password: true, oidc: false };
    app.renderLoginMethods();
    expect(document.getElementById("login-form").hidden).toBe(false);
    expect(document.getElementById("login-oidc").hidden).toBe(true);
    expect(document.getElementById("login-divider").hidden).toBe(true);
    expect(document.getElementById("login-note").hidden).toBe(true);
  });

  it("shows only the provider button and a note when the password is disabled", () => {
    app.state.methods = { password: false, oidc: true };
    app.state.oidcName = "Keycloak";
    app.renderLoginMethods();
    expect(document.getElementById("login-form").hidden).toBe(true);
    expect(document.getElementById("login-oidc").hidden).toBe(false);
    expect(document.getElementById("login-oidc-label").textContent).toBe("Войти через Keycloak");
    const note = document.getElementById("login-note");
    expect(note.hidden).toBe(false);
    expect(note.textContent).toBe("На этой установке вход возможен только через провайдера.");
  });

  it("offers both, separated by a divider, provider button styled as primary", () => {
    app.state.methods = { password: true, oidc: true };
    app.renderLoginMethods();
    expect(document.getElementById("login-form").hidden).toBe(false);
    expect(document.getElementById("login-oidc").hidden).toBe(false);
    expect(document.getElementById("login-divider").hidden).toBe(false);
    expect(document.getElementById("login-submit").className).toContain("btn--tonal");
  });

  it("warns when neither method is configured at all", () => {
    app.state.methods = { password: false, oidc: false };
    app.renderLoginMethods();
    const note = document.getElementById("login-note");
    expect(note.hidden).toBe(false);
    expect(note.textContent).toMatch(/Ни один способ входа не настроен/);
  });
});

describe("showLogin", () => {
  it("hides the app shell and reveals the login screen", () => {
    document.getElementById("shell").hidden = false;
    document.getElementById("login").hidden = true;
    app.state.methods = { password: true, oidc: false };
    app.showLogin();
    expect(document.getElementById("shell").hidden).toBe(true);
    expect(document.getElementById("login").hidden).toBe(false);
  });
});

describe("applyRoleToChrome", () => {
  it("an admin sees every nav item and every route", () => {
    app.state.role = "admin";
    app.state.user = "Админ";
    app.applyRoleToChrome();
    expect(document.body.classList.contains("is-viewer")).toBe(false);
    const nonProfileRoutes = app.$$("[data-route]").filter((item) => item.dataset.route !== "profiles");
    expect(nonProfileRoutes.every((item) => !item.hidden)).toBe(true);
    expect(document.getElementById("fab-add").hidden).toBe(false);
  });

  it("a viewer keeps only the pages holding links to hand out, and no FAB", () => {
    app.state.role = "viewer";
    app.state.user = "Читатель";
    app.applyRoleToChrome();
    expect(document.body.classList.contains("is-viewer")).toBe(true);
    const visible = app.$$("[data-route]").filter((item) => !item.hidden);
    expect(new Set(visible.map((item) => item.dataset.route))).toEqual(
      new Set(["profiles", "aggregates"])
    );
    expect(document.getElementById("fab-add").hidden).toBe(true);
  });

  it("shows the signed-in name with a role-specific title", () => {
    app.state.role = "viewer";
    app.state.user = "read.only@example.org";
    app.applyRoleToChrome();
    const account = document.getElementById("topbar-account");
    expect(account.hidden).toBe(false);
    expect(account.textContent).toBe("read.only@example.org");
    expect(account.title).toBe("Вы вошли как читатель");
  });

  it("hides the account chip entirely in demo mode, where there is no user", () => {
    app.state.role = "admin";
    app.state.user = null;
    app.applyRoleToChrome();
    expect(document.getElementById("topbar-account").hidden).toBe(true);
  });
});
