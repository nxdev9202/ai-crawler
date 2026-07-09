const { app, BrowserWindow, shell, dialog, ipcMain } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

// 자동 업데이트용 읽기전용 토큰(CI 빌드 시 시크릿으로 치환됨). dev/미치환이면 비활성.
const UPDATE_TOKEN = "__UPDATE_TOKEN__";

const BACKEND_HOST = "127.0.0.1";
const BACKEND_PORT = 8756;
const BACKEND_URL = `http://${BACKEND_HOST}:${BACKEND_PORT}`;

let backendProc = null;
let mainWindow = null;

// 백엔드(FastAPI) 프로세스 실행
function startBackend() {
  const fs = require("fs");
  const isWin = process.platform === "win32";
  // 쓰기 가능한 데이터 폴더(설치 앱에서 Program Files는 쓰기 불가 → userData 사용)
  const env = {
    ...process.env,
    PYTHONUNBUFFERED: "1",
    BACKEND_PORT: String(BACKEND_PORT),
  };
  // 패키지 설치본에서만 %APPDATA%로 데이터 저장(Program Files 쓰기불가 회피).
  // 개발 중엔 backend 폴더의 기존 .userdata/app.db를 그대로 사용.
  if (app.isPackaged) env.APP_DATA_DIR = app.getPath("userData");

  // 패키지 설치본: PyInstaller로 번들된 백엔드 exe 실행 (python 불필요)
  const bundledExe = path.join(
    process.resourcesPath || "",
    "backend",
    isWin ? "ai-crawler-backend.exe" : "ai-crawler-backend"
  );
  if (app.isPackaged && fs.existsSync(bundledExe)) {
    backendProc = spawn(bundledExe, [], { env, cwd: path.dirname(bundledExe) });
  } else {
    // 개발: venv python 또는 시스템 python
    const backendDir = path.join(__dirname, "..", "backend");
    const venvPy = isWin
      ? path.join(backendDir, ".venv", "Scripts", "python.exe")
      : path.join(backendDir, ".venv", "bin", "python");
    const py = fs.existsSync(venvPy) ? venvPy : isWin ? "python" : "python3";
    backendProc = spawn(py, ["-m", "app.main"], { cwd: backendDir, env });
  }
  // 백엔드 로그를 파일로도 남긴다(문제 진단용): %APPDATA%\NXaiCrawler\backend.log
  let logStream = null;
  try {
    const fs2 = require("fs");
    const logPath = path.join(app.getPath("userData"), "backend.log");
    logStream = fs2.createWriteStream(logPath, { flags: "w" });
    console.log(`[main] backend log → ${logPath}`);
  } catch (e) {
    console.error("log file open failed:", e.message);
  }
  const pipe = (d) => {
    const s = `[backend] ${d}`;
    console.log(s);
    if (logStream) logStream.write(d);
  };
  backendProc.stdout.on("data", pipe);
  backendProc.stderr.on("data", pipe);
  backendProc.on("exit", (code) => pipe(`\n[backend] exited with code ${code}\n`));
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1200,
    height: 860,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  mainWindow.loadFile("index.html");
  // 외부 링크는 기본 브라우저로
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: "deny" };
  });
}

// 자동 업데이트: GitHub Releases(private) 확인 → 다운로드 → 종료 시 설치.
// 진행 상태는 렌더러(update:status)로 보내 UI에 표시하고, 수동 버튼도 지원.
let autoUpdater = null;

function sendStatus(data) {
  try {
    if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send("update:status", data);
  } catch (_) {}
}

function setupAutoUpdate() {
  if (!app.isPackaged) {
    sendStatus({ state: "disabled", reason: "개발 모드" });
    return;
  }
  if (!UPDATE_TOKEN || UPDATE_TOKEN.includes("UPDATE_TOKEN")) {
    sendStatus({ state: "disabled", reason: "업데이트 토큰 미설정" });
    return;
  }
  try {
    ({ autoUpdater } = require("electron-updater"));
  } catch (e) {
    sendStatus({ state: "error", message: "updater 로드 실패: " + e.message });
    return;
  }
  // electron-updater는 process.env.GH_TOKEN을 내장 토큰보다 우선한다.
  // 사용자 PC에 잘못된 GH_TOKEN이 있으면 401이 나므로, 내 읽기전용 토큰으로 강제 지정.
  process.env.GH_TOKEN = UPDATE_TOKEN;
  process.env.GITHUB_TOKEN = UPDATE_TOKEN;

  // 자체서명 인증서라 electron-updater의 Windows 게시자명 검증이 실패한다.
  // 업데이트는 HTTPS + 인증된 private 저장소에서만 오므로 게시자명 검증은 건너뛴다.
  try {
    autoUpdater.verifySignature = () => Promise.resolve(null);
  } catch (_) {}
  autoUpdater.autoDownload = true;
  autoUpdater.autoInstallOnAppQuit = true;
  autoUpdater.setFeedURL({
    provider: "github",
    owner: "nxdev9202",
    repo: "ai-crawler",
    private: true,
    token: UPDATE_TOKEN,
  });
  autoUpdater.on("checking-for-update", () => sendStatus({ state: "checking" }));
  autoUpdater.on("update-available", (info) => sendStatus({ state: "available", version: info.version }));
  autoUpdater.on("update-not-available", () => sendStatus({ state: "latest", version: app.getVersion() }));
  autoUpdater.on("download-progress", (p) => sendStatus({ state: "downloading", percent: Math.round(p.percent) }));
  autoUpdater.on("update-downloaded", (info) => sendStatus({ state: "downloaded", version: info.version }));
  autoUpdater.on("error", (err) => sendStatus({ state: "error", message: String((err && err.message) || err) }));

  autoUpdater.checkForUpdates().catch(() => {});
  setInterval(() => autoUpdater.checkForUpdates().catch(() => {}), 6 * 60 * 60 * 1000);
}

// 렌더러 → 메인: 수동 업데이트 제어
ipcMain.handle("app:version", () => app.getVersion());
ipcMain.handle("update:check", async () => {
  if (!autoUpdater) return { ok: false, reason: app.isPackaged ? "업데이트 비활성(토큰 미설정)" : "개발 모드" };
  try {
    const r = await autoUpdater.checkForUpdates();
    return { ok: true, version: r && r.updateInfo && r.updateInfo.version };
  } catch (e) {
    return { ok: false, reason: String((e && e.message) || e) };
  }
});
ipcMain.handle("update:install", () => {
  if (autoUpdater) autoUpdater.quitAndInstall();
});

app.whenReady().then(() => {
  startBackend();
  // 창을 즉시 띄운다. 백엔드 연결은 렌더러가 폴링하며 로딩 화면을 보여준다.
  createWindow();
  setupAutoUpdate();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("quit", () => {
  if (backendProc) backendProc.kill();
});
