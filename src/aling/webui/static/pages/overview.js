import { adminFetch } from "../api.js";

const time = (value) => value ? new Date(value).toLocaleString("zh-CN") : "尚无记录";
const truth = (value) => value === true ? "是" : value === false ? "否" : "未知";
const diagnosticNames = {
  status_failed: "QQ 状态查询失败", groups_failed: "群列表查询失败",
  request_failed: "模型请求失败", tool_turn_failed: "模型工具调用失败",
  summary_failed: "网页摘要失败", read_failed: "读取失败", save_failed: "保存失败",
};
export function createOverview(container) {
  container.innerHTML = `
    <div class="status-grid">
      <section class="card status-card"><h2>阿零进程</h2><p class="status-value" data-value="process"></p><p class="muted" data-value="version"></p><p class="help" data-value="started"></p></section>
      <section class="card status-card"><h2>OneBot 连接</h2><p class="status-value" data-value="connection"></p><p class="help">连接状态独立于 QQ 账号在线状态。</p><p class="help" data-value="event"></p></section>
      <section class="card status-card"><h2>QQ 状态</h2><p data-value="qq"></p><p class="help" data-value="queried"></p><button class="primary" data-action="qq" type="button">刷新 QQ 状态</button></section>
    </div>
    <p class="error" data-value="error" role="alert" hidden></p>
    <section class="card section-card"><h2>受控联网</h2><p data-value="search"></p><p class="help">联网服务的详细诊断与来源策略请查看“联网设置”。</p></section>
    <section class="card section-card"><h2>模型与人格</h2><p data-value="model"></p><p class="muted" data-value="persona"></p><p class="help">此处仅显示配置状态，模型和人格配置由部署者维护。</p></section>
    <section class="card section-card"><h2>最近诊断</h2><p class="help">固定错误类别，最多保留 100 条、24 小时。不会显示聊天内容或原始日志。</p><ul class="diagnostic-list" data-value="diagnostics"></ul></section>`;
  const field = (name) => container.querySelector(`[data-value="${name}"]`);
  let loading = false;
  async function refresh() {
    if (loading) return;
    loading = true;
    try {
      const data = await adminFetch("/overview");
      field("process").textContent = data.process_available ? "运行中" : "未知";
      field("version").textContent = `版本 ${data.version}`;
      field("started").textContent = `启动于 ${time(data.started_at)}`;
      field("connection").textContent = data.onebot_connected ? "已连接" : "未连接";
      field("event").textContent = `最近事件 ${time(data.last_event_at)}`;
      field("qq").textContent = `在线：${truth(data.qq.online)} · 健康：${truth(data.qq.good)}`;
      field("queried").textContent = `查询时间 ${time(data.qq.queried_at)}；结果 30 秒后显示未知。`;
      field("model").textContent = data.model.configured ? `已配置 · ${data.model.name}` : "尚未配置模型";
      field("persona").textContent = data.persona.configured ? "人格指令已配置" : "人格指令未配置";
      const search = await adminFetch("/search-status");
      field("search").textContent = search.last_verified
        ? `服务 ${search.internal_reachable === true ? "可达" : search.internal_reachable === false ? "不可达" : "未知"} · JSON ${search.json_enabled === true ? "可用" : search.json_enabled === false ? "未启用" : "未知"} · 最近检查 ${time(search.last_verified)}`
        : "尚无联网服务探测记录";
      const list = field("diagnostics");
      list.replaceChildren();
      for (const item of data.diagnostics) {
        const row = document.createElement("li");
        row.textContent = `${time(item.at)} · ${item.component} · ${diagnosticNames[item.code] ?? "服务失败"}${item.exception_type ? ` (${item.exception_type})` : ""}`;
        list.append(row);
      }
      if (!data.diagnostics.length) {
        const row = document.createElement("li"); row.textContent = "暂无诊断记录"; list.append(row);
      }
      field("error").hidden = true;
    } catch (error) { field("error").textContent = error.message; field("error").hidden = false; }
    finally { loading = false; }
  }
  container.querySelector('[data-action="qq"]').addEventListener("click", async (event) => {
    const button = event.currentTarget; button.disabled = true;
    try { await adminFetch("/overview/refresh-qq", { method: "POST" }); await refresh(); }
    catch (error) { field("error").textContent = error.message; field("error").hidden = false; }
    finally { button.disabled = false; }
  });
  return { refresh };
}
