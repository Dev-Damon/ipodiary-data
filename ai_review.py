"""
AI 한줄평 생성기 — 확정공모가가 공시된 종목만 OpenAI로 수요 현황 요약.

동작:
  - data/ipos.json 에서 confirmed_price 있는 비(非)SPAC 종목 선별
  - data/ai_reviews.json 에 이미 있는 (같은 확정가) 종목은 스킵 → 신규/변경만 호출
  - 결과를 data/ai_reviews.json 으로 저장 (앱이 fetch)

원칙: 매수/청약 권유 금지 — 공개 데이터의 사실 요약만. (앱에 면책 문구 별도 표시)
비용: gpt-4o-mini, 종목당 수백 토큰 → 사실상 무시 가능한 수준.

환경변수: OPENAI_API_KEY (GitHub Actions에서는 Secrets로 주입)
"""

import json
import os
import sys
from datetime import datetime, timezone

import requests

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
IPOS = os.path.join(BASE, "data", "ipos.json")
LISTINGS = os.path.join(BASE, "data", "listings.json")
OUT = os.path.join(BASE, "data", "ai_reviews.json")

MODEL = "gpt-4o-mini"

SYSTEM = (
    "당신은 한국 공모주(IPO) 데이터 요약 도우미입니다. "
    "제공된 공개 데이터만 근거로 이 공모주의 수요 강도와 유의점을 한국어 2~3문장으로 요약하세요. "
    "규칙: (1) 매수·청약을 권유하거나 말리는 표현 금지 (2) 데이터에 없는 내용 추측 금지 "
    "(3) 수치를 근거로 담백하게 (4) 마지막 문장은 유의점이나 체크포인트로."
)


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def market_temp(listings):
    """최근 상장 10종목 평균 시초 수익률(%)."""
    rets = []
    for l in listings[:10]:
        o, p = l.get("offer_price"), l.get("opening_price")
        if o and p:
            rets.append((p - o) / o * 100)
    return round(sum(rets) / len(rets), 1) if rets else None


def build_prompt(ipo, temp):
    lines = [f"종목명: {ipo['name']} ({ipo.get('market', '시장미상')})"]
    band = ipo.get("band")
    lines.append(f"확정공모가: {ipo['confirmed_price']:,}원 (희망밴드 {band})" if band
                 else f"확정공모가: {ipo['confirmed_price']:,}원")
    if ipo.get("institutional_rate"):
        lines.append(f"수요예측 기관경쟁률: {ipo['institutional_rate']:,.0f}:1")
    if ipo.get("mandatory_holding"):
        lines.append(f"의무보유확약: {ipo['mandatory_holding']}")
    if ipo.get("total_shares"):
        lines.append(f"총공모주식수: {ipo['total_shares']:,}주")
    brokers = ipo.get("broker_conditions") or []
    if brokers:
        parts = []
        for b in brokers:
            s = b["broker"]
            if b.get("allocation_pct"):
                s += f"({b['allocation_pct']:.0f}%)"
            if b.get("role"):
                s += f"[{b['role']}]"
            parts.append(s)
        lines.append("주간사: " + ", ".join(parts))
    if temp is not None:
        lines.append(f"최근 상장 10종목 평균 시초가 수익률: {temp:+.1f}%")
    lines.append(f"청약기간: {ipo.get('subscription_period', '미상')}")
    return "\n".join(lines)


def call_openai(api_key, prompt):
    r = requests.post(
        "https://api.openai.com/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "model": MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 300,
            "temperature": 0.4,
        },
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"].strip()


def main():
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        print("[스킵] OPENAI_API_KEY 없음 — AI 한줄평 생략")
        return

    ipos = load(IPOS, [])
    listings = load(LISTINGS, [])
    reviews = load(OUT, {})
    temp = market_temp(listings)

    made = 0
    for ipo in ipos:
        name = ipo.get("name", "")
        price = ipo.get("confirmed_price")
        if not name or not price:
            continue  # 확정 전
        if "스팩" in name or "기업인수목적" in name:
            continue  # SPAC 제외
        prev = reviews.get(name)
        if prev and prev.get("confirmed_price") == price:
            continue  # 이미 생성됨 (확정가 변동 시 재생성)

        try:
            text = call_openai(api_key, build_prompt(ipo, temp))
        except Exception as e:
            print(f"[실패] {name}: {type(e).__name__}")
            continue
        reviews[name] = {
            "review": text,
            "confirmed_price": price,
            "model": MODEL,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        made += 1
        print(f"[생성] {name}")

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(reviews, f, ensure_ascii=False, indent=1)
    print(f"[완료] 신규 {made}건 / 총 {len(reviews)}건 → data/ai_reviews.json")


if __name__ == "__main__":
    main()
