let currentSid = null;
let pollTimer = null;

const $ = (id) => document.getElementById(id);
const esc = (s) => (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ===== NumberFlow식 롤링 카운터 ===== */
function animateNumber(el, to, suffix = "") {
  if (!el) return;
  to = Number(to) || 0;
  const from = Number(el.dataset.val || 0);
  const render = (v) => {
    el.innerHTML = v.toLocaleString("ko-KR") + (suffix ? `<small>${suffix}</small>` : "");
  };
  if (from === to) {
    render(to);
    el.dataset.val = String(to);
    return;
  }
  if (reduced()) {
    render(to);
    el.dataset.val = String(to);
    return;
  }
  const dur = 480;
  const start = performance.now();
  const step = (now) => {
    let t = Math.min(1, (now - start) / dur);
    t = 1 - Math.pow(1 - t, 3); // easeOutCubic
    render(Math.round(from + (to - from) * t));
    if (t < 1) {
      requestAnimationFrame(step);
    } else {
      render(to);
      el.dataset.val = String(to);
      el.animate(
        [{ transform: "translateY(-2px)" }, { transform: "translateY(0)" }],
        { duration: 160, easing: "ease-out" }
      );
    }
  };
  requestAnimationFrame(step);
}

const STATUS_LABEL = { running: "진행중", done: "완료", error: "오류", stopped: "중지됨", interrupted: "중단됨" };

/* ===== 헬스 ===== */
async function refreshHealth() {
  const pill = $("statusPill");
  try {
    const h = await window.api.health();
    if (h.has_key) {
      $("health").textContent = `연결됨 · ${h.model}`;
      pill.className = "status status--ok";
    } else {
      $("health").textContent = "연결됨 · API키 미설정";
      pill.className = "status status--warn";
    }
  } catch {
    $("health").textContent = "백엔드 미연결";
    pill.className = "status status--off";
  }
}

/* ===== 세션 목록 ===== */
async function refreshSessions() {
  const list = await window.api.listSessions();
  const el = $("sessions");
  if (!list.length) {
    el.innerHTML = `<div class="rail__empty">아직 실행한 세션이 없습니다.<br />검색어를 입력해 첫 크롤링을 시작하세요.</div>`;
    return;
  }
  el.innerHTML = list
    .map((s) => {
      const total = s.stats && s.stats.total ? s.stats.total : 0;
      return `<div class="sess ${s.id === currentSid ? "active" : ""}" data-id="${s.id}">
        <div class="sess__main">
          <span class="sess__q">${esc(s.query)}</span>
          <span class="sess__meta">
            <span class="pill pill--${s.status}">${STATUS_LABEL[s.status] || s.status}</span>
            ${total ? `<span class="sess__count">${total}개</span>` : ""}
          </span>
        </div>
        <button class="sess__del" data-del="${s.id}" title="세션 삭제">✕</button>
      </div>`;
    })
    .join("");
  el.querySelectorAll(".sess__main").forEach((n) =>
    n.addEventListener("click", () => openSession(parseInt(n.parentNode.dataset.id)))
  );
  el.querySelectorAll(".sess__del").forEach((n) =>
    n.addEventListener("click", async (e) => {
      e.stopPropagation();
      const id = parseInt(n.dataset.del);
      if (!confirm("이 세션을 삭제할까요? (상품·리뷰·분석 결과 모두 삭제)")) return;
      try {
        await window.api.deleteSession(id);
        if (id === currentSid) {
          currentSid = null;
          renderProducts([]);
          $("analysis").innerHTML = "";
          $("log").textContent = "대기 중…";
        }
        await refreshSessions();
      } catch (err) {
        alert("삭제 실패: " + err.message);
      }
    })
  );
}

/* ===== 결과 카드 ===== */
function renderProducts(products) {
  const n = products.filter((p) => p.source === "naver").length;
  const c = products.filter((p) => p.source === "coupang").length;
  $("resultMeta").textContent = products.length ? `총 ${products.length} · 네이버 ${n} · 쿠팡 ${c}` : "";
  if (!products.length) {
    $("results").innerHTML = `<div class="empty">수집된 상품이 없습니다.</div>`;
    return;
  }
  $("results").innerHTML = products
    .map((p) => {
      const reviews = (p.reviews_json || []).slice(0, 3).map((r) =>
        typeof r === "object" && r !== null ? (r.score ? `(${r.score}★) ` : "") + (r.content || "") : r
      );
      const chip = p.source === "naver" ? "chip--naver" : "chip--coupang";
      const chipLabel = p.source === "naver" ? "네이버" : "쿠팡";
      const ra = p.review_analysis || {};
      let sentiHtml = "";
      if (ra.total) {
        const tot = ra.positive + ra.negative + ra.neutral || 1;
        const pk = (ra.positive_keywords || []).slice(0, 4).map((k) => `<b>${esc(k[0])}</b>`).join(" ");
        const nk = (ra.negative_keywords || []).slice(0, 3).map((k) => `<em>${esc(k[0])}</em>`).join(" ");
        sentiHtml = `<div class="senti">
          <span class="senti__bar">
            <i class="p" style="width:${(ra.positive / tot) * 100}%"></i>
            <i class="n" style="width:${(ra.negative / tot) * 100}%"></i>
            <i class="z" style="width:${(ra.neutral / tot) * 100}%"></i>
          </span>
          <span class="senti__t">리뷰 ${ra.total} · 긍 ${ra.positive} / 부 ${ra.negative}${ra.avg_score ? ` · ${ra.avg_score}★` : ""}</span>
          <span class="senti__kw">${pk}${nk ? " · " + nk : ""}</span>
        </div>`;
      }
      const media = p.image_url
        ? `<img src="${esc(p.image_url)}" onerror="this.parentNode.textContent='◳'"/>`
        : "◳";
      return `<article class="pcard">
        <div class="pcard__media">${media}</div>
        <div>
          <div class="pcard__meta">
            <span class="chip ${chip}">${chipLabel}</span>
            <span class="pcard__mall">${esc(p.mall_name || "")}</span>
            ${p.rating ? `<span class="pcard__rating">★ ${esc(p.rating)}${p.review_count ? ` · ${esc(String(p.review_count))}` : ""}</span>` : ""}
          </div>
          <h3 class="pcard__title">${esc(p.title)}</h3>
          <div class="pcard__price ${p.price ? "" : "empty"}">${
            p.price ? p.price.toLocaleString("ko-KR") + '<span>원</span>' : "가격 미확인"
          }</div>
          ${p.spec_text ? `<p class="pcard__spec">${esc(p.spec_text.slice(0, 320))}</p>` : ""}
          ${sentiHtml}
          ${p.crawl_error ? `<div class="pcard__err">수집오류: ${esc(p.crawl_error)}</div>` : ""}
          <div class="disc">
            ${
              reviews.length
                ? `<details><summary>리뷰 ${reviews.length}</summary><div class="disc__body">${reviews
                    .map((r) => "• " + esc(r))
                    .join("\n")}</div></details>`
                : ""
            }
            <details><summary>스펙 JSON</summary><div class="disc__body">${esc(
              JSON.stringify(p.spec_json || {}, null, 2)
            )}</div></details>
          </div>
        </div>
      </article>`;
    })
    .join("");
}

/* ===== 세션 열기 ===== */
async function openSession(sid) {
  currentSid = sid;
  await refreshSessions();
  const s = await window.api.getSession(sid);
  const products = s.products || [];
  renderProducts(products);
  animateNumber($("liveCount"), products.length, "개");
  animateNumber($("cntNaver"), products.filter((p) => p.source === "naver").length);
  animateNumber($("cntCoupang"), products.filter((p) => p.source === "coupang").length);
  setRunState(s.status);
  $("analysis").innerHTML = s.analyses && s.analyses.length
    ? `<div class="analysis">${esc(s.analyses[0].result_text)}</div>`
    : "";
  if (s.status === "running") startPolling(sid);
}

function setRunState(status) {
  const running = status === "running";
  document.body.classList.toggle("is-running", running);
  $("runLabel").textContent = STATUS_LABEL[status] || "대기 중";
}

/* ===== 진행 폴링 ===== */
function startPolling(sid) {
  if (pollTimer) clearInterval(pollTimer);
  setRunState("running");
  pollTimer = setInterval(async () => {
    try {
      const pr = await window.api.progress(sid);
      $("log").textContent = (pr.logs || []).join("\n");
      $("log").scrollTop = $("log").scrollHeight;
      animateNumber($("liveCount"), pr.product_count || 0, "개");
      if (pr.stats) {
        animateNumber($("cntNaver"), pr.stats.naver || 0);
        animateNumber($("cntCoupang"), pr.stats.coupang || 0);
      }
      if (pr.status !== "running") {
        clearInterval(pollTimer);
        pollTimer = null;
        await openSession(sid);
      }
    } catch (e) {
      console.error(e);
    }
  }, 1200);
}

/* ===== 크롤링 시작 ===== */
$("startBtn").addEventListener("click", async () => {
  const query = $("query").value.trim();
  if (!query) return alert("검색어를 입력하세요.");
  const sources = [];
  if ($("src_naver").checked) sources.push("naver");
  if ($("src_coupang").checked) sources.push("coupang");
  if (!sources.length) return alert("사이트를 하나 이상 선택하세요.");
  const maxp = parseInt($("maxp").value) || 10;

  $("startBtn").disabled = true;
  $("log").textContent = "세션 시작 중…";
  $("results").innerHTML = `<div class="empty">크롤링 중…</div>`;
  $("analysis").innerHTML = "";
  $("liveCount").dataset.val = "0";
  $("cntNaver").dataset.val = "0";
  $("cntCoupang").dataset.val = "0";
  try {
    const r = await window.api.createSession(query, sources, maxp);
    currentSid = r.session_id;
    await refreshSessions();
    startPolling(r.session_id);
  } catch (e) {
    $("log").textContent = "시작 실패: " + e.message;
    setRunState("error");
  } finally {
    $("startBtn").disabled = false;
  }
});

/* ===== 분석 ===== */
$("analyzeBtn").addEventListener("click", async () => {
  if (!currentSid) return alert("먼저 세션을 선택하세요.");
  const prompt = $("prompt").value.trim();
  $("analyzeBtn").disabled = true;
  $("analyzeStatus").textContent = "Gemini 분석 중…";
  try {
    const r = await window.api.analyze(currentSid, prompt || null);
    $("analysis").innerHTML = `<div class="analysis">${esc(r.result_text)}</div>`;
    $("analyzeStatus").textContent = `완료 · ${r.model}`;
  } catch (e) {
    $("analyzeStatus").textContent = "분석 실패: " + e.message;
  } finally {
    $("analyzeBtn").disabled = false;
  }
});

/* ===== 중지 ===== */
$("stopBtn").addEventListener("click", async () => {
  if (!currentSid) return;
  $("stopBtn").disabled = true;
  try {
    await window.api.stopSession(currentSid);
    // 폴링이 다음 틱에서 상태 변화를 감지해 마무리함
  } catch (e) {
    console.error(e);
  } finally {
    $("stopBtn").disabled = false;
  }
});

$("query").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("startBtn").click();
});

/* ===== 계정 설정 모달 ===== */
const acctModal = $("acctModal");
async function openAccounts() {
  acctModal.classList.add("open");
  $("acctMsg").textContent = "";
  try {
    const a = await window.api.getAccounts();
    $("acc_naver_id").value = a.naver_id || "";
    if ($("acc_coupang_email")) $("acc_coupang_email").value = a.coupang_email || "";
    // 드롭다운: 저장된 모델이 목록에 없으면 옵션으로 추가 후 선택
    const sel = $("acc_gemini_model");
    const m = a.gemini_model || "gemini-3.5-flash";
    if (m && ![...sel.options].some((o) => o.value === m)) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m + " (현재)";
      sel.insertBefore(opt, sel.firstChild);
    }
    sel.value = m;
    setLoginDot("Naver", a.naver_set ? null : false, a.naver_set ? "계정 설정됨" : "계정 없음");
    setLoginDot("Coupang", a.coupang_set ? null : false, a.coupang_set ? "계정 설정됨" : "계정 없음");
    setLoginDot("Gemini", a.gemini_set ? true : false, a.gemini_set ? "키 설정됨 " + (a.gemini_key_hint || "") : "키 없음");
    // 내 크롬 프로필 토글 + 프로필 목록
    $("acc_use_real_chrome").checked = !!a.use_real_chrome;
    const psel = $("acc_chrome_profile");
    const profs = a.chrome_profiles || [];
    psel.innerHTML = profs.length
      ? profs.map((p) => `<option value="${esc(p.dir)}">${esc(p.name)}${p.email ? " · " + esc(p.email) : ""} (${esc(p.dir)})</option>`).join("")
      : `<option value="Default">Default</option>`;
    psel.value = a.chrome_profile || "Default";
    // 프록시
    $("acc_proxy_enabled").checked = !!a.proxy_enabled;
    $("acc_proxy_server").value = a.proxy_server || "";
    $("acc_proxy_username").value = a.proxy_username || "";
    $("proxyTestMsg").textContent = "";
    // 세션 쿠키 상태
    try {
      const ck = await window.api.getCookies();
      renderCookieStat(ck);
    } catch (_) {}
    $("cookieMsg").textContent = "";
  } catch (e) {
    $("acctMsg").textContent = "불러오기 실패: " + e.message;
  }
}
function setLoginDot(site, on, txt) {
  const dot = $("dot" + site);
  dot.className = "login-dot" + (on === true ? " on" : on === false ? " off" : "");
  $("txt" + site).textContent = txt;
}
$("acctBtn").addEventListener("click", openAccounts);
$("acctClose").addEventListener("click", () => acctModal.classList.remove("open"));
acctModal.addEventListener("click", (e) => {
  if (e.target === acctModal) acctModal.classList.remove("open");
});
$("acctSave").addEventListener("click", async () => {
  const data = {
    naver_id: $("acc_naver_id").value.trim(),
    naver_pw: $("acc_naver_pw").value,
    coupang_email: $("acc_coupang_email") ? $("acc_coupang_email").value.trim() : "",
    coupang_pw: $("acc_coupang_pw") ? $("acc_coupang_pw").value : "",
    gemini_api_key: $("acc_gemini_api_key").value.trim(),
    gemini_model: $("acc_gemini_model").value,
    use_real_chrome: "0",
    chrome_profile: $("acc_chrome_profile").value || "Default",
    proxy_enabled: $("acc_proxy_enabled").checked ? "1" : "0",
    proxy_server: $("acc_proxy_server").value.trim(),
    proxy_username: $("acc_proxy_username").value.trim(),
    proxy_password: $("acc_proxy_password").value,
  };
  $("acctSave").disabled = true;
  $("acctMsg").textContent = "저장 중…";
  try {
    const a = await window.api.setAccounts(data);
    $("acc_naver_pw").value = "";
    if ($("acc_coupang_pw")) $("acc_coupang_pw").value = "";
    $("acc_gemini_api_key").value = "";
    $("acc_proxy_password").value = "";
    setLoginDot("Naver", a.naver_set ? null : false, a.naver_set ? "계정 설정됨" : "계정 없음");
    setLoginDot("Coupang", a.coupang_set ? null : false, a.coupang_set ? "계정 설정됨" : "계정 없음");
    setLoginDot("Gemini", a.gemini_set ? true : false, a.gemini_set ? "키 설정됨 " + (a.gemini_key_hint || "") : "키 없음");
    $("acctMsg").textContent = "저장됨 ✓";
  } catch (e) {
    $("acctMsg").textContent = "저장 실패: " + e.message;
  } finally {
    $("acctSave").disabled = false;
  }
});
/* 세션 쿠키 붙여넣기 */
function renderCookieStat(ck) {
  const el = $("cookieStat");
  if (!el) return;
  const d = (ck && ck.domains) || {};
  const parts = [];
  if (d.coupang) parts.push(`쿠팡 ${d.coupang}`);
  if (d.naver) parts.push(`네이버 ${d.naver}`);
  if (d["기타"]) parts.push(`기타 ${d["기타"]}`);
  el.textContent = ck && ck.count ? `저장됨: ${parts.join(" · ")}` : "저장된 쿠키 없음";
}
$("cookieSave").addEventListener("click", async () => {
  const raw = $("acc_cookies").value.trim();
  const msg = $("cookieMsg");
  if (!raw) {
    msg.textContent = "붙여넣은 내용이 없습니다.";
    msg.style.color = "#e5533d";
    return;
  }
  $("cookieSave").disabled = true;
  msg.textContent = "저장 중…";
  msg.style.color = "";
  try {
    const r = await window.api.saveCookies(raw);
    if (r.ok) {
      msg.textContent = `✓ ${r.count}개 저장됨`;
      msg.style.color = "#22a06b";
      $("acc_cookies").value = "";
      renderCookieStat({ count: r.count, domains: r.domains });
    } else {
      msg.textContent = r.error || "저장 실패";
      msg.style.color = "#e5533d";
    }
  } catch (e) {
    msg.textContent = "저장 실패: " + e.message;
    msg.style.color = "#e5533d";
  } finally {
    $("cookieSave").disabled = false;
  }
});
$("cookieClear").addEventListener("click", async () => {
  try {
    await window.api.clearCookies();
    renderCookieStat({ count: 0, domains: {} });
    $("cookieMsg").textContent = "삭제됨";
    $("cookieMsg").style.color = "";
  } catch (e) {
    $("cookieMsg").textContent = "삭제 실패: " + e.message;
  }
});

/* 프록시 테스트: 저장된 프록시로 공인 IP·쿠팡 접속 확인 */
$("proxyTest").addEventListener("click", async () => {
  const btn = $("proxyTest");
  const msg = $("proxyTestMsg");
  btn.disabled = true;
  msg.textContent = "테스트 중… (최대 40초)";
  msg.style.color = "";
  try {
    const r = await window.api.proxyTest();
    const ipTxt = r.ip ? `IP ${r.ip}${r.proxy_used ? "(프록시)" : "(직접)"}` : "IP 확인 실패";
    if (r.coupang_ok) {
      msg.textContent = `✓ ${ipTxt} · 쿠팡 접속 정상`;
      msg.style.color = "#22a06b";
    } else {
      msg.textContent = `${ipTxt} · 쿠팡 ✗ ${r.detail || ""}`;
      msg.style.color = "#e5533d";
    }
  } catch (e) {
    msg.textContent = "테스트 실패: " + e.message;
    msg.style.color = "#e5533d";
  } finally {
    btn.disabled = false;
  }
});

/* 미리 로그인: 브라우저 창을 열어 세션을 .userdata에 저장. 2차 인증은 창에서 처리 */
async function preLogin(site, siteKey) {
  const btn = $("login" + siteKey);
  btn.disabled = true;
  setLoginDot(siteKey, null, "로그인 중…");
  $("acctMsg").textContent = "브라우저 창이 열립니다. 2차 인증이 뜨면 창에서 직접 입력하세요.";
  try {
    await window.api.login(site);
  } catch (e) {
    setLoginDot(siteKey, false, "실패");
    $("acctMsg").textContent = "로그인 시작 실패: " + e.message;
    btn.disabled = false;
    return;
  }
  // 진행 폴링 (최대 ~6분)
  for (let i = 0; i < 180; i++) {
    await new Promise((r) => setTimeout(r, 2000));
    let pr;
    try {
      pr = await window.api.loginProgress(site);
    } catch {
      continue;
    }
    const last = (pr.logs || [])[pr.logs.length - 1] || "";
    $("acctMsg").textContent = last;
    if (!pr.running) {
      if (pr.logged_in) {
        setLoginDot(siteKey, true, "로그인됨");
        $("acctMsg").textContent = "로그인 완료 ✓ 세션이 저장되었습니다.";
      } else {
        setLoginDot(siteKey, false, "미로그인");
        $("acctMsg").textContent = "로그인 실패/미완료: " + last;
      }
      break;
    }
  }
  btn.disabled = false;
}
$("loginNaver").addEventListener("click", () => preLogin("naver", "Naver"));
$("loginCoupang").addEventListener("click", () => preLogin("coupang", "Coupang"));

$("acctCheck").addEventListener("click", async () => {
  $("acctCheck").disabled = true;
  $("acctMsg").textContent = "로그인 상태 확인 중… (브라우저 확인, 최대 20초)";
  setLoginDot("Naver", null, "확인 중…");
  setLoginDot("Coupang", null, "확인 중…");
  try {
    const s = await window.api.loginStatus();
    setLoginDot("Naver", !!s.naver, s.naver ? "로그인됨" : "미로그인");
    setLoginDot("Coupang", !!s.coupang, s.coupang ? "로그인됨" : "미로그인");
    $("acctMsg").textContent = "확인 완료";
  } catch (e) {
    $("acctMsg").textContent = "확인 실패: " + e.message;
  } finally {
    $("acctCheck").disabled = false;
  }
});

/* ===== 백엔드 연결 관리: 연결될 때까지 폴링, 연결되면 오버레이 숨기고 init ===== */
let backendConnected = false;
async function connectionLoop() {
  let tries = 0;
  const boot = $("bootOverlay");
  const bootMsg = $("bootMsg");
  while (!backendConnected) {
    tries++;
    try {
      await window.api.health();
      backendConnected = true;
      if (boot) boot.classList.add("hide");
      setTimeout(() => boot && (boot.style.display = "none"), 400);
      await init(); // 연결되면 세션 로드
      refreshHealth();
      setInterval(refreshHealth, 5000);
      return;
    } catch {
      if (bootMsg && tries > 6) bootMsg.textContent = "백엔드 연결 대기 중… (백신 검사로 지연될 수 있음)";
      await new Promise((r) => setTimeout(r, 1500));
    }
  }
}

/* ===== 시작 시: 진행 중 세션 자동 복원, 없으면 최근 세션 표시 ===== */
async function init() {
  await refreshHealth();
  try {
    const list = await window.api.listSessions();
    const running = list.find((s) => s.status === "running");
    const target = running || list[0];
    if (target) await openSession(target.id);
    else await refreshSessions();
  } catch {
    await refreshSessions();
  }
}

/* ===== 앱 업데이트 UI ===== */
(async () => {
  if (!window.updater) return;
  try {
    $("appVer").textContent = await window.updater.version();
  } catch {}
  const st = $("updateStatus");
  const setSt = (txt, cls = "") => {
    st.textContent = txt;
    st.className = "update-status" + (cls ? " " + cls : "");
  };
  window.updater.onStatus((d) => {
    if (d.state === "checking") setSt("업데이트 확인 중…");
    else if (d.state === "available") setSt(`새 버전 ${d.version} 발견, 다운로드 중…`);
    else if (d.state === "downloading") setSt(`다운로드 중… ${d.percent}%`);
    else if (d.state === "downloaded") {
      setSt(`새 버전 ${d.version} 준비됨 — 클릭해 재시작`, "ok");
      st.style.cursor = "pointer";
      st.onclick = () => window.updater.install();
    } else if (d.state === "latest") setSt("최신 버전입니다", "ok");
    else if (d.state === "disabled") setSt("자동 업데이트 " + (d.reason || "비활성"));
    else if (d.state === "error") setSt("업데이트 오류: " + (d.message || ""), "err");
  });
  $("updateBtn").addEventListener("click", async () => {
    $("updateBtn").disabled = true;
    setSt("업데이트 확인 중…");
    try {
      const r = await window.updater.check();
      if (!r.ok) setSt("확인 실패: " + (r.reason || ""), "err");
    } catch (e) {
      setSt("확인 실패: " + e.message, "err");
    } finally {
      setTimeout(() => ($("updateBtn").disabled = false), 2000);
    }
  });
  const logBtn = $("logBtn");
  if (logBtn && window.updater && window.updater.openLogs) {
    logBtn.addEventListener("click", () => window.updater.openLogs());
  }
})();

connectionLoop();
