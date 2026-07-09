"""CPU 기반 한국어 리뷰 전처리.

- kiwipiepy(형태소 분석, CPU 전용)로 명사/형용사 핵심 키워드 추출
- 별점(reviewScore) 기반 긍/부정 분류, 별점이 없으면 감성어 사전으로 보정
- 결과를 Gemini가 분석하기 좋은 '핵심 요소' 형태로 정제
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from kiwipiepy import Kiwi

_kiwi: Kiwi | None = None


def _get_kiwi() -> Kiwi:
    global _kiwi
    if _kiwi is None:
        _kiwi = Kiwi()
    return _kiwi


# 키워드로 쓸 형태소 태그: 일반명사/고유명사/형용사/어근
KEYWORD_TAGS = {"NNG", "NNP", "VA", "VA-I", "XR"}

# 정보량 없는 불용어(명사)
STOPWORDS = {
    "것", "거", "수", "때", "저", "제", "그", "이거", "저거", "그거", "점", "분", "번",
    "제품", "상품", "구매", "주문", "사용", "생각", "경우", "정도", "하나", "때문", "정말",
    "진짜", "그냥", "조금", "약간", "그것", "부분", "가지", "니다", "습니다", "이번", "다음",
}

# 별점 없는 리뷰(쿠팡 등) 보정을 위한 간이 감성어 사전
POS_WORDS = {
    "좋다", "만족", "빠르다", "저렴하다", "맛있다", "훌륭하다", "괜찮다", "편하다", "튼튼하다",
    "깔끔하다", "친절하다", "최고", "추천", "가성비", "신선하다", "부드럽다", "예쁘다", "든든하다",
}
NEG_WORDS = {
    "별로", "아쉽다", "느리다", "비싸다", "불편하다", "약하다", "부족하다", "실망", "터지다",
    "깨지다", "상하다", "냄새", "하자", "불량", "최악", "환불", "찢어지다", "눅눅하다", "짜다",
}


def _keywords(reviews: list[dict[str, Any]], topn: int = 25) -> list[list[Any]]:
    kiwi = _get_kiwi()
    cnt: Counter[str] = Counter()
    texts = [r.get("content", "") for r in reviews if r.get("content")]
    if not texts:
        return []
    for tokens in kiwi.tokenize(texts):  # 배치 토크나이즈
        for tok in tokens:
            if tok.tag in KEYWORD_TAGS and len(tok.form) > 1:
                form = tok.form + ("다" if tok.tag.startswith("VA") else "")
                if form in STOPWORDS or tok.form in STOPWORDS:
                    continue
                cnt[form] += 1
    return [[w, c] for w, c in cnt.most_common(topn)]


def _sentiment_of(review: dict[str, Any]) -> str:
    score = review.get("score")
    if isinstance(score, (int, float)) and score > 0:
        if score >= 4:
            return "positive"
        if score <= 2:
            return "negative"
        return "neutral"
    # 별점 없음 → 감성어 사전
    text = review.get("content", "")
    pos = sum(1 for w in POS_WORDS if w[:-1] in text or w in text)
    neg = sum(1 for w in NEG_WORDS if w[:-1] in text or w in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def analyze_reviews(reviews: list[dict[str, Any]], sample_size: int = 150) -> dict[str, Any]:
    """리뷰 리스트 → 긍/부정 분포 + 핵심 키워드 + 대표 샘플."""
    reviews = [r for r in (reviews or []) if r.get("content")]
    if not reviews:
        return {"total": 0}

    pos = [r for r in reviews if _sentiment_of(r) == "positive"]
    neg = [r for r in reviews if _sentiment_of(r) == "negative"]
    neu = [r for r in reviews if _sentiment_of(r) == "neutral"]

    scores = [r["score"] for r in reviews if isinstance(r.get("score"), (int, float)) and r["score"]]
    avg = round(sum(scores) / len(scores), 2) if scores else None

    # 대표 샘플: 긍/부정/중립을 라운드로빈으로 섞어 최대 sample_size개까지 채움
    # (한쪽이 적으면 다른 쪽으로 보충 → 리뷰가 충분하면 항상 sample_size개 확보)
    buckets = [("긍", pos), ("부", neg), ("중", neu)]
    idxs = [0, 0, 0]
    sample: list[dict[str, Any]] = []
    while len(sample) < sample_size and any(idxs[i] < len(buckets[i][1]) for i in range(3)):
        for i in range(3):
            if len(sample) >= sample_size:
                break
            label, bucket = buckets[i]
            if idxs[i] < len(bucket):
                r = bucket[idxs[i]]
                idxs[i] += 1
                sample.append({"s": label, "score": r.get("score"), "text": r["content"]})

    return {
        "total": len(reviews),
        "positive": len(pos),
        "negative": len(neg),
        "neutral": len(neu),
        "avg_score": avg,
        "positive_keywords": _keywords(pos, 25),
        "negative_keywords": _keywords(neg, 25),
        "sample_reviews": sample,
    }
