// Minimal DOM adapter: execute the actual page module and its event handlers.
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

class Element {
  constructor(tag) {
    this.tagName = tag;
    this.children = [];
    this.listeners = new Map();
    this.value = "";
    this.checked = false;
    this.classList = { toggle() {} };
  }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  add(option) { this.append(option); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(handler);
  }
  async emit(name) {
    await Promise.all((this.listeners.get(name) ?? []).map((handler) => handler({
      currentTarget: this, preventDefault() {},
    })));
  }
  showModal() { this.open = true; }
  close() { this.open = false; this.emit("close"); }
}
class Container extends Element {
  constructor() { super("div"); this.fields = new Map(); }
  set innerHTML(markup) {
    for (const match of markup.matchAll(/data-(area|action)="([^"]+)"/g)) {
      this.fields.set(`[data-${match[1]}="${match[2]}"]`, new Element("div"));
    }
    this.fields.set("select", new Element("select"));
    this.fields.set("dialog", new Element("dialog"));
  }
  querySelector(selector) { return this.fields.get(selector); }
}
globalThis.document = {
  createElement: (tag) => new Element(tag),
  createTextNode: (text) => text,
  addEventListener() {},
};
globalThis.Option = class extends Element {
  constructor(text, value) { super("option"); this.textContent = text; this.value = value; }
};
const settle = () => new Promise(setImmediate);
const state = (enabled) => ({ global_requested: true, group_requested: enabled, effective: enabled, blocked_by: enabled ? [] : ["group_off"] });
const states = (enabled) => Object.fromEntries(["ai", "web", "meme", "memory"].map((name) => [name, state(enabled)]));
let releaseSave;
const delayedSave = new Promise((resolve) => { releaseSave = resolve; });
let saveStarted = false;
globalThis.testAdminFetch = async (path, options = {}) => {
  if (options.method === "PUT" && path === "/groups/34/features/web") {
    saveStarted = true;
    return delayedSave;
  }
  if (path === "/features/global") return states(true);
  if (path.startsWith("/groups?")) return {
    items: [{ group_id: 34, allowed: true }, { group_id: 35, allowed: true }], total: 2,
  };
  if (path === "/groups/34/features" || path === "/groups/35/features") return states(false);
  throw new Error(`Unexpected request ${path}`);
};
const source = (await readFile(process.argv[2], "utf8")).replace(
  'import { adminFetch } from "../api.js";',
  "const adminFetch = globalThis.testAdminFetch;",
);
const { createFeatures } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);
const container = new Container();
const page = createFeatures(container);
await page.refresh();
const select = container.querySelector("select");
async function choose(group) { select.value = group; await select.emit("change"); await settle(); }
function webRow() {
  const row = container.querySelector('[data-area="group"]').children[1];
  return { row, input: row.children[1].children[0].children[0], status: row.children[0].children[1] };
}
await choose("34");
const origin = webRow();
origin.input.checked = true;
await origin.input.emit("change");
const save = origin.row.emit("submit");
await settle();
assert.equal(saveStarted, true);
await choose("35");
const destination = webRow();
destination.input.checked = true;
await destination.input.emit("change");
releaseSave(state(true));
await save;
await settle();
assert.equal(destination.input.checked, true, "late group A save must retain group B's unsaved checkbox");
assert.match(destination.status.textContent, /保存：关闭/, "group B must still show its own saved state");
await page.refresh();
assert.equal(destination.input.checked, true, "later refresh must retain group B's dirty choice");
console.log("Delayed group save keeps the destination form unchanged");
