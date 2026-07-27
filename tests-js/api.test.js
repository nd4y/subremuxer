/**
 * The `request()`/`api` layer: how a response becomes either a value or an
 * ApiError, and what happens to the session on a 401. `fetch` is mocked in
 * every test — nothing here talks to a real server.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadApp, mountShell } from "./support/loadApp.js";

let app;

function jsonResponse(body, { status = 200, statusText = "OK" } = {}) {
  return {
    status,
    statusText,
    ok: status >= 200 && status < 300,
    text: async () => JSON.stringify(body),
  };
}

beforeEach(() => {
  mountShell();
  app = loadApp();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api.get / request", () => {
  it("sends credentials and decodes a JSON body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ ok: true })));
    const result = await app.api.get("/api/profiles");
    expect(result).toEqual({ ok: true });
    expect(fetch).toHaveBeenCalledWith(
      "/api/profiles",
      expect.objectContaining({ method: "GET", credentials: "same-origin" })
    );
  });

  it("JSON-encodes the body and sets the content-type for a write", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse({ id: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    await app.api.post("/api/profiles", { name: "x" });
    const [, options] = fetchMock.mock.calls[0];
    expect(options.headers["content-type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({ name: "x" });
  });

  it("falls back to the raw text when the body is not JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 200, ok: true, statusText: "OK", text: async () => "not json" })
    );
    expect(await app.api.get("/api/whatever")).toBe("not json");
  });

  it("returns null for an empty body", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({ status: 204, ok: true, statusText: "No Content", text: async () => "" })
    );
    expect(await app.api.del("/api/profiles/1")).toBeNull();
  });

  it("raises an ApiError carrying the server's detail message", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse({ detail: "укажите название профиля" }, { status: 400 })));
    await expect(app.api.post("/api/profiles", {})).rejects.toMatchObject({
      message: "укажите название профиля",
      status: 400,
    });
  });

  it("falls back to the HTTP status text when the body carries no detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse("oops", { status: 500, statusText: "Server Error" })));
    await expect(app.api.get("/api/profiles")).rejects.toMatchObject({ message: "Server Error", status: 500 });
  });

  it("on a 401 marks the session gone and shows the login screen, without a retry", async () => {
    app.state.authenticated = true;
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ status: 401, ok: false, statusText: "Unauthorized" }));

    await expect(app.api.get("/api/profiles")).rejects.toMatchObject({ status: 401 });
    expect(app.state.authenticated).toBe(false);
    expect(document.getElementById("login").hidden).toBe(false);
    expect(document.getElementById("shell").hidden).toBe(true);
  });
});
