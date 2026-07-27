/**
 * Logic that runs before any request leaves the browser: the regex preview,
 * URL/OS helpers, the syntax highlighter, and the escape hatch. None of it
 * touches the DOM, so no fixture is needed — just a fresh app per test.
 */
import { beforeEach, describe, expect, it } from "vitest";
import { loadApp } from "./support/loadApp.js";

let app;

beforeEach(() => {
  app = loadApp();
});

describe("buildRegexLocally", () => {
  it("mirrors app/filtering.py::build_regex for a single contains condition", () => {
    const filter = {
      mode: "builder",
      match: "all",
      case_sensitive: false,
      conditions: [{ op: "contains", value: "LTE" }],
    };
    expect(app.buildRegexLocally(filter)).toBe("(?i)^(?=.*LTE).*$");
  });

  it("ANDs conditions when match is all", () => {
    const filter = {
      mode: "builder",
      match: "all",
      case_sensitive: false,
      conditions: [
        { op: "contains", value: "LTE" },
        { op: "not_contains", value: "RU" },
      ],
    };
    expect(app.buildRegexLocally(filter)).toBe("(?i)^(?=.*LTE)(?!.*RU).*$");
  });

  it("ORs conditions when match is any", () => {
    const filter = {
      mode: "builder",
      match: "any",
      case_sensitive: false,
      conditions: [
        { op: "contains", value: "NL" },
        { op: "contains", value: "DE" },
      ],
    };
    expect(app.buildRegexLocally(filter)).toBe("(?i)^(?:(?:(?=.*NL).*)|(?:(?=.*DE).*))$");
  });

  it("drops the case-insensitive flag when case_sensitive is set", () => {
    const filter = {
      mode: "builder",
      match: "all",
      case_sensitive: true,
      conditions: [{ op: "equals", value: "RU-1" }],
    };
    expect(app.buildRegexLocally(filter)).toBe("^(?=RU-1$).*$");
  });

  it("escapes regex metacharacters in literal operators", () => {
    const filter = {
      mode: "builder",
      match: "all",
      case_sensitive: false,
      conditions: [{ op: "contains", value: "a.b+c" }],
    };
    expect(app.buildRegexLocally(filter)).toBe("(?i)^(?=.*a\\.b\\+c).*$");
  });

  it("leaves a regex operator's value unescaped", () => {
    const filter = {
      mode: "builder",
      match: "all",
      case_sensitive: false,
      conditions: [{ op: "regex", value: "lte|4g" }],
    };
    expect(app.buildRegexLocally(filter)).toBe("(?i)^(?=.*(?:lte|4g)).*$");
  });

  it("returns empty for raw mode — the server compiles that side instead", () => {
    expect(app.buildRegexLocally({ mode: "raw", conditions: [] })).toBe("");
  });

  it("returns empty when every condition has an empty value", () => {
    const filter = { mode: "builder", match: "all", case_sensitive: false, conditions: [{ op: "contains", value: "" }] };
    expect(app.buildRegexLocally(filter)).toBe("");
  });
});

describe("hostOf", () => {
  it("extracts the host from a well-formed URL", () => {
    expect(app.hostOf("https://rw-sub.st1.nd4y.icu:8443/sub/abc")).toBe("rw-sub.st1.nd4y.icu:8443");
  });

  it("falls back to the original string for something unparsable", () => {
    expect(app.hostOf("not a url")).toBe("not a url");
  });
});

describe("forcedFormatLabel", () => {
  it("is null when the profile carries no User-Agent override", () => {
    expect(app.forcedFormatLabel("")).toBeNull();
    expect(app.forcedFormatLabel(null)).toBeNull();
  });

  it("names the format family for a known preset", () => {
    app.state.meta = { client_presets: [{ id: "happ", user_agent: "Happ/2.16.0", family: "Xray JSON" }] };
    expect(app.forcedFormatLabel("Happ/2.16.0")).toBe("формат: Xray JSON");
  });

  it("falls back to a generic label for an unrecognised User-Agent", () => {
    app.state.meta = { client_presets: [] };
    expect(app.forcedFormatLabel("MyClient/1.0")).toBe("свой User-Agent");
  });
});

describe("isViewer", () => {
  it("reads the role off state", () => {
    app.state.role = "admin";
    expect(app.isViewer()).toBe(false);
    app.state.role = "viewer";
    expect(app.isViewer()).toBe(true);
  });
});

describe("HWID_RE", () => {
  it.each(["a".repeat(10), "ABC123=-abc123", "x".repeat(64)])("accepts a valid HWID %s", (value) => {
    expect(app.HWID_RE.test(value)).toBe(true);
  });

  it.each(["short", "x".repeat(65), "has spaces!", ""])("rejects an invalid HWID %s", (value) => {
    expect(app.HWID_RE.test(value)).toBe(false);
  });
});

describe("escapeHtml / highlightJson / highlightYaml", () => {
  it("escapes the three HTML-significant characters", () => {
    expect(app.escapeHtml("<a> & </a>")).toBe("&lt;a&gt; &amp; &lt;/a&gt;");
  });

  it("wraps a JSON key and string value in their token spans", () => {
    const html = app.highlightJson('{"name": "value"}');
    expect(html).toContain('<span class="tok-key">"name"</span>:');
    expect(html).toContain('<span class="tok-str">"value"</span>');
  });

  it("wraps JSON literals and numbers", () => {
    expect(app.highlightJson("true")).toContain('<span class="tok-lit">true</span>');
    expect(app.highlightJson("42.5")).toContain('<span class="tok-num">42.5</span>');
  });

  it("wraps a YAML key and treats a trailing # as a comment", () => {
    const html = app.highlightYaml("name: value # a comment");
    expect(html).toContain('<span class="tok-key">name</span>:');
    expect(html).toContain('<span class="tok-com"># a comment</span>');
  });

  it("does not treat a # inside a quoted YAML scalar as a comment", () => {
    const html = app.highlightYaml('title: "not # a comment"');
    expect(html).not.toContain("tok-com");
  });
});

describe("detectOs", () => {
  it("prefers a value already stored for this browser", () => {
    localStorage.setItem(app.OS_KEY, "ios");
    expect(app.detectOs()).toBe("ios");
  });

  it("detects Android from the user agent when nothing is stored", () => {
    Object.defineProperty(navigator, "userAgent", {
      value: "Mozilla/5.0 (Linux; Android 14)",
      configurable: true,
    });
    expect(app.detectOs()).toBe("android");
  });

  it("falls back to android when nothing matches", () => {
    Object.defineProperty(navigator, "userAgent", { value: "SomeExoticBot/1.0", configurable: true });
    expect(app.detectOs()).toBe("android");
  });
});

describe("CLIENTS import-scheme builders", () => {
  it("builds a plain Happ deep link", () => {
    const happ = app.CLIENTS.find((client) => client.id === "happ");
    expect(happ.build("https://example.com/s/tok")).toBe("happ://add/https://example.com/s/tok");
  });

  it("base64-encodes the URL for Shadowrocket", () => {
    const shadowrocket = app.CLIENTS.find((client) => client.id === "shadowrocket");
    const built = shadowrocket.build("https://example.com/s/tok");
    expect(built).toBe(`shadowrocket://add/sub://${btoa("https://example.com/s/tok")}`);
  });

  it("URL-encodes the subscription link where the scheme takes a query param", () => {
    const singbox = app.CLIENTS.find((client) => client.id === "singbox");
    const url = "https://example.com/s/tok?format=base64";
    expect(singbox.build(url)).toBe(`sing-box://import-remote-profile?url=${encodeURIComponent(url)}`);
  });

  it("every listed client name is unique", () => {
    const ids = app.CLIENTS.map((client) => client.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("auto-login suppression", () => {
  it("is not suppressed by default", () => {
    expect(app.autoLoginSuppressed()).toBe(false);
  });

  it("is suppressed once ?disableAutoLogin=true is visited, and stays suppressed after", () => {
    window.history.pushState({}, "", "/?disableAutoLogin=true");
    expect(app.autoLoginSuppressed()).toBe(true);
    expect(sessionStorage.getItem(app.NO_AUTO_LOGIN_KEY)).toBe("1");

    window.history.pushState({}, "", "/");
    expect(app.autoLoginSuppressed()).toBe(true);
  });

  it("ignores ?disableAutoLogin=false — the same spelling Grafana treats as off", () => {
    window.history.pushState({}, "", "/?disableAutoLogin=false");
    expect(app.autoLoginSuppressed()).toBe(false);
  });

  it("suppressAutoLogin() alone is enough, without visiting the URL", () => {
    app.suppressAutoLogin();
    expect(app.autoLoginSuppressed()).toBe(true);
  });
});

describe("applyAuthState", () => {
  it("maps /api/auth/me onto state", () => {
    app.applyAuthState({
      authenticated: true,
      demo: false,
      role: "admin",
      user: "Тестовый",
      methods: { password: false, oidc: true },
      oidc_name: "Keycloak",
      auto_login: false,
    });
    expect(app.state).toMatchObject({
      authenticated: true,
      demo: false,
      role: "admin",
      user: "Тестовый",
      methods: { password: false, oidc: true },
      oidcName: "Keycloak",
      autoLogin: false,
    });
  });

  it("defaults role to viewer and methods to password-only for a bare response", () => {
    app.applyAuthState({ authenticated: false });
    expect(app.state.role).toBe("viewer");
    expect(app.state.methods).toEqual({ password: true, oidc: false });
  });
});
