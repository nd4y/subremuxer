/**
 * The aggregates page: the card that summarises a merged link and the editor
 * that builds one. The rules the editor enforces on its own — no profile twice,
 * order is meaningful, a deleted source stays visible instead of vanishing —
 * are the ones a human can only notice here, so they are what is covered.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { loadApp, mountShell } from "./support/loadApp.js";

let app;

const PROFILES = [
  { id: 1, name: "Панель A", enabled: true },
  { id: 2, name: "Панель B", enabled: true },
  { id: 3, name: "Панель C", enabled: false },
];

const AGGREGATE = {
  id: 7,
  name: "Всё сразу",
  enabled: true,
  prefix_names: true,
  dedupe: true,
  output_format: "auto",
  subscription_url: "https://subremuxer.nd4y.icu/s/agg",
  sources: [
    { profile_id: 1, prefix: "A", name: "Панель A", enabled: true, missing: false },
    { profile_id: 2, prefix: "", name: "Панель B", enabled: true, missing: false },
  ],
};

function okResponse(body) {
  return { status: 200, ok: true, statusText: "OK", text: async () => JSON.stringify(body) };
}

beforeEach(() => {
  mountShell();
  app = loadApp();
  app.state.role = "admin";
  app.state.profiles = PROFILES;
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("plural", () => {
  it("declines the Russian noun after the count", () => {
    const forms = ["источник", "источника", "источников"];
    expect([1, 2, 5, 11, 21, 24, 112].map((n) => app.plural(n, forms))).toEqual([
      "источник",
      "источника",
      "источников",
      "источников",
      "источник",
      "источника",
      "источников",
    ]);
  });
});

describe("aggregateCard (admin)", () => {
  it("lists the sources it merges as the subtitle", () => {
    const card = app.aggregateCard(AGGREGATE);
    expect(app.$(".profile__upstream", card).textContent).toBe("Панель A + Панель B");
  });

  it("badges the source count and the merge switches", () => {
    const badges = app.$$(".badge", app.aggregateCard(AGGREGATE)).map((b) => b.textContent);
    expect(badges).toEqual(expect.arrayContaining(["2 источника", "с подписями", "без дублей"]));
  });

  it("says so when a source's profile has been deleted", () => {
    const card = app.aggregateCard({
      ...AGGREGATE,
      sources: [
        AGGREGATE.sources[0],
        { profile_id: 99, prefix: "", name: null, enabled: false, missing: true },
      ],
    });
    const badges = app.$$(".badge", card).map((b) => b.textContent);
    expect(badges).toContain("1 источник удалён");
    expect(app.$(".profile__upstream", card).textContent).toBe("Панель A + удалённый профиль");
  });

  it("marks a switched-off aggregate", () => {
    const card = app.aggregateCard({ ...AGGREGATE, enabled: false });
    expect(card.className).toContain("is-disabled");
    expect(app.$$(".badge", card).map((b) => b.textContent)).toContain("выключена");
  });

  it("names the forced output envelope only when one is forced", () => {
    const auto = app.$$(".badge", app.aggregateCard(AGGREGATE)).map((b) => b.textContent);
    expect(auto).not.toContain("Base64");
    const forced = app.$$(
      ".badge",
      app.aggregateCard({ ...AGGREGATE, output_format: "base64" })
    ).map((b) => b.textContent);
    expect(forced).toContain("Base64");
  });
});

describe("aggregateCard (viewer)", () => {
  it("shows the link and nothing about how it is put together", () => {
    app.state.role = "viewer";
    const card = app.aggregateCard({
      id: 7,
      name: "Всё сразу",
      enabled: true,
      subscription_url: "https://subremuxer.nd4y.icu/s/agg",
      updated_at: 0,
    });
    expect(app.$(".profile__upstream", card).textContent).toBe("Подписка активна");
    expect(app.$$(".badge", card)).toHaveLength(0);
    expect(app.$$(".profile__actions .btn", card).map((b) => b.textContent)).toEqual(["Подключить"]);
  });
});

describe("renderAggregates", () => {
  it("tells an admin what a build is for when there are none", () => {
    app.state.aggregates = [];
    app.renderAggregates();
    const empty = document.getElementById("aggregates-list");
    expect(empty.textContent).toContain("Пока нет ни одной сборки");
    expect(empty.textContent).toContain("каждый источник фильтруется своими правилами");
  });

  it("renders one card per aggregate", () => {
    app.state.aggregates = [AGGREGATE, { ...AGGREGATE, id: 8, name: "Вторая" }];
    app.renderAggregates();
    expect(app.$$("#aggregates-list .profile")).toHaveLength(2);
  });
});

describe("showAggregateEditor", () => {
  function openFor(existing) {
    app.showAggregateEditor(existing);
    return document.getElementById("sheet-body");
  }

  it("lists the existing sources in order, with their prefixes", () => {
    const body = openFor(AGGREGATE);
    expect(app.$$(".source__name", body).map((n) => n.textContent)).toEqual([
      "Панель A",
      "Панель B",
    ]);
    expect(app.$$(".source .field__input", body).map((i) => i.value)).toEqual(["A", ""]);
    expect(app.$$(".source__index", body).map((n) => n.textContent)).toEqual(["1", "2"]);
  });

  it("offers only the profiles that are not already sources", () => {
    const body = openFor(AGGREGATE);
    const options = app.$$("#a-add option", body).map((o) => o.textContent);
    expect(options).toEqual(["Панель C"]);
  });

  it("adds a picked profile to the end of the list", () => {
    const body = openFor(AGGREGATE);
    app.$("#a-add", body).value = "3";
    app.$$(".row .btn", body).find((b) => b.textContent === "Добавить").click();
    expect(app.$$(".source__name", body).map((n) => n.textContent)).toEqual([
      "Панель A",
      "Панель B",
      "Панель C",
    ]);
    // Nothing left to add, so the picker gets out of the way.
    expect(app.$("#a-add", body).disabled).toBe(true);
  });

  it("reorders sources, because order is the order of the merged list", () => {
    const body = openFor(AGGREGATE);
    app.$$(".source", body)[1].querySelector('[aria-label="Поднять источник"]').click();
    expect(app.$$(".source__name", body).map((n) => n.textContent)).toEqual([
      "Панель B",
      "Панель A",
    ]);
  });

  it("removes a source and puts its profile back in the picker", () => {
    const body = openFor(AGGREGATE);
    app.$$(".source", body)[0].querySelector('[aria-label="Убрать из сборки"]').click();
    expect(app.$$(".source__name", body).map((n) => n.textContent)).toEqual(["Панель B"]);
    expect(app.$$("#a-add option", body).map((o) => o.textContent)).toEqual([
      "Панель A",
      "Панель C",
    ]);
  });

  it("shows a deleted source as deleted rather than dropping it silently", () => {
    const body = openFor({
      ...AGGREGATE,
      sources: [{ profile_id: 42, prefix: "", name: null, enabled: false, missing: true }],
    });
    expect(app.$(".source__name", body).textContent).toBe("Профиль #42 удалён");
    expect(app.$(".source__name", body).className).toContain("source__name--missing");
  });

  it("flags a source whose profile is switched off", () => {
    const body = openFor({
      ...AGGREGATE,
      sources: [{ profile_id: 3, prefix: "", name: "Панель C", enabled: false, missing: false }],
    });
    expect(app.$(".source .badge", body).textContent).toBe("выключен");
  });

  it("posts a new aggregate with the sources it was given", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(okResponse({ id: 9 }))
      .mockResolvedValueOnce(okResponse([]))
      .mockResolvedValueOnce(okResponse([]))
      .mockResolvedValueOnce(okResponse(null));
    vi.stubGlobal("fetch", fetchMock);

    const body = openFor(null);
    app.$("#a-name", body).value = "Новая";
    app.$("#a-add", body).value = "2";
    app.$$(".row .btn", body).find((b) => b.textContent === "Добавить").click();
    app.$(".source .field__input", body).value = "B";
    app.$(".source .field__input", body).dispatchEvent(new Event("input"));

    app.$$("#sheet-footer .btn").find((b) => b.textContent === "Сохранить").click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/aggregates");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toMatchObject({
      name: "Новая",
      enabled: true,
      prefix_names: true,
      dedupe: true,
      output_format: "auto",
      sources: [{ profile_id: 2, prefix: "B" }],
    });
  });

  it("updates in place when editing an existing aggregate", async () => {
    const fetchMock = vi.fn().mockResolvedValue(okResponse([]));
    vi.stubGlobal("fetch", fetchMock);

    openFor(AGGREGATE);
    app.$$("#sheet-footer .btn").find((b) => b.textContent === "Сохранить").click();
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [path, options] = fetchMock.mock.calls[0];
    expect(path).toBe("/api/aggregates/7");
    expect(options.method).toBe("PUT");
  });
});

describe("deleteAggregate", () => {
  it("drops it from the list and offers Undo", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(okResponse(null)));
    app.state.aggregates = [AGGREGATE];

    await app.deleteAggregate(AGGREGATE);

    expect(app.state.aggregates).toEqual([]);
    const snackbar = document.getElementById("snackbar");
    expect(snackbar.textContent).toContain("«Всё сразу» удалена");
    expect(app.$(".snackbar__action", snackbar).textContent).toBe("Отменить");
  });

  it("keeps the list untouched when the server refuses", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue({
        status: 404,
        ok: false,
        statusText: "Not Found",
        text: async () => JSON.stringify({ detail: "Сборка не найдена" }),
      })
    );
    app.state.aggregates = [AGGREGATE];

    await app.deleteAggregate(AGGREGATE);

    expect(app.state.aggregates).toEqual([AGGREGATE]);
    expect(document.getElementById("snackbar").textContent).toBe("Сборка не найдена");
  });
});

describe("showLinkSheet for an aggregate", () => {
  it("points the QR code and the token rotation at the aggregate endpoints", () => {
    app.showLinkSheet(AGGREGATE, { kind: "aggregates" });
    const body = document.getElementById("sheet-body");
    expect(app.$(".qr img", body).getAttribute("src")).toBe("/api/aggregates/7/qr.svg");
    // An aggregate has nothing of its own to export.
    expect(body.textContent).not.toContain("Экспорт YAML");
    expect(body.textContent).toContain("Сменить токен");
  });
});
