"""FastAPI 백엔드: 크롤링 세션 실행 → 저장 → Gemini 분석."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, update

from .accounts import get_accounts, masked_status, save_accounts
from .config import settings
from .db import Analysis, CrawlSession, Product, SessionLocal, init_db

# 무거운 모듈(playwright/google-genai/kiwipiepy)은 백엔드 시작 속도를 위해
# 최상단에서 import하지 않고, 실제로 쓰는 함수 안에서 지연 import한다.

app = FastAPI(title="AI 분석 크롤러")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 세션별 진행 로그 (메모리) - 폴링으로 프론트에 전달
PROGRESS: dict[int, list[str]] = defaultdict(list)
# 실행 중인 크롤링 asyncio 작업 (중지용)
RUNNING_TASKS: dict[int, asyncio.Task] = {}


class CreateSessionReq(BaseModel):
    query: str
    sources: list[str] = ["naver", "coupang"]
    max_products: Optional[int] = None


class AnalyzeReq(BaseModel):
    prompt: Optional[str] = None


class AccountsReq(BaseModel):
    naver_id: Optional[str] = None
    naver_pw: Optional[str] = None
    coupang_email: Optional[str] = None
    coupang_pw: Optional[str] = None
    gemini_api_key: Optional[str] = None
    gemini_model: Optional[str] = None
    use_real_chrome: Optional[str] = None
    chrome_profile: Optional[str] = None


@app.on_event("startup")
async def _startup() -> None:
    await init_db()
    # 백엔드 재시작으로 남겨진 고아(running) 세션 정리
    async with SessionLocal() as db:
        await db.execute(
            update(CrawlSession)
            .where(CrawlSession.status == "running")
            .values(status="interrupted", error="백엔드 재시작으로 중단됨")
        )
        await db.commit()


@app.get("/health")
async def health() -> dict[str, Any]:
    from .accounts import get_gemini_config

    cfg = get_gemini_config()
    key = cfg["api_key"]
    has_key = bool(key) and "붙여넣기" not in key
    return {"ok": True, "model": cfg["model"], "has_key": has_key}


@app.get("/accounts")
async def get_accounts_status() -> dict[str, Any]:
    """설정된 전용 계정 상태(비밀번호 제외)."""
    return masked_status()


@app.post("/accounts")
async def set_accounts(req: AccountsReq) -> dict[str, Any]:
    """전용 크롤링 계정 저장."""
    save_accounts(req.model_dump(exclude_none=True))
    return masked_status()


@app.get("/login-status")
async def login_status() -> dict[str, Any]:
    """네이버·쿠팡 로그인 상태 확인(전용 프로필 기준)."""
    from .crawlers import login as site_login  # 지연 import

    result: dict[str, Any] = {}
    try:
        result["naver"] = await site_login.check_login()
    except Exception:  # noqa: BLE001
        result["naver"] = False
    try:
        result["coupang"] = await site_login.coupang_check_login()
    except Exception:  # noqa: BLE001
        result["coupang"] = False
    return result


# 사이트별 미리 로그인(사전 세션 확보) 상태
LOGIN_STATE: dict[str, dict[str, Any]] = {}


async def _run_login(site: str) -> None:
    """헤드풀로 전용 계정 로그인 → .userdata 세션 저장(백그라운드)."""
    st = LOGIN_STATE[site]

    def log(m: str) -> None:
        st["logs"].append(m)

    try:
        if site == "coupang":
            # 쿠팡: 크롤러 크롬 창을 열어 사용자가 직접 둘러보며 Akamai 통과(Windows 대응)
            from .crawlers.cdp_login import prepare_coupang_session

            r = await prepare_coupang_session(on_progress=log)
            st["logged_in"] = bool(r.get("ok") or r.get("logged_in"))
            if not r.get("ok"):
                log("[쿠팡] 준비 미완료 — 창에서 상품을 더 둘러본 뒤 다시 시도하세요.")
        else:
            # 네이버: 진짜 Chrome + CDP로 로그인. 세션은 .userdata에 저장됨.
            from .crawlers.cdp_login import login_via_cdp

            ok = await login_via_cdp(site, on_progress=log)
            st["logged_in"] = bool(ok)
    except Exception as e:  # noqa: BLE001
        log(f"[오류] {type(e).__name__}: {e}")
        st["logged_in"] = False
    finally:
        st["running"] = False


@app.post("/login/{site}")
async def start_login(site: str) -> dict[str, Any]:
    """미리 로그인 시작. 브라우저 창이 열리고 2차 인증은 창에서 직접 처리."""
    if site not in ("naver", "coupang"):
        raise HTTPException(400, "site는 naver 또는 coupang")
    # 다른 로그인/크롤이 프로필을 쓰는 중이면 충돌 방지
    if any(s.get("running") for s in LOGIN_STATE.values()):
        raise HTTPException(409, "다른 로그인이 진행 중입니다. 잠시 후 다시 시도하세요.")
    if any(not t.done() for t in RUNNING_TASKS.values()):
        raise HTTPException(409, "크롤링이 진행 중입니다. 완료 후 로그인하세요.")

    acc = get_accounts()
    if site == "naver" and not (acc["naver_id"] and acc["naver_pw"]):
        raise HTTPException(400, "네이버 계정이 설정되지 않았습니다.")
    if site == "coupang" and not (acc["coupang_email"] and acc["coupang_pw"]):
        raise HTTPException(400, "쿠팡 계정이 설정되지 않았습니다.")

    LOGIN_STATE[site] = {"running": True, "logged_in": None, "logs": ["[로그인] 브라우저 여는 중…"]}
    asyncio.create_task(_run_login(site))
    return {"started": True}


@app.get("/login/{site}/progress")
async def login_progress(site: str) -> dict[str, Any]:
    return LOGIN_STATE.get(site, {"running": False, "logged_in": None, "logs": []})


async def _run_crawl(session_id: int, query: str, sources: list[str], max_products: Optional[int]) -> None:
    """백그라운드 크롤링 작업. 상품을 1개씩 즉시 DB에 저장(증분)."""
    from .analysis.nlp import analyze_reviews  # 지연 import
    from .crawlers import coupang as coupang_crawler
    from .crawlers import naver as naver_crawler

    def log(msg: str) -> None:
        PROGRESS[session_id].append(msg)

    counts = {"naver": 0, "coupang": 0, "errors": 0}

    async def save_product(entry: dict[str, Any]) -> None:
        """상품 1개를 즉시 저장하고 세션 통계 갱신 → 중간에 끊겨도 보존.

        저장 전에 리뷰를 CPU NLP로 전처리(긍/부정 + 핵심 키워드)한다.
        """
        try:
            analysis = analyze_reviews(entry.get("reviews_json") or [])
        except Exception:  # noqa: BLE001
            analysis = {}
        async with SessionLocal() as db:
            db.add(Product(session_id=session_id, review_analysis=analysis, **entry))
            counts[entry["source"]] = counts.get(entry["source"], 0) + 1
            if entry.get("crawl_error"):
                counts["errors"] += 1
            sess = await db.get(CrawlSession, session_id)
            if sess:
                sess.stats = {"total": counts["naver"] + counts["coupang"], **counts}
            await db.commit()

    try:
        if "naver" in sources:
            await naver_crawler.crawl(query, max_products, on_progress=log, on_product=save_product)
        if "coupang" in sources:
            await coupang_crawler.crawl(query, max_products, on_progress=log, on_product=save_product)

        async with SessionLocal() as db:
            sess = await db.get(CrawlSession, session_id)
            if sess:
                sess.status = "done"
                await db.commit()
        log(f"[완료] 총 {counts['naver'] + counts['coupang']}개 저장")
    except asyncio.CancelledError:
        log("[중지] 사용자 요청으로 크롤링을 중지했습니다.")
        async with SessionLocal() as db:
            sess = await db.get(CrawlSession, session_id)
            if sess:
                sess.status = "stopped"
                await db.commit()
        raise
    except Exception as e:  # noqa: BLE001
        err = f"{type(e).__name__}: {e}"
        log(f"[오류] {err}")
        async with SessionLocal() as db:
            sess = await db.get(CrawlSession, session_id)
            if sess:
                sess.status = "error"
                sess.error = err
                await db.commit()
    finally:
        RUNNING_TASKS.pop(session_id, None)


@app.post("/sessions")
async def create_session(req: CreateSessionReq) -> dict[str, Any]:
    if not req.query.strip():
        raise HTTPException(400, "검색어가 비어 있습니다.")
    async with SessionLocal() as db:
        sess = CrawlSession(query=req.query.strip(), sources=req.sources, status="running", stats={})
        db.add(sess)
        await db.commit()
        await db.refresh(sess)
        sid = sess.id
    PROGRESS[sid] = ["[시작] 세션 생성됨"]
    # 백그라운드 실행 (중지 가능하도록 task 보관)
    task = asyncio.create_task(_run_crawl(sid, req.query.strip(), req.sources, req.max_products))
    RUNNING_TASKS[sid] = task
    return {"session_id": sid, "status": "running"}


@app.post("/sessions/{sid}/stop")
async def stop_session(sid: int) -> dict[str, Any]:
    """진행 중인 크롤링을 정상 취소. 그때까지 저장된 상품은 보존됨."""
    task = RUNNING_TASKS.get(sid)
    if task and not task.done():
        task.cancel()
        return {"stopped": True}
    # 작업 핸들이 없으면(백엔드 재시작 등) 상태만 정리
    async with SessionLocal() as db:
        sess = await db.get(CrawlSession, sid)
        if sess and sess.status == "running":
            sess.status = "stopped"
            await db.commit()
    return {"stopped": True}


@app.get("/sessions/{sid}/progress")
async def get_progress(sid: int) -> dict[str, Any]:
    async with SessionLocal() as db:
        sess = await db.get(CrawlSession, sid)
        if sess is None:
            raise HTTPException(404, "세션 없음")
        count = (
            await db.execute(select(Product).where(Product.session_id == sid))
        ).scalars().all()
    return {
        "status": sess.status,
        "error": sess.error,
        "logs": PROGRESS.get(sid, []),
        "product_count": len(count),
        "stats": sess.stats,
    }


@app.get("/sessions")
async def list_sessions() -> list[dict[str, Any]]:
    async with SessionLocal() as db:
        rows = (
            await db.execute(select(CrawlSession).order_by(CrawlSession.id.desc()).limit(100))
        ).scalars().all()
        return [
            {
                "id": s.id,
                "query": s.query,
                "sources": s.sources,
                "status": s.status,
                "stats": s.stats,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in rows
        ]


@app.delete("/sessions/{sid}")
async def delete_session(sid: int) -> dict[str, Any]:
    """세션과 그 하위 상품/분석을 삭제. 실행 중이면 먼저 중지."""
    task = RUNNING_TASKS.get(sid)
    if task and not task.done():
        task.cancel()
    async with SessionLocal() as db:
        # 하위 레코드 먼저 삭제(SQLite ondelete 미보장 대비) 후 세션 삭제
        from sqlalchemy import delete as sa_delete

        await db.execute(sa_delete(Product).where(Product.session_id == sid))
        await db.execute(sa_delete(Analysis).where(Analysis.session_id == sid))
        sess = await db.get(CrawlSession, sid)
        if sess:
            await db.delete(sess)
        await db.commit()
    PROGRESS.pop(sid, None)
    return {"deleted": True}


@app.get("/sessions/{sid}")
async def get_session(sid: int) -> dict[str, Any]:
    async with SessionLocal() as db:
        sess = await db.get(CrawlSession, sid)
        if sess is None:
            raise HTTPException(404, "세션 없음")
        products = (
            await db.execute(
                select(Product).where(Product.session_id == sid).order_by(Product.source, Product.rank)
            )
        ).scalars().all()
        analyses = (
            await db.execute(
                select(Analysis).where(Analysis.session_id == sid).order_by(Analysis.id.desc())
            )
        ).scalars().all()
    return {
        "id": sess.id,
        "query": sess.query,
        "sources": sess.sources,
        "status": sess.status,
        "error": sess.error,
        "stats": sess.stats,
        "products": [
            {
                "id": p.id,
                "source": p.source,
                "rank": p.rank,
                "title": p.title,
                "price": p.price,
                "mall_name": p.mall_name,
                "url": p.url,
                "image_url": p.image_url,
                "spec_json": p.spec_json,
                "spec_text": p.spec_text,
                "reviews_json": p.reviews_json,
                "review_analysis": p.review_analysis,
                "rating": p.rating,
                "review_count": p.review_count,
                "crawl_error": p.crawl_error,
            }
            for p in products
        ],
        "analyses": [
            {"id": a.id, "prompt": a.prompt, "model": a.model, "result_text": a.result_text}
            for a in analyses
        ],
    }


@app.post("/sessions/{sid}/analyze")
async def analyze(sid: int, req: AnalyzeReq) -> dict[str, Any]:
    async with SessionLocal() as db:
        sess = await db.get(CrawlSession, sid)
        if sess is None:
            raise HTTPException(404, "세션 없음")
        products = (
            await db.execute(select(Product).where(Product.session_id == sid))
        ).scalars().all()
        if not products:
            raise HTTPException(400, "분석할 상품 데이터가 없습니다.")
        payload = [
            {
                "source": p.source,
                "title": p.title,
                "price": p.price,
                "mall_name": p.mall_name,
                "rating": p.rating,
                "review_count": p.review_count,
                "spec_text": p.spec_text,
                "review_analysis": p.review_analysis,
            }
            for p in products
        ]
        from .analysis.gemini import analyze_session  # 지연 import

        try:
            result = await analyze_session(sess.query, payload, req.prompt)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(500, f"분석 실패: {e}")

        row = Analysis(
            session_id=sid,
            prompt=result["prompt"],
            model=result["model"],
            result_text=result["result_text"],
            result_json=result["result_json"],
        )
        db.add(row)
        await db.commit()
        await db.refresh(row)
    return {"id": row.id, "result_text": row.result_text, "model": row.model}


def run() -> None:
    import uvicorn

    uvicorn.run(app, host=settings.backend_host, port=settings.backend_port, log_level="info")


if __name__ == "__main__":
    run()
