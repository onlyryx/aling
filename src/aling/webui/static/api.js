// Session and CSRF stay in memory. Failed writes are never automatically replayed.
let csrfToken = null;
export function setSession(session) { csrfToken = session?.csrf_token ?? null; }
export async function adminFetch(path, { method = "GET", body, anonymous = false } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (!anonymous && !["GET", "HEAD"].includes(method) && csrfToken) headers["X-CSRF-Token"] = csrfToken;
  const response = await fetch(`/api/admin${path}`, {
    method, headers, credentials: "same-origin", cache: "no-store",
    ...(body === undefined ? {} : { body: JSON.stringify(body) }),
  });
  if (response.status === 401 && !anonymous) {
    setSession(null);
    document.dispatchEvent(new Event("panel-session-expired"));
  }
  if (!response.ok) {
    let reason = "";
    try { reason = (await response.json()).detail; } catch { /* Only fixed known reasons are displayed. */ }
    const messages = {
      scope_restricted: "本群不在允许范围，无法保存。",
      feature_unavailable: "功能依赖尚未满足，请检查全局开关、AI 或模型配置。",
      memory_confirmation_required: "请重新阅读记忆说明并确认；票据可能已过期或失效。",
      storage_unavailable: "配置读取或保存失败，请检查数据目录后重试。",
      qq_unavailable: "QQ 查询不可用，请检查 OneBot 连接后重试。",
    };
    const error = new Error(messages[reason] ?? (response.status === 429 ? "登录尝试过多，请稍后再试。" :
      response.status === 401 ? "密钥无效或会话已失效，请重新登录。" :
      response.status === 403 ? "请求来源或权限验证失败，请刷新页面后重试。" : "请求失败，请稍后重试。"));
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

export const api = adminFetch;
