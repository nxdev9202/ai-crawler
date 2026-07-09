const { app, BrowserWindow, shell } = require("electron");
const { spawn } = require("child_process");
const path = require("path");
const http = require("http");

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

app.whenReady().then(() => {
  startBackend();
  // 창을 즉시 띄운다. 백엔드 연결은 렌더러가 폴링하며 로딩 화면을 보여준다.
  createWindow();

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
