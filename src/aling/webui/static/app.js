import { api, setSession } from "./api.js";
import { createOverview } from "./pages/overview.js";
import { createFeatures } from "./pages/features.js";
import { createMemes } from "./pages/memes.js";
import { createSchedules } from "./pages/schedules.js";

const byId = (id) => document.getElementById(id);
const loginView = byId("login-view");
const panelView = byId("panel-view");
const loginKey = byId("login-key");
const loginError = byId("login-error");
const pages = {
  overview: ["概览", "阿零的管理入口，清楚查看每一项操作。", "管理面板已连接", "运行状态与最近事件将在后续版本接入。当前页面仅提供安全登录和导航，不显示尚未获取的数据。"],
  features: ["群与功能", "按群查看功能与依赖，操作结果由服务端确认。", "群与功能正在接入", "功能开关和群选择将在后续版本接入。当前页面尚未提供修改操作。"],
  memes: ["表情包", "管理阿零的本地素材与说明。", "素材管理正在接入", "素材列表、上传与编辑将在后续版本接入。当前页面尚未提供素材操作。"],
  schedules: ["定时消息", "查看计划和发言条件，编辑后由阿零按当前规则尝试发送。", "", ""],
};
let theme = "light";
try { if (localStorage.getItem("aling_panel_theme") === "dark") theme = "dark"; } catch { /* Theme still works when storage is unavailable. */ }
function applyTheme() {
  document.documentElement.dataset.theme = theme;
  document.querySelectorAll(".theme-toggle").forEach((button) => {
    button.textContent = theme === "dark" ? "切换浅色主题" : "切换深色主题";
  });
}
applyTheme();
document.querySelectorAll(".theme-toggle").forEach((button) => button.addEventListener("click", () => {
  theme = theme === "dark" ? "light" : "dark";
  applyTheme();
  try { localStorage.setItem("aling_panel_theme", theme); } catch { /* Optional preference. */ }
}));
function closeNav() {
  byId("sidebar").classList.remove("open");
  byId("nav-backdrop").hidden = true;
  byId("nav-toggle").setAttribute("aria-expanded", "false");
}
byId("nav-toggle").addEventListener("click", () => {
  const open = byId("sidebar").classList.toggle("open");
  byId("nav-backdrop").hidden = !open;
  byId("nav-toggle").setAttribute("aria-expanded", String(open));
  if (open) document.querySelector(".nav-item.selected").focus();
});
byId("nav-backdrop").addEventListener("click", () => { closeNav(); byId("nav-toggle").focus(); });
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && byId("sidebar").classList.contains("open")) {
    closeNav(); byId("nav-toggle").focus();
  }
});
let activePage = "overview";
const pageFactories = { overview: createOverview, features: createFeatures, memes: createMemes, schedules: createSchedules };
const pageInstances = new Map();
function openPage(page) {
  activePage = page;
  const [title, subtitle, heading, description] = pages[page];
  byId("page-title").textContent = title;
  byId("breadcrumb-page").textContent = title;
  byId("page-subtitle").textContent = subtitle;
  const factory = pageFactories[page];
  byId("placeholder").hidden = Boolean(factory);
  byId("placeholder-title").textContent = heading;
  byId("placeholder-description").textContent = description;
  if (factory && !pageInstances.has(page)) {
    const container = document.createElement("div");
    byId("page-content").append(container);
    pageInstances.set(page, { container, ...factory(container) });
  }
  for (const [name, instance] of pageInstances) instance.container.hidden = name !== page;
  document.querySelectorAll(".nav-item").forEach((item) => {
    const current = item.dataset.page === page;
    item.classList.toggle("selected", current);
    if (current) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current");
  });
  closeNav();
  if (!panelView.hidden && !document.hidden) pageInstances.get(page)?.refresh();
}
document.querySelectorAll(".nav-item").forEach((button) => button.addEventListener("click", () => {
  openPage(button.dataset.page); byId("main").focus();
}));
function refreshVisiblePage() {
  if (!panelView.hidden && !document.hidden) pageInstances.get(activePage)?.refresh();
}
setInterval(refreshVisiblePage, 15000);
document.addEventListener("visibilitychange", refreshVisiblePage);
function showLogin(message = "") {
  panelView.hidden = true;
  loginView.hidden = false;
  closeNav();
  loginError.textContent = message;
  loginError.hidden = !message;
  loginKey.focus();
}
function showPanel(session) {
  setSession(session);
  loginKey.value = "";
  loginError.hidden = true;
  loginView.hidden = true;
  panelView.hidden = false;
  byId("panel-notice").hidden = true;
  openPage(activePage);
  byId("main").focus();
}
document.addEventListener("panel-session-expired", () => {
  // Hide the existing page; retain any future unsaved forms in memory.
  showLogin("会话已失效，请重新登录。未提交的表单仍保留，之前的操作不会自动重试。");
});
byId("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const submit = byId("login-submit");
  submit.disabled = true;
  submit.textContent = "正在登录…";
  loginError.hidden = true;
  try { showPanel(await api("/session", { method: "POST", body: { token: loginKey.value }, anonymous: true })); }
  catch (error) { loginError.textContent = error.status ? error.message : "暂时无法连接，请检查网络后重试。"; loginError.hidden = false; }
  finally { submit.disabled = false; submit.textContent = "登录面板"; }
});
byId("logout").addEventListener("click", async () => {
  const button = byId("logout");
  button.disabled = true;
  try { await api("/session", { method: "DELETE" }); setSession(null); showLogin(); }
  catch (error) {
    if (error.status !== 401) {
      byId("panel-notice").textContent = "退出失败，请检查连接后重试。";
      byId("panel-notice").hidden = false;
    }
  } finally { button.disabled = false; }
});
async function checkSession() {
  try { showPanel(await api("/session", { anonymous: true })); }
  catch (error) { showLogin(error.status === 401 ? "" : "暂时无法连接，请稍后重试。"); }
}
checkSession();
