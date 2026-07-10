const { contextBridge, ipcRenderer } = require("electron");

const BASE = "http://127.0.0.1:8756";

async function req(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    const t = await res.text();
    throw new Error(`${res.status}: ${t}`);
  }
  return res.json();
}

contextBridge.exposeInMainWorld("api", {
  health: () => req("GET", "/health"),
  createSession: (query, sources, maxProducts) =>
    req("POST", "/sessions", { query, sources, max_products: maxProducts }),
  progress: (sid) => req("GET", `/sessions/${sid}/progress`),
  stopSession: (sid) => req("POST", `/sessions/${sid}/stop`),
  listSessions: () => req("GET", "/sessions"),
  deleteSession: (sid) => req("DELETE", `/sessions/${sid}`),
  getSession: (sid) => req("GET", `/sessions/${sid}`),
  analyze: (sid, prompt) => req("POST", `/sessions/${sid}/analyze`, { prompt }),
  getAccounts: () => req("GET", "/accounts"),
  setAccounts: (data) => req("POST", "/accounts", data),
  loginStatus: () => req("GET", "/login-status"),
  login: (site) => req("POST", `/login/${site}`),
  loginProgress: (site) => req("GET", `/login/${site}/progress`),
  proxyTest: () => req("POST", "/proxy-test"),
  getCookies: () => req("GET", "/cookies"),
  saveCookies: (raw) => req("POST", "/cookies", { raw }),
  clearCookies: () => req("DELETE", "/cookies"),
});

// 앱 업데이트 제어(메인 프로세스 IPC)
contextBridge.exposeInMainWorld("updater", {
  version: () => ipcRenderer.invoke("app:version"),
  check: () => ipcRenderer.invoke("update:check"),
  install: () => ipcRenderer.invoke("update:install"),
  onStatus: (cb) => ipcRenderer.on("update:status", (_e, d) => cb(d)),
  openLogs: () => ipcRenderer.invoke("app:openLogs"),
  openExtension: () => ipcRenderer.invoke("app:openExtension"),
});
