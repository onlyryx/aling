import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

class Element {
  constructor(tag, attributes = {}) {
    this.tagName = tag.toLowerCase();
    this.attributes = attributes;
    this.children = [];
    this.listeners = new Map();
    this.dataset = Object.fromEntries(Object.entries(attributes)
      .filter(([name]) => name.startsWith("data-") && name !== "data-area" && name !== "data-field" && name !== "data-form")
      .map(([name, value]) => [name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase()), value]));
    this.classList = { toggle() {} };
    this.value = "";
    this.checked = false;
    this.disabled = false;
    this.hidden = false;
    this.textContent = "";
  }
  append(...children) { this.children.push(...children); }
  addEventListener(name, handler) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(handler);
  }
  async emit(name, details = {}) {
    for (const handler of this.listeners.get(name) ?? []) {
      await handler({ currentTarget: this, preventDefault() {}, ...details });
    }
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  querySelectorAll(selector) {
    const choices = selector.split(",").map((item) => item.trim());
    const results = [];
    const visit = (element) => {
      for (const child of element.children) {
        if (choices.some((choice) => matches(child, choice))) results.push(child);
        visit(child);
      }
    };
    visit(this);
    return results;
  }
  set innerHTML(markup) { this.children = parse(markup); }
  reportValidity() { return true; }
}

function matches(element, selector) {
  const attribute = selector.match(/^\[([\w-]+)="([^"]+)"\]$/);
  if (attribute) return element.attributes[attribute[1]] === attribute[2];
  return element.tagName === selector.toLowerCase();
}

function parse(markup) {
  const root = new Element("root");
  const stack = [root];
  const voidTags = new Set(["input", "img", "br", "hr", "meta", "link"]);
  for (const match of markup.matchAll(/<\/?([\w-]+)([^>]*)>/g)) {
    const [, tag, rawAttributes] = match;
    if (match[0].startsWith("</")) {
      if (stack.length > 1) stack.pop();
      continue;
    }
    const attributes = Object.fromEntries([...rawAttributes.matchAll(/([\w-]+)(?:="([^"]*)")?/g)]
      .map((entry) => [entry[1], entry[2] ?? ""]));
    const element = new Element(tag, attributes);
    stack.at(-1).append(element);
    if (!voidTags.has(tag.toLowerCase())) stack.push(element);
  }
  return root.children;
}

const state = {
  conditions: {
    timezone: "Asia/Shanghai", quiet_start: "23:00", quiet_end: "08:00",
    global_enabled: true, cooldown_seconds: 300, max_per_hour: 3,
    max_per_day: 1, onebot_connected: true,
  },
  items: [{
    group_id: 34,
    schedule: { hour: 12, minute: 0, text: "saved text", last_attempted_date: null },
    proactive_enabled: false,
    active: false,
    inactive_reasons: ["group_off"],
  }],
};
globalThis.testAdminFetch = async (path, options = {}) => {
  if (path === "/schedules") return structuredClone(state);
  if (path === "/groups/34/schedule" && options.method === "PUT") {
    throw new Error("fictional save failure");
  }
  if (path === "/groups/34/proactive" && options.method === "PUT") {
    state.items[0].proactive_enabled = options.body.enabled;
    state.items[0].active = options.body.enabled;
    state.items[0].inactive_reasons = options.body.enabled ? [] : ["group_off"];
    return {};
  }
  throw new Error(`Unexpected request ${options.method ?? "GET"} ${path}`);
};

const source = (await readFile(process.argv[2], "utf8")).replace(
  'import { adminFetch } from "../api.js";',
  "const adminFetch = globalThis.testAdminFetch;",
);
const { createSchedules } = await import(`data:text/javascript;base64,${Buffer.from(source).toString("base64")}`);

globalThis.document = { createElement: (tag) => new Element(tag) };
const container = new Element("div");
const page = createSchedules(container);
await page.refresh();
const group = container.querySelector('[data-area="groups"]').children[0];
const message = group.querySelector('[data-field="text"]');
const time = group.querySelector('[data-field="time"]');
assert.equal(message.value, "saved text");
message.value = "draft retained after a failed save";
time.value = "12:30";
await message.emit("input");
await time.emit("input");
const scheduleForm = group.querySelector('[data-form="schedule"]');
await scheduleForm.emit("submit", { submitter: { dataset: { action: "save-schedule" } } });
await page.refresh();
assert.equal(message.value, "draft retained after a failed save");
assert.equal(time.value, "12:30");

const deleteButton = group.querySelector('[data-action="delete-schedule"]');
assert.equal(deleteButton.disabled, false);
const proactive = group.querySelector('[data-field="proactive"]');
proactive.checked = true;
await proactive.emit("change");
await group.querySelector('[data-form="proactive"]').emit("submit");
assert.equal(deleteButton.disabled, false, "saving the group toggle must leave the saved schedule deletable");
console.log("Schedule drafts survive save failure and Delete remains enabled after group toggle");
