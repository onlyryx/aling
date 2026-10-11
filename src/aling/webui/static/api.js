// Session and CSRF stay in memory. Failed writes are never automatically replayed.
let csrfToken = null;
export function setSession(session) { csrfToken = session?.csrf_token ?? null; }
export async function api(path, { method = "GET", body, anonymous = false } = {}) {
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
    const error = new Error(response.status === 429 ? "登录尝试过多，请稍后再试。" :
      response.status === 401 ? "密钥无效或会话已失效，请重新登录。" :
      response.status === 403 ? "请求来源或权限验证失败，请刷新页面后重试。" : "请求失败，请稍后重试。");
    error.status = response.status;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}
