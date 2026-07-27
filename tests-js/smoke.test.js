import { describe, expect, it } from "vitest";
import { loadApp } from "./support/loadApp.js";

describe("loadApp", () => {
  it("evaluates app.js and exposes its top-level names", () => {
    const app = loadApp();
    expect(typeof app.buildRegexLocally).toBe("function");
    expect(typeof app.h).toBe("function");
    expect(app.state.role).toBe("viewer");
  });

  it("gives every call a fresh, independent state object", () => {
    const first = loadApp();
    first.state.role = "admin";
    const second = loadApp();
    expect(second.state.role).toBe("viewer");
  });
});
