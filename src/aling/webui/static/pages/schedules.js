import { adminFetch } from "../api.js";

const reasonLabels = {
  global_off: "全局主动发言开关已关闭",
  group_off: "本群主动发言开关已关闭",
  scope_restricted: "本群不在允许范围",
  onebot_disconnected: "OneBot 当前未连接",
};

export function createSchedules(container) {
  container.innerHTML = `
    <section class="card section-card"><h2>部署发送条件</h2><div data-area="conditions" class="schedule-conditions"></div></section>
    <section class="card section-card"><div class="card-heading"><h2>群定时计划</h2><button class="secondary" type="button" data-action="refresh">刷新</button></div>
      <p class="help">计划按配置时区每天最多发送一次，并遵循全局及本群开关。保存或删除计划不会重置已有的当日发送声明。</p>
      <div data-area="groups"></div><p data-area="empty" class="help" hidden>当前没有可管理的群。允许范围内的群会显示在这里。</p></section>
    <p data-area="feedback" class="notice" role="status" hidden></p>`;

  const area = (name) => container.querySelector(`[data-area="${name}"]`);
  const groupViews = new Map();

  function feedback(element, message, failed = false) {
    element.textContent = message;
    element.classList.toggle("error", failed);
    element.hidden = !message;
  }

  function createGroup(groupId) {
    const card = document.createElement("article");
    card.className = "card schedule-card";
    card.innerHTML = `
      <div class="card-heading"><h3>群 <span data-field="group"></span></h3><p data-field="state" class="help"></p></div>
      <p data-field="reasons" class="help"></p>
      <form data-form="schedule" class="schedule-form">
        <div class="schedule-fields">
          <div><label for="schedule-time-${groupId}">每日发送时间</label><input id="schedule-time-${groupId}" data-field="time" type="time" required></div>
          <div class="schedule-message"><label for="schedule-text-${groupId}">定时消息</label><textarea id="schedule-text-${groupId}" data-field="text" rows="3" minlength="1" maxlength="500" required></textarea><p class="help">1–500 字</p></div>
        </div>
        <p data-field="attempt" class="help"></p>
        <p data-field="schedule-error" class="error" role="alert" hidden></p>
        <div class="actions"><button class="primary" type="submit" data-action="save-schedule">保存计划</button><button class="secondary" type="submit" data-action="delete-schedule">删除计划</button></div>
      </form>
      <form data-form="proactive" class="proactive-form"><label><input data-field="proactive" type="checkbox"> 允许本群主动发言</label><p data-field="proactive-error" class="error" role="alert" hidden></p><button class="secondary" type="submit">保存群开关</button></form>`;
    const view = {
      card,
      time: card.querySelector('[data-field="time"]'),
      text: card.querySelector('[data-field="text"]'),
      proactive: card.querySelector('[data-field="proactive"]'),
      scheduleError: card.querySelector('[data-field="schedule-error"]'),
      proactiveError: card.querySelector('[data-field="proactive-error"]'),
      scheduleDirty: false,
      proactiveDirty: false,
      busy: false,
      hasSchedule: false,
    };
    card.querySelector('[data-field="group"]').textContent = groupId;
    view.time.addEventListener("input", () => { view.scheduleDirty = true; });
    view.text.addEventListener("input", () => { view.scheduleDirty = true; });
    view.proactive.addEventListener("change", () => { view.proactiveDirty = true; });
    card.querySelector('[data-form="schedule"]').addEventListener("submit", async (event) => {
      event.preventDefault();
      if (view.busy) return;
      const deleting = event.submitter?.dataset.action === "delete-schedule";
      if (!deleting && !event.currentTarget.reportValidity()) return;
      view.busy = true;
      const controls = [...event.currentTarget.querySelectorAll("button, input, textarea")];
      controls.forEach((control) => { control.disabled = true; });
      feedback(view.scheduleError, "");
      try {
        await adminFetch(`/groups/${groupId}/schedule`, deleting
          ? { method: "DELETE" }
          : { method: "PUT", body: { hour: Number(view.time.value.slice(0, 2)), minute: Number(view.time.value.slice(3, 5)), text: view.text.value } });
        view.scheduleDirty = false;
        feedback(area("feedback"), deleting ? `群 ${groupId} 的计划已删除` : `群 ${groupId} 的计划已保存`);
        await refresh();
      } catch (error) {
        feedback(view.scheduleError, error.message, true);
      } finally {
        view.busy = false;
        controls.forEach((control) => { control.disabled = false; });
        view.card.querySelector('[data-action="delete-schedule"]').disabled = !view.hasSchedule;
      }
    });
    card.querySelector('[data-form="proactive"]').addEventListener("submit", async (event) => {
      event.preventDefault();
      if (view.busy) return;
      view.busy = true;
      const controls = [...event.currentTarget.querySelectorAll("button, input")];
      controls.forEach((control) => { control.disabled = true; });
      feedback(view.proactiveError, "");
      try {
        await adminFetch(`/groups/${groupId}/proactive`, { method: "PUT", body: { enabled: view.proactive.checked } });
        view.proactiveDirty = false;
        feedback(area("feedback"), `群 ${groupId} 的主动发言开关已保存`);
        await refresh();
      } catch (error) {
        feedback(view.proactiveError, error.message, true);
      } finally {
        view.busy = false;
        controls.forEach((control) => { control.disabled = false; });
        view.card.querySelector('[data-action="delete-schedule"]').disabled = !view.hasSchedule;
      }
    });
    area("groups").append(card);
    groupViews.set(groupId, view);
    return view;
  }

  function updateGroup(item) {
    const view = groupViews.get(item.group_id) ?? createGroup(item.group_id);
    const state = view.card.querySelector('[data-field="state"]');
    const reasons = view.card.querySelector('[data-field="reasons"]');
    const schedule = item.schedule;
    view.hasSchedule = Boolean(schedule);
    state.textContent = schedule ? (item.active ? "计划已保存 · 全局和本群开关已开启" : "计划已保存 · 当前未启用") : "尚无计划";
    state.classList.toggle("warning", Boolean(schedule && !item.active));
    reasons.textContent = schedule && item.inactive_reasons.length
      ? item.inactive_reasons.map((reason) => reasonLabels[reason] ?? "当前条件受限").join("；")
      : schedule ? "实际发送仍受安静时段、每日次数和小时限频约束。" : "";
    view.card.querySelector('[data-field="attempt"]').textContent = schedule?.last_attempted_date
      ? `最后尝试日期：${schedule.last_attempted_date}`
      : "尚无发送尝试记录";
    if (!view.scheduleDirty && !view.busy) {
      view.time.value = schedule ? `${String(schedule.hour).padStart(2, "0")}:${String(schedule.minute).padStart(2, "0")}` : "";
      view.text.value = schedule?.text ?? "";
    }
    if (!view.proactiveDirty && !view.busy) view.proactive.checked = item.proactive_enabled;
    view.card.querySelector('[data-action="delete-schedule"]').disabled = !schedule || view.busy;
  }

  async function refresh() {
    try {
      const data = await adminFetch("/schedules");
      const conditions = data.conditions;
      const globalState = conditions.global_enabled ? "已开启" : "已关闭";
      area("conditions").textContent = `全局主动发言：${globalState} · 时区：${conditions.timezone} · 安静时段：${conditions.quiet_start}–${conditions.quiet_end} · 小时限频：${conditions.max_per_hour} 次 · 每日上限：${conditions.max_per_day} 次 · 冷却：${conditions.cooldown_seconds} 秒 · OneBot：${conditions.onebot_connected ? "已连接" : "未连接"}`;
      for (const item of data.items) updateGroup(item);
      area("empty").hidden = data.items.length > 0;
      return data;
    } catch (error) {
      feedback(area("feedback"), error.message, true);
      return null;
    }
  }

  container.querySelector('[data-action="refresh"]').addEventListener("click", refresh);
  void refresh();
  return { refresh };
}
