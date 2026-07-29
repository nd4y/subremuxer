/**
 * The hyperscript helper and the small form-field builders every editor is
 * assembled from, plus the snackbar and theme toggle that read/write real
 * elements from the app shell.
 */
import { beforeEach, describe, expect, it, vi } from "vitest";
import { loadApp, loadHelp, mountShell } from "./support/loadApp.js";

let app;

beforeEach(() => {
  mountShell();
  app = loadApp();
});

describe("h() — the hyperscript helper", () => {
  it("sets class, text and dataset from props", () => {
    const node = app.h("div", { class: "card", text: "hi", dataset: { id: "7" } });
    expect(node.tagName).toBe("DIV");
    expect(node.className).toBe("card");
    expect(node.textContent).toBe("hi");
    expect(node.dataset.id).toBe("7");
  });

  it("wires an onX prop as a real event listener", () => {
    const onclick = vi.fn();
    const node = app.h("button", { onclick });
    node.click();
    expect(onclick).toHaveBeenCalledOnce();
  });

  it("flattens nested arrays of children and drops null/undefined/false", () => {
    const node = app.h("ul", {}, [app.h("li", { text: "a" }), null, [app.h("li", { text: "b" }), false, undefined]]);
    expect(node.children).toHaveLength(2);
    expect(node.textContent).toBe("ab");
  });

  it("renders a bare string child as text, not markup", () => {
    const node = app.h("p", {}, "<b>not bold</b>");
    expect(node.children).toHaveLength(0);
    expect(node.textContent).toBe("<b>not bold</b>");
  });

  it("sets an attribute for true and omits it for false/null/undefined", () => {
    const node = app.h("input", { disabled: true, required: false, placeholder: null });
    expect(node.hasAttribute("disabled")).toBe(true);
    expect(node.hasAttribute("required")).toBe(false);
    expect(node.hasAttribute("placeholder")).toBe(false);
  });
});

describe("$ / $$", () => {
  it("$ returns the first match, scoped to a root when given one", () => {
    const root = app.h("div", {}, app.h("span", { class: "x", text: "inner" }));
    document.body.append(root);
    expect(app.$(".x", root).textContent).toBe("inner");
    document.body.removeChild(root);
  });

  it("$$ returns a real array, not a live NodeList", () => {
    const root = app.h("div", {}, app.h("i", { class: "y" }), app.h("i", { class: "y" }));
    expect(Array.isArray(app.$$(".y", root))).toBe(true);
    expect(app.$$(".y", root)).toHaveLength(2);
  });
});

describe("field builders", () => {
  it("field() associates the input with its label via a wrapping <label>", () => {
    const node = app.field({ id: "p-name", label: "Название", value: "x" });
    const input = app.$("input", node);
    expect(node.tagName).toBe("LABEL");
    expect(input.id).toBe("p-name");
    expect(input.value).toBe("x");
    expect(app.$(".field__label", node).textContent).toBe("Название");
  });

  it("selectField() renders one <option> per entry and preselects the value", () => {
    const node = app.selectField({
      id: "fmt",
      label: "Формат",
      value: "yaml",
      options: [{ value: "yaml", label: "YAML" }, { value: "json", label: "JSON" }],
    });
    const select = app.$("select", node);
    expect(select.value).toBe("yaml");
    expect(app.$$("option", select)).toHaveLength(2);
  });

  it("segmented() marks the initial value selected and swaps selection on click", () => {
    const onChange = vi.fn();
    const node = app.segmented({
      value: "all",
      options: [{ value: "all", label: "Все" }, { value: "any", label: "Любое" }],
      onChange,
    });
    const [all, any] = app.$$(".segmented__item", node);
    expect(all.classList.contains("is-selected")).toBe(true);
    any.click();
    expect(onChange).toHaveBeenCalledWith("any");
    expect(any.classList.contains("is-selected")).toBe(true);
    expect(all.classList.contains("is-selected")).toBe(false);
  });

  it("switchRow() calls onChange with the checkbox's new state", () => {
    const onChange = vi.fn();
    const node = app.switchRow({ id: "s", title: "T", checked: false, onChange });
    const input = app.$("input", node);
    input.checked = true;
    input.dispatchEvent(new window.Event("change"));
    expect(onChange).toHaveBeenCalledWith(true);
  });
});

describe("helpBlock", () => {
  it("renders a link block as an anchor that cannot reach back into the app", () => {
    const node = app.helpBlock({ link: { href: "https://example.org/repo", text: "repo" } });
    const link = app.$("a", node);
    expect(link.getAttribute("href")).toBe("https://example.org/repo");
    expect(link.textContent).toBe("repo");
    expect(link.getAttribute("target")).toBe("_blank");
    // Without noopener the opened tab gets a handle on window.opener.
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("gives both roles a way to the project, since a viewer has no settings", () => {
    loadHelp();
    for (const sections of [window.HELP_SECTIONS, window.HELP_SECTIONS_VIEWER]) {
      const blocks = sections.flatMap((section) => section.blocks);
      const links = blocks.filter((block) => block.link).map((block) => block.link.href);
      expect(links).toContain("https://github.com/nd4y/subremuxer");
    }
  });

  it("settings carry the same link as a plain anchor, styled as a button", () => {
    const link = document.getElementById("project-link");
    expect(link.tagName).toBe("A");
    expect(link.getAttribute("href")).toBe("https://github.com/nd4y/subremuxer");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    expect(link.className).toContain("btn");
  });
});

describe("theme", () => {
  it("cycleTheme() advances auto -> light -> dark -> auto and persists the choice", () => {
    localStorage.removeItem(app.THEME_KEY);
    app.cycleTheme();
    expect(localStorage.getItem(app.THEME_KEY)).toBe("light");
    app.cycleTheme();
    expect(localStorage.getItem(app.THEME_KEY)).toBe("dark");
    app.cycleTheme();
    expect(localStorage.getItem(app.THEME_KEY)).toBe("auto");
  });

  it("applyTheme() stamps data-theme on <html> and marks the matching segmented option", () => {
    app.applyTheme("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    const selected = document.querySelector("#theme-segmented .segmented__item.is-selected");
    expect(selected.dataset.theme).toBe("dark");
  });
});

describe("snackbar", () => {
  it("toast() shows the message and un-hides the snackbar", () => {
    app.toast("Скопировано");
    const node = document.getElementById("snackbar");
    expect(node.hidden).toBe(false);
    expect(node.textContent).toBe("Скопировано");
  });

  it("an action toast renders a clickable action and a countdown ring", () => {
    const onClick = vi.fn();
    app.toast("Удалено", { action: { label: "Отменить", onClick } });
    const node = document.getElementById("snackbar");
    const button = node.querySelector(".snackbar__action");
    expect(button.textContent).toBe("Отменить");
    expect(node.querySelector(".countdown")).not.toBeNull();
    button.click();
    expect(onClick).toHaveBeenCalledOnce();
    // dismissSnackbar() hides the node after a 200ms close transition, not
    // synchronously — is-closing is the immediate, testable signal.
    expect(node.classList.contains("is-closing")).toBe(true);
  });

  it("a variant is reflected as a snackbar--<variant> class", () => {
    app.toast("Ошибка", "error");
    expect(document.getElementById("snackbar").className).toContain("snackbar--error");
  });
});
