"""Google AI(Gemini)로 세션 크롤링 데이터를 분석."""
from __future__ import annotations

import json
from typing import Any

from google import genai
from google.genai import types

from ..config import settings

DEFAULT_INSTRUCTION = (
    "당신은 이커머스 상품 데이터 분석가입니다. 아래는 네이버쇼핑/쿠팡에서 특정 검색어로 "
    "수집한 상품들의 제목·가격·스펙(자연어)과, 각 상품 리뷰를 CPU NLP로 전처리한 결과"
    "(긍정/부정/중립 분포, 긍정·부정 핵심 키워드와 빈도, 대표 리뷰 샘플)입니다. "
    "이 데이터를 바탕으로 다음을 한국어로 구체적 근거와 함께 정리하세요:\n"
    "1) 가격대 분포와 가성비가 좋은 상품 Top 3 (근거 포함)\n"
    "2) 스펙 관점의 공통점/차별점\n"
    "3) 리뷰 감성 분석: 상품별 긍정/부정 비율과, 부정 핵심 키워드로 본 공통 불만 요인, "
    "긍정 키워드로 본 강점\n"
    "4) 종합 구매 의사결정 추천 (어떤 상황에 어떤 상품)\n"
)


def _kw_str(pairs: list[list[Any]]) -> str:
    return ", ".join(f"{w}({c})" for w, c in (pairs or [])) or "없음"


def _build_corpus(products: list[dict[str, Any]]) -> str:
    """상품 리스트를 분석용 텍스트 코퍼스로 직렬화.

    리뷰는 CPU NLP 전처리 결과(긍/부정 분포 + 핵심 키워드 + 대표 샘플)를 넣어
    Gemini가 대량 리뷰의 핵심을 바로 분석하도록 한다.
    """
    lines: list[str] = []
    for p in products:
        lines.append(f"### [{p['source']}] {p['title']}")
        if p.get("price") is not None:
            lines.append(f"- 가격: {p['price']:,}원")
        if p.get("mall_name"):
            lines.append(f"- 판매처: {p['mall_name']}")
        if p.get("rating"):
            lines.append(f"- 평점: {p['rating']} / 총리뷰수: {p.get('review_count')}")
        if p.get("spec_text"):
            lines.append(f"- 스펙: {p['spec_text']}")

        ra = p.get("review_analysis") or {}
        if ra.get("total"):
            lines.append(
                f"- 리뷰분석(수집 {ra['total']}개): 긍정 {ra.get('positive', 0)} · "
                f"부정 {ra.get('negative', 0)} · 중립 {ra.get('neutral', 0)}"
                + (f" · 평균 {ra['avg_score']}점" if ra.get("avg_score") else "")
            )
            lines.append(f"  · 긍정 핵심어: {_kw_str(ra.get('positive_keywords'))}")
            lines.append(f"  · 부정 핵심어: {_kw_str(ra.get('negative_keywords'))}")
            sample = ra.get("sample_reviews") or []
            if sample:
                joined = " | ".join(
                    f"[{s.get('s', '')}{('/' + str(s['score']) + '점') if s.get('score') else ''}] {s.get('text', '')}"
                    for s in sample[:120]
                )
                lines.append(f"  · 대표리뷰: {joined}")
        lines.append("")
    return "\n".join(lines)


async def analyze_session(
    query: str,
    products: list[dict[str, Any]],
    user_prompt: str | None = None,
) -> dict[str, Any]:
    """세션 데이터를 Gemini로 분석하고 결과 텍스트를 반환."""
    from ..accounts import get_gemini_config

    cfg = get_gemini_config()
    if not cfg["api_key"]:
        raise RuntimeError("Gemini API 키가 설정되지 않았습니다. 설정에서 키를 입력하세요.")

    model = cfg["model"]
    client = genai.Client(api_key=cfg["api_key"])
    corpus = _build_corpus(products)
    instruction = user_prompt.strip() if user_prompt and user_prompt.strip() else DEFAULT_INSTRUCTION

    full_prompt = (
        f"검색어: '{query}'\n"
        f"수집 상품 수: {len(products)}\n\n"
        f"[분석 지시]\n{instruction}\n\n"
        f"[상품 데이터]\n{corpus}"
    )

    # google-genai는 동기 클라이언트지만 aio 인터페이스 제공
    resp = await client.aio.models.generate_content(
        model=model,
        contents=full_prompt,
        config=types.GenerateContentConfig(temperature=0.3),
    )
    text = resp.text or ""
    return {
        "model": model,
        "prompt": instruction,
        "result_text": text,
        "result_json": {"query": query, "product_count": len(products)},
    }
