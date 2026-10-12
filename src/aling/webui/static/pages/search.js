import { adminFetch } from "../api.js";

const purposes = [
  ["technical", "技术资料与公开软件项目"],
  ["ordinary_meme", "普通静态表情"],
  ["gaming", "游戏"],
  ["hobby", "兴趣"],
  ["practical", "日常实用信息"],
];
const purposeNames = Object.fromEntries(purposes);
const limits = [
  ["task_seconds", "任务总秒数", 60], ["work_seconds", "外部工作秒数", 57],
  ["max_tool_rounds", "工具轮数", 3], ["max_model_requests", "模型请求数", 5],
  ["max_external_operations", "外部操作数", 4], ["max_searches", "搜索次数", 1],
  ["max_results", "结果条数", 3], ["request_timeout_seconds", "单次请求秒数", 10],
  ["instance_concurrency", "实例并发数", 2], ["group_concurrency", "单群并发数", 1],
  ["user_cooldown_seconds", "用户冷却秒数", 15], ["user_hourly_limit", "每用户每小时", 10],
  ["group_hourly_limit", "每群每小时", 30], ["instance_hourly_limit", "每实例每小时", 100],
];
const codes = {
  success: "已取得获准来源的搜索结果", no_results: "服务可达，但没有返回结果",
  service_unreachable: "内部搜索服务不可达", json_disabled: "SearXNG JSON 接口未启用",
  upstream_failed: "搜索服务返回无效结果", budget_exceeded: "探测达到时间或操作上限",
  rate_limited: "探测额度已用尽", classification_unavailable: "用途检查不可用",
  intent_uncertain: "用途检查未能确认", topic_not_allowed: "当前策略不允许探测用途",
  source_not_allowed: "当前没有匹配的批准来源", policy_changed: "探测期间策略发生变化",
};
const node = (tag, cls = "", text = "") => {
  const element = document.createElement(tag);
  if (cls) element.className = cls;
  element.textContent = text;
  return element;
};

export function createSearch(container) {
  container.innerHTML = `
    <p class="notice" data-area="notice" role="status" hidden></p>
    <p class="error" data-area="error" role="alert" hidden></p>
    <section class="card section-card"><h2>外部数据流确认</h2><p class="help">用途检查会把本次检索文字发送给已配置的模型服务；获准关键词会发送到自托管 SearXNG，并可能转交其上游搜索引擎；获准的标题、摘要和来源会返回群聊或发送给模型服务。现有模型服务可能产生费用。联网功能默认关闭。</p><p data-area="consent"></p><button class="secondary" data-action="consent" type="button">确认已阅读并了解数据流</button></section>
    <section class="card section-card"><h2>全局功能与模型工具</h2><p class="help">只有部署者可更改。联网功能还需要 AI、搜索服务、获准来源和上面的独立确认。</p><div data-area="switches"></div><div class="feature-row"><div><h3>AI 工具</h3><p class="help">允许 AI 在当前会话中选择已开放的受控工具。</p></div><div class="feature-controls"><label><input type="checkbox" data-input="ai-tools"> 启用</label><button class="secondary" data-action="ai-tools" type="button">保存</button></div></div></section>
    <section class="card section-card"><h2>部署者允许范围与来源</h2><p class="help">全局设置定义上限。在线图片来源页和实际图片/CDN 主机必须分别批准。只填写准确主机名和必要路径。</p><form data-form="policy"><div data-area="purposes"></div><div data-area="sources"></div><div class="search-source-add" data-area="source-form"></div><h3>搜索额度上限</h3><div class="search-limits" data-area="limits"></div><button class="primary" type="submit">保存全局范围、来源与额度</button></form></section>
    <section class="card section-card"><h2>群级进一步收紧</h2><p class="help">群设置只能减少部署者允许的用途、来源和额度。此处同时显示功能依赖的实际状态。</p><label for="search-group">选择已开放的群</label><select id="search-group"><option value="">请选择群</option></select><div data-area="group-policy"></div><div data-area="group-features"></div><button class="primary" data-action="save-group" type="button" disabled>保存本群限制</button><button class="secondary" data-action="probe" type="button" disabled>运行安全探测</button><p data-area="probe" class="help" role="status"></p></section>
    <section class="card section-card"><h2>服务状态</h2><p data-area="status">正在读取…</p><button class="secondary" data-action="refresh" type="button">刷新状态</button></section>
    <dialog class="memory-dialog" data-dialog="consent"><h2>确认联网数据流</h2><p>本次检索文字会先发送给模型服务进行用途分类，获准的关键词会发送到自托管 SearXNG 及其上游搜索引擎。搜索结果可能再次发送给模型服务；原有模型服务可能产生费用。查询不保存在面板中。请仅为已审核的用途与来源启用联网。</p><label><input type="checkbox" data-input="consent-check"> 我已阅读并了解这些外部数据流与可能费用</label><div class="actions"><button class="secondary" type="button" data-action="cancel-consent">取消</button><button class="primary" type="button" data-action="confirm-consent" disabled>确认</button></div></dialog>`;

  const area = (name) => container.querySelector(`[data-area="${name}"]`);
  const action = (name) => container.querySelector(`[data-action="${name}"]`);
  const input = (name) => container.querySelector(`[data-input="${name}"]`);
  const groupSelect = container.querySelector("#search-group");
  const consentDialog = container.querySelector('[data-dialog="consent"]');
  const policyForm = container.querySelector('[data-form="policy"]');
  let allowedGroups = [];
  let policy = null;
  let groupPolicy = null;
  let consent = false;
  let policyDirty = false;
  let groupDirty = false;
  let selectedGroup = "";
  let pendingFeature = null;
  let loading = false;

  function message(text, failed = false) {
    area(failed ? "error" : "notice").textContent = text;
    area(failed ? "error" : "notice").hidden = false;
    if (failed) area("notice").hidden = true;
  }
  function purposeChecks(target, selected, prefix) {
    target.replaceChildren();
    for (const [value, labelText] of purposes) {
      const label = node("label", "search-check");
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.name = prefix;
      checkbox.value = value;
      checkbox.checked = selected.includes(value);
      label.append(checkbox, document.createTextNode(labelText));
      target.append(label);
    }
  }
  function renderSources() {
    const target = area("sources");
    target.replaceChildren(node("h3", "", `已批准来源（${policy.sources.length}）`));
    if (!policy.sources.length) target.append(node("p", "help warning", "来源列表为空，联网检索会被拒绝。"));
    for (const source of policy.sources) {
      const row = node("div", "search-source");
      const text = node("p", "", `${source.source_id} · ${purposeNames[source.purpose] ?? source.purpose} · ${source.scheme}://${source.host}${source.path_prefix === "/" ? "" : source.path_prefix} · ${source.kind === "image" ? "图片/CDN" : "页面"}`);
      const remove = node("button", "secondary", "移除");
      remove.type = "button";
      remove.addEventListener("click", () => {
        policy.sources = policy.sources.filter((item) => item.source_id !== source.source_id);
        policyDirty = true;
        renderSources();
      });
      row.append(text, remove); target.append(row);
    }
  }
  function renderLimits(target, values, prefix = "", ceilings = null) {
    target.replaceChildren();
    for (const [key, labelText, maximum] of limits) {
      const label = node("label", "search-limit", labelText);
      const field = node("input"); field.type = "number"; field.min = key === "user_cooldown_seconds" ? "15" : "1"; field.max = String(ceilings?.[key] ?? maximum); field.step = "1"; field.value = values[key]; field.name = `${prefix}${key}`;
      field.addEventListener("input", () => { if (prefix) groupDirty = true; else policyDirty = true; });
      label.append(field); target.append(label);
    }
  }
  function readLimits(target, prefix = "") {
    return Object.fromEntries(limits.map(([key]) => [key, Number(target.querySelector(`[name="${prefix}${key}"]`).value)]));
  }
  async function savePolicy(event) {
    event.preventDefault();
    const purposesSelected = [...area("purposes").querySelectorAll("input:checked")].map((field) => field.value);
    try {
      policy = await adminFetch("/search-policy", { method: "PUT", body: {
        allowed_purposes: purposesSelected,
        sources: policy.sources,
        limits: readLimits(policyForm),
      } });
      policyDirty = false; message("全局搜索策略已保存，旧任务与在线临时素材已撤销。"); await refresh();
    } catch (error) { message(error.message, true); }
  }
  function addSourceForm() {
    const target = area("source-form"); target.replaceChildren(node("h3", "", "添加来源"));
    const form = node("div", "search-source-form");
    const fields = {};
    for (const [name, labelText, placeholder] of [
      ["source_id", "来源 ID", "python-docs"], ["host", "准确主机名", "docs.python.org"], ["path_prefix", "路径前缀", "/3/"],
    ]) {
      const label = node("label", "", labelText); const field = node("input"); field.maxLength = name === "host" ? 253 : name === "source_id" ? 64 : 500; field.placeholder = placeholder; field.required = name !== "path_prefix"; field.value = name === "path_prefix" ? "/" : ""; field.addEventListener("input", () => { policyDirty = true; }); label.append(field); form.append(label); fields[name] = field;
    }
    const purposeLabel = node("label", "", "用途"); const purpose = node("select");
    for (const [value, text] of purposes) purpose.add(new Option(text, value)); purposeLabel.append(purpose); form.append(purposeLabel);
    const kindLabel = node("label", "", "来源类型"); const kind = node("select"); kind.add(new Option("页面", "page")); kind.add(new Option("图片/CDN", "image")); kindLabel.append(kind); form.append(kindLabel);
    const add = node("button", "secondary", "加入来源列表"); add.type = "button";
    add.addEventListener("click", () => {
      const sourceId = fields.source_id.value.trim(); const host = fields.host.value.trim(); const path = fields.path_prefix.value.trim() || "/";
      if (!sourceId || !host || policy.sources.some((source) => source.source_id === sourceId)) { message("来源 ID 和主机名必填，来源 ID 不能重复。", true); return; }
      if (kind.value === "image" && purpose.value !== "ordinary_meme") { message("图片/CDN 来源只能用于普通表情。", true); return; }
      policy.sources.push({ source_id: sourceId, host, path_prefix: path, purpose: purpose.value, kind: kind.value, scheme: "https", port: null, allowed_query_params: [] });
      policyDirty = true; message("来源已加入待保存列表。"); renderSources();
      fields.source_id.value = ""; fields.host.value = ""; fields.path_prefix.value = "/";
    });
    target.append(form, add);
  }
  function renderGroup() {
    const target = area("group-policy"); target.replaceChildren();
    if (!groupPolicy) { target.append(node("p", "help", "选择群后查看和收紧策略。")); return; }
    const allowed = groupPolicy.allowed_purposes ?? policy.allowed_purposes;
    const box = node("div", "search-purpose-list"); purposeChecks(box, allowed, "group-purpose"); box.addEventListener("change", () => { groupDirty = true; });
    target.append(node("h3", "", "本群可用用途"), box);
    const sourceBox = node("div", "search-check-list");
    const selectedSources = groupPolicy.source_ids ?? policy.sources.map((source) => source.source_id);
    for (const source of policy.sources) {
      const label = node("label", "search-check"); const check = node("input"); check.type = "checkbox"; check.name = "group-source"; check.value = source.source_id; check.checked = selectedSources.includes(source.source_id); check.addEventListener("change", () => { groupDirty = true; }); label.append(check, document.createTextNode(`${source.source_id} · ${source.host}`)); sourceBox.append(label);
    }
    target.append(node("h3", "", "本群可用来源"), sourceBox);
    const modeLabel = node("label", "", "表情包模式"); const mode = node("select"); mode.name = "meme-mode";
    for (const [value, text] of [["local", "仅本地"], ["auto", "本地优先，允许后再联网"], ["online", "仅联网"]]) mode.add(new Option(text, value)); mode.value = groupPolicy.meme_mode; mode.addEventListener("change", () => { groupDirty = true; }); modeLabel.append(mode); target.append(modeLabel);
    target.append(node("h3", "", "本群额度上限"));
    const groupLimits = node("div", "search-limits"); renderLimits(groupLimits, groupPolicy.limits ?? policy.limits, "group-", policy.limits); target.append(groupLimits);
  }
  async function loadGroup() {
    const group = groupSelect.value;
    action("save-group").disabled = !group;
    action("probe").disabled = !group;
    if (!group) { groupPolicy = null; area("group-features").replaceChildren(); renderGroup(); return; }
    const [nextPolicy, featureStates] = await Promise.all([
      adminFetch(`/groups/${group}/search-policy`), adminFetch(`/groups/${group}/features`),
    ]);
    groupPolicy = nextPolicy;
    renderGroup();
    const details = area("group-features"); details.replaceChildren(node("h3", "", "功能依赖状态"));
    for (const key of ["ai", "web", "meme", "search", "online_meme"]) {
      const state = featureStates[key]; const row = node("p", "help");
      const blockers = state.blocked_by.map((code) => ({
        global_off: "全局关闭", group_off: "本群关闭", ai_credentials_missing: "缺少模型凭据",
        global_ai_off: "全局 AI 关闭", group_ai_off: "本群 AI 关闭", global_meme_off: "全局表情包关闭",
        group_meme_off: "本群表情包关闭", search_consent_required: "未确认数据流", scope_restricted: "群未开放",
      }[code] ?? code));
      row.textContent = `${{ ai: "AI", web: "网页读取", meme: "表情包", search: "联网搜索", online_meme: "在线表情" }[key]}：${state.effective ? "已生效" : blockers.join("、") || "未生效"}`;
      details.append(row);
    }
  }
  async function saveGroup() {
    const group = groupSelect.value; if (!group || !groupPolicy) return;
    const allowed = [...area("group-policy").querySelectorAll('input[name="group-purpose"]:checked')].map((field) => field.value);
    const sources = [...area("group-policy").querySelectorAll('input[name="group-source"]:checked')].map((field) => field.value);
    const mode = area("group-policy").querySelector('[name="meme-mode"]').value;
    try {
      groupPolicy = await adminFetch(`/groups/${group}/search-policy`, { method: "PUT", body: { allowed_purposes: allowed, source_ids: sources, meme_mode: mode, limits: readLimits(area("group-policy"), "group-") } });
      groupDirty = false; message("本群限制已保存，旧任务与临时在线素材已撤销。"); await loadGroup();
    } catch (error) { message(error.message, true); }
  }
  async function refreshStatus() {
    const status = await adminFetch("/search-status");
    area("status").textContent = `配置：${status.configured ? "已配置" : "未配置"} · 内部服务：${status.internal_reachable === null ? "未知" : status.internal_reachable ? "可达" : "不可达"} · JSON：${status.json_enabled === null ? "未知" : status.json_enabled ? "已启用" : "未启用"} · 上游结果：${status.upstream_results === null ? "未知" : status.upstream_results ? `${status.result_count} 条` : "无结果"} · 最近验证：${status.last_verified ? new Date(status.last_verified).toLocaleString("zh-CN") : "从未"} · ${codes[status.code] ?? "状态未知"}${status.exception_type ? ` · ${status.exception_type}` : ""}`;
  }
  async function refresh() {
    if (loading || policyDirty || groupDirty) return;
    loading = true;
    try {
      const [nextPolicy, groups, globalFeatures, aiTools] = await Promise.all([
        adminFetch("/search-policy"), adminFetch("/groups?limit=100"), adminFetch("/features/global"), adminFetch("/runtime-settings/ai-tools"),
      ]);
      policy = nextPolicy; consent = Boolean(policy.consent_acknowledged); allowedGroups = groups.items.filter((group) => group.allowed);
      area("consent").textContent = consent ? "已确认数据流；此确认保存在本地配置中。" : "尚未确认。搜索和在线表情无法启用。";
      action("consent").hidden = consent;
      input("ai-tools").checked = aiTools.enabled;
      area("switches").replaceChildren();
      for (const key of ["ai", "web", "meme", "search", "online_meme"]) {
        const state = globalFeatures[key]; const row = node("form", "feature-row"); const description = node("div");
        const title = node("h3", "", { ai: "AI", web: "网页读取", meme: "表情包", search: "受控联网搜索", online_meme: "在线表情" }[key]);
        const blockedNames = { global_off: "全局关闭", group_off: "群内关闭", ai_credentials_missing: "缺少模型凭据", global_ai_off: "全局 AI 关闭", group_ai_off: "本群 AI 关闭", global_meme_off: "全局表情包关闭", group_meme_off: "本群表情包关闭", search_consent_required: "尚未确认联网数据流", scope_restricted: "群未开放" };
        const help = node("p", "help", `实际状态：${state.effective ? "已生效" : state.blocked_by.length ? state.blocked_by.map((code) => blockedNames[code] ?? code).join("、") : "关闭"}`); description.append(title, help);
        const controls = node("div", "feature-controls"); const label = node("label"); const check = node("input"); check.type = "checkbox"; check.checked = state.global_requested; label.append(check, document.createTextNode("全局请求开启")); const button = node("button", "secondary", "保存"); button.type = "submit"; controls.append(label, button); row.append(description, controls); area("switches").append(row);
        row.addEventListener("submit", async (event) => {
          event.preventDefault(); button.disabled = true;
          try {
            if (check.checked && ["search", "online_meme"].includes(key) && !consent) {
              pendingFeature = key;
              input("consent-check").checked = false; action("confirm-consent").disabled = true; consentDialog.showModal();
              return;
            }
            await adminFetch(`/features/global/${key}`, { method: "PUT", body: { enabled: check.checked } }); message("全局功能状态已保存。"); await refresh();
          } catch (error) { message(error.message, true); }
          finally { button.disabled = false; }
        });
      }
      purposeChecks(area("purposes"), policy.allowed_purposes, "global-purpose");
      area("purposes").className = "search-purpose-list";
      area("purposes").addEventListener("change", () => { policyDirty = true; });
      renderSources(); addSourceForm(); renderLimits(area("limits"), policy.limits);
      groupSelect.replaceChildren(new Option("请选择群", ""));
      for (const group of allowedGroups) groupSelect.add(new Option(`${group.name ?? "群"} · ${group.group_id}`, String(group.group_id)));
      groupSelect.value = allowedGroups.some((group) => String(group.group_id) === selectedGroup) ? selectedGroup : "";
      selectedGroup = groupSelect.value;
      if (!allowedGroups.length) area("group-policy").replaceChildren(node("p", "help", "当前没有已开放的群可用于设置或探测。"));
      else if (groupSelect.value) await loadGroup();
      await refreshStatus();
      area("error").hidden = true;
    } catch (error) { message(error.message, true); }
    finally { loading = false; }
  }

  policyForm.addEventListener("input", () => { policyDirty = true; });
  policyForm.addEventListener("submit", savePolicy);
  groupSelect.addEventListener("change", () => {
    const next = groupSelect.value;
    if (groupDirty && !window.confirm("切换群会放弃尚未保存的本群策略修改。继续？")) { groupSelect.value = selectedGroup; return; }
    groupDirty = false; selectedGroup = next; area("probe").textContent = ""; loadGroup().catch((error) => message(error.message, true));
  });
  action("save-group").addEventListener("click", saveGroup);
  action("probe").addEventListener("click", async () => {
    action("probe").disabled = true; area("probe").textContent = "正在通过当前群权限执行固定安全探测…";
    try {
      const result = await adminFetch("/search-probe", { method: "POST", body: { group_id: Number(groupSelect.value) } });
      area("probe").textContent = `探测成功 · ${result.category} · ${result.result_count} 条 · ${new Date(result.last_verified).toLocaleString("zh-CN")}`; await refreshStatus();
    } catch (error) { area("probe").textContent = `探测未成功：${error.message}`; await refreshStatus(); }
    finally { action("probe").disabled = !groupSelect.value; }
  });
  action("refresh").addEventListener("click", refreshStatus);
  action("consent").addEventListener("click", () => { input("consent-check").checked = false; action("confirm-consent").disabled = true; consentDialog.showModal(); });
  input("consent-check").addEventListener("change", () => { action("confirm-consent").disabled = !input("consent-check").checked; });
  action("cancel-consent").addEventListener("click", () => {
    pendingFeature = null;
    consentDialog.close();
  });
  action("confirm-consent").addEventListener("click", async () => {
    action("confirm-consent").disabled = true;
    try {
      await adminFetch("/search-consent", { method: "POST", body: { acknowledged: true } });
      consent = true;
      const enableAfterConsent = pendingFeature;
      pendingFeature = null;
      consentDialog.close();
      if (enableAfterConsent) {
        await adminFetch(`/features/global/${enableAfterConsent}`, {
          method: "PUT",
          body: { enabled: true },
        });
      }
      message("数据流确认已保存。联网仍需单独启用。");
      await refresh();
    }
    catch (error) { message(error.message, true); action("confirm-consent").disabled = false; }
  });
  action("ai-tools").addEventListener("click", async () => {
    const button = action("ai-tools"); button.disabled = true;
    try { const result = await adminFetch("/runtime-settings/ai-tools", { method: "PUT", body: { enabled: input("ai-tools").checked } }); input("ai-tools").checked = result.enabled; message("AI 工具开关已保存。"); }
    catch (error) { message(error.message, true); }
    finally { button.disabled = false; }
  });
  return { refresh };
}
