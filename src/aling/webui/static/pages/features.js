import { adminFetch } from "../api.js";

const names = { ai: "AI 聊天", web: "网页读取", meme: "表情包", memory: "短期记忆" };
const reasons = { global_off: "全局开关已关闭", group_off: "本群开关已关闭", ai_credentials_missing: "模型凭据尚未配置", global_ai_off: "全局 AI 已关闭", group_ai_off: "本群 AI 已关闭", scope_restricted: "本群不在允许范围" };
export function createFeatures(container) {
  container.innerHTML = `
    <section class="card section-card"><h2>全局功能</h2><p class="help">全局开关决定功能是否可供群启用。保存状态与实际生效状态分别显示。</p><div data-area="global"></div></section>
    <section class="card section-card"><div class="card-heading"><h2>群与功能</h2><button type="button" class="secondary" data-action="groups">从 QQ 刷新群列表</button></div>
      <label for="feature-group">选择群</label><select id="feature-group"><option value="">请选择群</option></select>
      <div class="pagination"><button class="secondary" type="button" data-action="previous">上一页</button><span class="help" data-area="page"></span><button class="secondary" type="button" data-action="next">下一页</button></div>
      <p class="help" data-area="groups-info"></p><div data-area="group"></div></section>
    <p data-area="feedback" class="notice" role="status" hidden></p>
    <dialog class="memory-dialog"><h2>启用本群短期记忆</h2><p data-area="notice"></p><p class="help">票据有效 60 秒。确认后保存，内容可用 /forget 清除。</p><p data-area="confirmation-error" class="error" role="alert" hidden></p><div class="actions"><button class="secondary" type="button" data-action="cancel">取消</button><button class="primary" type="button" data-action="confirm">我已了解，启用记忆</button></div></dialog>`;
  const area = (name) => container.querySelector(`[data-area="${name}"]`);
  const action = (name) => container.querySelector(`[data-action="${name}"]`);
  const select = container.querySelector("select");
  const dialog = container.querySelector("dialog");
  const rows = new Map();
  let selected = "", offset = 0, total = 0, loading = false, refreshPending = false, confirmation = null;
  function feedback(message, failed = false) {
    area("feedback").textContent = message; area("feedback").hidden = false;
    area("feedback").classList.toggle("error", failed);
  }
  function makeRows(scope, target) {
    target.replaceChildren();
    for (const [feature, name] of Object.entries(names)) {
      const row = document.createElement("form"); row.className = "feature-row";
      const description = document.createElement("div");
      const title = document.createElement("h3"); title.textContent = name;
      const state = document.createElement("p"); state.className = "help";
      const blocked = document.createElement("p"); blocked.className = "help";
      description.append(title, state, blocked);
      const controls = document.createElement("div"); controls.className = "feature-controls";
      const label = document.createElement("label");
      const input = document.createElement("input"); input.type = "checkbox";
      label.append(input, document.createTextNode("请求开启"));
      const button = document.createElement("button"); button.type = "submit"; button.className = "secondary"; button.textContent = "保存";
      controls.append(label, button); row.append(description, controls); target.append(row);
      const item = { input, button, state, blocked, dirty: false, busy: false, value: null };
      rows.set(`${scope}:${feature}`, item);
      input.addEventListener("change", () => { item.dirty = input.checked !== item.value; });
      row.addEventListener("submit", async (event) => {
        event.preventDefault(); if (item.busy) return;
        item.busy = true; button.disabled = true; input.disabled = true;
        const group = selected;
        try {
          if (scope === "group" && feature === "memory" && input.checked) {
            const consent = await adminFetch(`/groups/${group}/memory-confirmation`, { method: "POST" });
            confirmation = { ticket: consent.ticket, group, item };
            area("notice").textContent = consent.notice;
            area("confirmation-error").hidden = true;
            dialog.showModal();
          } else {
            const path = scope === "global" ? `/features/global/${feature}` : `/groups/${group}/features/${feature}`;
            update(scope, feature, await adminFetch(path, { method: "PUT", body: { enabled: input.checked } }), true);
            feedback(`${name}已保存`); await refresh();
          }
        } catch (error) { feedback(error.message, true); }
        finally { item.busy = false; button.disabled = false; input.disabled = false; }
      });
    }
  }
  function update(scope, feature, data, saved = false) {
    const item = rows.get(`${scope}:${feature}`); if (!item) return;
    const requested = scope === "global" ? data.global_requested : data.group_requested;
    item.value = requested;
    item.state.textContent = `保存：${requested ? "开启" : "关闭"} · 实际：${data.effective ? "开启" : "受限 / 关闭"}`;
    item.blocked.textContent = data.blocked_by.length ? data.blocked_by.map((code) => reasons[code] ?? "功能受限").join("；") : "当前已生效";
    if (saved) item.dirty = false;
    if (!item.dirty && !item.busy) item.input.checked = requested;
  }
  makeRows("global", area("global"));
  async function loadGroups() {
    const data = await adminFetch(`/groups?offset=${offset}&limit=100`);
    total = data.total;
    select.replaceChildren(new Option("请选择群", ""));
    for (const group of data.items) {
      select.add(new Option(`${group.name ?? "名称未查询"} · ${group.group_id}${group.allowed ? "" : " · 未开放"}`, String(group.group_id)));
    }
    if (selected && !data.items.some((group) => String(group.group_id) === selected)) select.add(new Option(`已选群 ${selected}`, selected));
    select.value = selected;
    area("page").textContent = `${total ? offset + 1 : 0}–${Math.min(offset + 100, total)} / ${total} 群`;
    action("previous").disabled = offset === 0; action("next").disabled = offset + 100 >= total;
    area("groups-info").textContent = `${data.queried_at ? `QQ 快照：${new Date(data.queried_at).toLocaleString("zh-CN")}` : "尚未查询 QQ；当前列出已保存群和允许群"}${data.truncated ? "。QQ 快照已截断为前 1000 条" : ""}`;
  }
  async function refresh() {
    if (loading) { refreshPending = true; return; }
    loading = true;
    const group = selected;
    try {
      const global = await adminFetch("/features/global");
      for (const [feature, state] of Object.entries(global)) update("global", feature, state);
      await loadGroups();
      if (group) {
        const data = await adminFetch(`/groups/${group}/features`);
        if (group === selected) for (const [feature, state] of Object.entries(data)) update("group", feature, state);
      }
    } catch (error) { feedback(error.message, true); }
    finally {
      loading = false;
      if (refreshPending) { refreshPending = false; refresh(); }
    }
  }
  select.addEventListener("change", () => {
    selected = select.value;
    if (selected) makeRows("group", area("group")); else area("group").replaceChildren();
    refresh();
  });
  for (const [name, direction] of [["previous", -1], ["next", 1]]) action(name).addEventListener("click", async () => {
    offset = Math.max(0, offset + direction * 100);
    try { await loadGroups(); } catch (error) { feedback(error.message, true); }
  });
  action("groups").addEventListener("click", async () => {
    action("groups").disabled = true;
    try { await adminFetch("/groups/refresh", { method: "POST" }); offset = 0; await loadGroups(); feedback("群列表已刷新"); }
    catch (error) { feedback(error.message, true); }
    finally { action("groups").disabled = false; }
  });
  action("cancel").addEventListener("click", () => dialog.close());
  dialog.addEventListener("close", () => { confirmation = null; });
  action("confirm").addEventListener("click", async () => {
    if (!confirmation) return;
    const current = confirmation; action("confirm").disabled = true;
    try {
      const data = await adminFetch(`/groups/${current.group}/memory-confirmation/confirm`, { method: "POST", body: { ticket: current.ticket } });
      if (selected === current.group) update("group", "memory", data, true);
      dialog.close(); feedback("短期记忆已保存"); await refresh();
    } catch (error) { area("confirmation-error").textContent = error.message; area("confirmation-error").hidden = false; }
    finally { action("confirm").disabled = false; }
  });
  document.addEventListener("panel-session-expired", () => {
    if (dialog.open) dialog.close();
  });
  return { refresh };
}
