/**
 * The shared action helpers introduced to collapse repeated try/catch-toast
 * and delete-then-offer-Undo blocks (attempt, attemptOk, undoToast, iconButton),
 * plus the profile actions rebuilt on top of them — the refactor's actual
 * safety net, not just a re-check of behaviour that predates it.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadApp, mountShell } from "./support/loadApp.js";

let app;

function okResponse(body) {
  return { status: 200, ok: true, statusText: "OK", text: async () => JSON.stringify(body) };
}

function failResponse(detail, status = 400) {
  return { status, ok: false, statusText: "Bad Request", text: async () => JSON.stringify({ detail }) };
}

beforeEach(() => {
  mountShell();
  app = loadApp();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("attempt", () => {
  it("returns the action's resolved value on success", async () => {
    expect(await app.attempt(() => Promise.resolve(42))).toBe(42);
  });

  it("toasts the error and resolves to undefined on failure", async () => {
    const result = await app.attempt(() => {
      throw new app.ApiError("nope", 400);
    });
    expect(result).toBeUndefined();
    expect(document.getElementById("snackbar").textContent).toBe("nope");
  });
});

describe("attemptOk", () => {
  it("resolves true on success", async () => {
    expect(await app.attemptOk(() => Promise.resolve())).toBe(true);
  });

  it("toasts the error and resolves false on failure", async () => {
    const ok = await app.attemptOk(() => {
      throw new Error("boom");
    });
    expect(ok).toBe(false);
    expect(document.getElementById("snackbar").textContent).toBe("boom");
  });
});

describe("iconButton", () => {
  it("builds a labelled icon-btn and wires the click handler", () => {
    const onclick = vi.fn();
    const node = app.iconButton({ icon: "<svg></svg>", label: "Удалить", onclick });
    expect(node.className).toBe("icon-btn");
    expect(node.getAttribute("aria-label")).toBe("Удалить");
    node.click();
    expect(onclick).toHaveBeenCalledOnce();
  });

  it("omits onclick/title entirely when not given, rather than setting them empty", () => {
    const node = app.iconButton({ icon: "<svg></svg>", label: "Скопировать" });
    expect(node.hasAttribute("title")).toBe(false);
  });
});

describe("undoToast", () => {
  it("shows the message with an Отменить action that runs onUndo", async () => {
    const onUndo = vi.fn().mockResolvedValue(undefined);
    app.undoToast("Профиль «x» удалён", onUndo);
    const button = document.querySelector("#snackbar .snackbar__action");
    expect(button.textContent).toBe("Отменить");
    button.click();
    expect(onUndo).toHaveBeenCalledOnce();
  });

  it("routes a failed undo through the same error toast as any other action", () => {
    app.undoToast("Профиль «x» удалён", () => {
      throw new Error("не удалось восстановить");
    });
    const button = document.querySelector("#snackbar .snackbar__action");
    button.click();
    // attemptOk()'s catch runs synchronously here — onUndo throws before ever
    // reaching an await — so the error toast is already in place post-click.
    expect(document.getElementById("snackbar").textContent).toBe("не удалось восстановить");
  });
});

describe("cloneProfile (rebuilt on attempt())", () => {
  it("reloads the profile list and offers to open the editor for the clone", async () => {
    // loadProfiles() fetches both /api/profiles and /api/stats for an admin.
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ id: 2, name: "Мой профиль (копия)" }))
      .mockResolvedValueOnce(okResponse([]))
      .mockResolvedValueOnce(okResponse(null));
    vi.stubGlobal("fetch", fetchMock);
    app.state.role = "admin";

    await app.cloneProfile({ id: 1, name: "Мой профиль" });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/profiles/1/clone");
    expect(document.getElementById("snackbar").textContent).toContain("Мой профиль (копия)");
  });

  it("toasts the server error and does not touch the profile list on failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failResponse("апстрим недоступен")));
    app.state.profiles = [{ id: 1, name: "x" }];

    await app.cloneProfile({ id: 1, name: "x" });

    expect(document.getElementById("snackbar").textContent).toBe("апстрим недоступен");
    expect(app.state.profiles).toEqual([{ id: 1, name: "x" }]);
  });
});

describe("deleteProfile (rebuilt on attemptOk() + undoToast())", () => {
  it("removes the profile locally and re-renders once the server confirms", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse(null)));
    app.state.role = "admin";
    app.state.profiles = [
      { id: 1, name: "A", enabled: true, upstream_url: "https://a", subscription_url: "https://s/1", protocols: [] },
      { id: 2, name: "B", enabled: true, upstream_url: "https://b", subscription_url: "https://s/2", protocols: [] },
    ];

    await app.deleteProfile(app.state.profiles[0]);

    expect(app.state.profiles.map((p) => p.id)).toEqual([2]);
    expect(document.getElementById("snackbar").textContent).toContain("«A» удалён");
  });

  it("leaves the profile list untouched when the delete request fails", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(failResponse("профиль не найден", 404)));
    app.state.profiles = [{ id: 1, name: "A" }];

    await app.deleteProfile({ id: 1, name: "A" });

    expect(app.state.profiles).toEqual([{ id: 1, name: "A" }]);
    expect(document.getElementById("snackbar").textContent).toBe("профиль не найден");
  });
});
