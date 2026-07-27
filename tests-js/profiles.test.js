/**
 * The profile list: what an admin sees and edits versus what a viewer's own,
 * far narrower card renders — the field-level redaction happens on the server
 * (see app/routers/admin.py::_profile_view), but the two card layouts are what
 * a human actually looks at, so they get their own coverage here.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { loadApp, mountShell } from "./support/loadApp.js";

let app;

const ADMIN_PROFILE = {
  id: 1,
  name: "Мой профиль",
  enabled: true,
  upstream_url: "https://rw.st1.nd4y.icu/sub/abc",
  subscription_url: "https://subremuxer.nd4y.icu/s/tok",
  hwid_mode: "override",
  device_model: "Pixel 9",
  upstream_ua: "",
  filter: { mode: "builder", conditions: [{ op: "contains", value: "LTE" }] },
  protocols: ["vless"],
  cache_ttl: 30,
};

beforeEach(() => {
  mountShell();
  app = loadApp();
  app.state.meta = { client_presets: [] };
});

describe("profileCard (admin)", () => {
  it("shows the upstream host, not the subscription link, as the subtitle", () => {
    app.state.role = "admin";
    const card = app.profileCard(ADMIN_PROFILE);
    expect(app.$(".profile__upstream", card).textContent).toBe("rw.st1.nd4y.icu");
  });

  it("badges the HWID mode, device, filter condition count, protocols and cache TTL", () => {
    app.state.role = "admin";
    const card = app.profileCard(ADMIN_PROFILE);
    const text = app.$$(".badge", card).map((b) => b.textContent);
    expect(text).toEqual(
      expect.arrayContaining(["HWID подменяется", "Pixel 9", "1 усл.", "vless", "кэш 30s"])
    );
  });

  it("badges a disabled profile and applies the disabled class", () => {
    app.state.role = "admin";
    const card = app.profileCard({ ...ADMIN_PROFILE, enabled: false });
    expect(card.classList.contains("is-disabled")).toBe(true);
    expect(app.$$(".badge", card).map((b) => b.textContent)).toContain("выключен");
  });

  it("offers admin-only actions: configure, test, clone, save as template, delete", () => {
    app.state.role = "admin";
    const card = app.profileCard(ADMIN_PROFILE);
    const labels = app.$$(".profile__actions button", card).map((b) => b.textContent || b.getAttribute("aria-label"));
    expect(labels).toEqual(
      expect.arrayContaining(["Настроить", "Тест", "Клонировать профиль", "Сохранить как шаблон", "Удалить профиль"])
    );
  });
});

describe("viewerProfileCard", () => {
  it("shows the subscription link as the visible URL, never the upstream one", () => {
    const card = app.viewerProfileCard(ADMIN_PROFILE);
    expect(card.textContent).toContain(ADMIN_PROFILE.subscription_url);
    expect(card.textContent).not.toContain(ADMIN_PROFILE.upstream_url);
  });

  it("carries none of the admin badges — HWID, filter, protocols never render", () => {
    const card = app.viewerProfileCard(ADMIN_PROFILE);
    expect(card.querySelector(".badge")).toBeNull();
  });

  it("offers exactly one action: connect, no edit/delete/clone", () => {
    const card = app.viewerProfileCard(ADMIN_PROFILE);
    const actionLabels = app.$$(".profile__actions button", card).map((b) => b.textContent);
    expect(actionLabels).toEqual(["Подключить"]);
  });

  it("profileCard() dispatches to the viewer card when state.role is viewer", () => {
    app.state.role = "viewer";
    const card = app.profileCard(ADMIN_PROFILE);
    expect(card.querySelector(".badge")).toBeNull();
    expect(app.$$(".profile__actions button", card).map((b) => b.textContent)).toEqual(["Подключить"]);
  });
});

describe("renderProfiles", () => {
  it("renders one card per profile", () => {
    app.state.role = "admin";
    app.state.profiles = [ADMIN_PROFILE, { ...ADMIN_PROFILE, id: 2, name: "Второй" }];
    app.renderProfiles();
    expect(document.querySelectorAll("#profiles-list .profile")).toHaveLength(2);
  });

  it("shows an admin-worded empty state when there are no profiles", () => {
    app.state.role = "admin";
    app.state.profiles = [];
    app.renderProfiles();
    expect(document.getElementById("profiles-list").textContent).toMatch(/Добавьте ссылку/);
  });

  it("shows a viewer-worded empty state instead", () => {
    app.state.role = "viewer";
    app.state.profiles = [];
    app.renderProfiles();
    expect(document.getElementById("profiles-list").textContent).toMatch(/откроет для вас/);
  });
});
