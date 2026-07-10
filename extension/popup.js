// 쿠팡/네이버 쿠키를 읽어 NX 백엔드(localhost:8756/cookies)로 전송한다.
// chrome.cookies API는 httpOnly 쿠키(인증·_abck 등)까지 읽을 수 있어, 페이지 JS로는
// 불가능한 완전한 세션 전송이 된다.

const statusEl = document.getElementById("status");
const portEl = document.getElementById("port");

// 저장된 포트 불러오기
chrome.storage.local.get(["port"], (r) => {
  if (r.port) portEl.value = r.port;
});
portEl.addEventListener("change", () => {
  chrome.storage.local.set({ port: portEl.value.trim() || "8756" });
});

function setStatus(msg, cls) {
  statusEl.textContent = msg;
  statusEl.className = "status" + (cls ? " " + cls : "");
}

// 특정 도메인의 모든 쿠키 조회(httpOnly 포함)
function getCookies(domain) {
  return new Promise((resolve) => {
    chrome.cookies.getAll({ domain }, (cookies) => resolve(cookies || []));
  });
}

async function collect(site) {
  let cookies = [];
  if (site === "coupang" || site === "all") {
    cookies = cookies.concat(await getCookies("coupang.com"));
  }
  if (site === "naver" || site === "all") {
    cookies = cookies.concat(await getCookies("naver.com"));
  }
  return cookies;
}

async function sendToBackend(cookies) {
  const port = portEl.value.trim() || "8756";
  const res = await fetch(`http://127.0.0.1:${port}/cookies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    // 백엔드 /cookies는 {raw: "<JSON 문자열>"} 형식을 받아 파싱·정규화한다.
    body: JSON.stringify({ raw: JSON.stringify(cookies) }),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

async function handle(site) {
  const buttons = document.querySelectorAll("button");
  buttons.forEach((b) => (b.disabled = true));
  setStatus("쿠키 읽는 중…");
  try {
    const cookies = await collect(site);
    if (!cookies.length) {
      setStatus(
        "쿠키가 없습니다. 해당 사이트에 먼저 로그인한 뒤 다시 눌러주세요.",
        "err"
      );
      return;
    }
    setStatus(`전송 중… (${cookies.length}개)`);
    const r = await sendToBackend(cookies);
    if (r.ok) {
      const d = r.domains || {};
      const parts = [];
      if (d.coupang) parts.push(`쿠팡 ${d.coupang}`);
      if (d.naver) parts.push(`네이버 ${d.naver}`);
      setStatus(`✓ 전송 완료! 저장됨: ${parts.join(" · ") || r.count + "개"}\n이제 NX 앱에서 크롤하시면 됩니다.`, "ok");
    } else {
      setStatus("전송됨, 그러나: " + (r.error || "쿠팡/네이버 쿠키 없음"), "err");
    }
  } catch (e) {
    setStatus(
      "전송 실패: " + e.message + "\n→ NX 앱이 켜져 있는지, 포트(8756)가 맞는지 확인하세요.",
      "err"
    );
  } finally {
    buttons.forEach((b) => (b.disabled = false));
  }
}

document.querySelectorAll("button[data-site]").forEach((btn) => {
  btn.addEventListener("click", () => handle(btn.dataset.site));
});
