// 서버와 이야기하는 부분. 로그인 토큰은 휴대폰의 보안 저장소(SecureStore)에 둔다.
import * as SecureStore from "expo-secure-store";

let base = "";
let token = null;

export async function loadConfig() {
  base = (await SecureStore.getItemAsync("server")) || "";
  token = await SecureStore.getItemAsync("token");
  return { base, loggedIn: !!token };
}

export async function setServer(url) {
  base = url.trim().replace(/\/+$/, "");
  await SecureStore.setItemAsync("server", base);
}
export const getServer = () => base;

async function readJson(res) {
  const text = await res.text();
  try { return JSON.parse(text); } catch { return { detail: `서버 오류 (${res.status})` }; }
}
export const errText = (d) => (typeof d.detail === "string" ? d.detail : (d.detail || []).map((x) => x.msg).join(", "));

export async function login(username, password) {
  const res = await fetch(`${base}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Client": "mobile" },
    body: JSON.stringify({ username, password }),
  });
  const d = await readJson(res);
  if (!res.ok) throw new Error(errText(d));
  token = d.token;
  await SecureStore.setItemAsync("token", token);
  return d;
}

export async function logout() {
  try { await api("/auth/logout", { method: "POST" }); } catch {}
  token = null;
  await SecureStore.deleteItemAsync("token");
}

export class AuthError extends Error {}

// 로그인한 상태로 API 부르기. 401이면 AuthError → 로그인 화면으로.
export async function api(path, { method = "GET", body } = {}) {
  const res = await fetch(`${base}${path}`, {
    method,
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: body ? JSON.stringify(body) : undefined,
  });
  const d = await readJson(res);
  if (res.status === 401) throw new AuthError("로그인이 필요합니다");
  if (!res.ok) throw new Error(errText(d));
  return d;
}
