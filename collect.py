"""
IPODiary 데이터 수집기 (서버리스: GitHub Actions에서 주기 실행)

38커뮤니케이션에서 공모주 청약일정과 신규상장 성과를 수집해
data/ipos.json, data/listings.json, data/meta.json 으로 저장한다.

- 청약일정(o=k): 종목명, 청약기간, 확정공모가, 밴드, 경쟁률, 주간사
- 신규상장(o=nw): 상장일, 공모가, 시초가, 첫날종가, 현재가

주의: 38커뮤는 HTTPS 핸드셰이크가 실패하므로 HTTP만 사용, 인코딩은 EUC-KR.
"""

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEDULE_URL = "http://www.38.co.kr/html/fund/index.htm?o=k"
LISTING_URL = "http://www.38.co.kr/html/fund/index.htm?o=nw"
DETAIL_URL = "http://www.38.co.kr/html/fund/?o=v&no={no}"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; IPODiaryBot/1.0)"}
OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _clean(v):
    v = (v or "").strip()
    return v if v and v != "-" else None


def _num(v):
    v = _clean(v)
    if not v:
        return None
    digits = re.sub(r"[^\d]", "", v)
    return int(digits) if digits else None


def _fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=25)
    r.encoding = "euc-kr"
    r.raise_for_status()
    return r.text


def parse_schedule(html):
    """공모주 청약일정 표 파싱."""
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for table in soup.find_all("table"):
        head = table.get_text()
        if "종목명" in head and "공모주일정" in head and "주간사" in head:
            target = table
            break
    if target is None:
        return []

    records = []
    for tr in target.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) < 6:
            continue
        name = _clean(cells[0].get_text())
        period = _clean(cells[1].get_text())
        if not name or not period:
            continue
        if not re.fullmatch(r"\d{4}\.\d{2}\.\d{2}\s*~\s*\d{2}\.\d{2}", period):
            continue
        if "공모뉴스" in name or "공모주일정" in name:
            continue
        underwriters = [u.strip() for u in re.split(r"[,/]", cells[5].get_text())
                        if u.strip()]
        # 상세 페이지 번호 (종목명 링크의 no= 파라미터)
        detail_no = None
        link = cells[0].find("a", href=True)
        if link:
            m = re.search(r"no=(\d+)", link["href"])
            if m:
                detail_no = m.group(1)
        records.append({
            "name": name,
            "subscription_period": period,
            "confirmed_price": _num(cells[2].get_text()),
            "band": _clean(cells[3].get_text()),
            "competition_rate": _clean(cells[4].get_text()),
            "underwriters": underwriters,
            "detail_no": detail_no,
        })
    return records


def parse_underwriter_table(soup):
    """인수회사 표 파싱: 증권사별 배정 물량·역할(대표/공동)·청약한도.

    예: 인수회사 | 주식수              | 청약한도 | 기타
        KB증권   | 384,750 ~ 461,700 주 | - 주    | 대표
    """
    # 38커뮤는 중첩 레이아웃 테이블 구조 → 두 키워드를 모두 포함하는
    # 가장 안쪽(텍스트가 가장 짧은) 테이블이 실제 인수회사 표
    candidates = [
        t for t in soup.find_all("table")
        if "인수회사" in t.get_text() and "주식수" in t.get_text()
    ]
    if not candidates:
        return []
    target = min(candidates, key=lambda t: len(t.get_text()))

    def upper_num(s):
        """'384,750 ~ 461,700 주' → 461700 (범위면 상단)."""
        nums = re.findall(r"[\d,]{2,}", s or "")
        if not nums:
            return None
        return int(nums[-1].replace(",", ""))

    conds = []
    for tr in target.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all("td")]
        if len(cells) < 2 or "인수회사" in cells[0]:
            continue
        broker = cells[0].strip()
        # 증권사명만: 짧은 텍스트 + '증권' 포함 (본문 텍스트 블록 배제)
        if not broker or "증권" not in broker or len(broker) > 20:
            continue
        shares = upper_num(cells[1]) if len(cells) > 1 else None
        limit = upper_num(cells[2]) if len(cells) > 2 else None
        role = None
        if len(cells) > 3 and cells[3].strip() in ("대표", "공동", "인수", "주간"):
            role = cells[3].strip()
        conds.append({
            "broker": broker,
            "allocation": shares,
            "limit": limit,
            "role": role,
        })

    # 배정 비중(%) 계산
    total = sum(c["allocation"] or 0 for c in conds)
    if total > 0:
        for c in conds:
            if c["allocation"]:
                c["allocation_pct"] = round(c["allocation"] / total * 100, 1)
    return conds


def load_broker_fees():
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "broker_fees.json")
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"default": 2000}


def parse_detail(html):
    """종목 상세 페이지에서 증거금율·납입/환불일·인수회사별 물량 등 추출."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ")
    text = re.sub(r"\s+", " ", text)

    def find(pat, group=1):
        m = re.search(pat, text)
        return m.group(group) if m else None

    out = {}
    market = find(r"시장구분\s*(코스닥|코스피|유가증권|코넥스)")
    if market == "유가증권":
        market = "코스피"
    out["market"] = market
    out["payment_date"] = find(r"납입일\s*(\d{4}\.\d{2}\.\d{2})")
    out["refund_date"] = find(r"환불일\s*(\d{4}\.\d{2}\.\d{2})")
    out["listing_date"] = find(r"상장일\s*(\d{4}\.\d{2}\.\d{2})")
    out["par_value"] = _num(find(r"액면가\s*([\d,]+)\s*원"))
    out["total_shares"] = _num(find(r"총공모주식수\s*([\d,]+)\s*주"))
    out["mandatory_holding"] = find(r"의무보유확약\s*([\d.]+%)")
    out["max_subscription"] = find(r"청약\s*최고한도\s*:?\s*([\d,~]+)\s*주")
    # 수요예측 기관경쟁률 (예: "기관경쟁률 714.52") — 확정공모가 전에는 없음
    rate_s = find(r"기관경쟁률\s*:?\s*([\d,]+(?:\.\d+)?)")
    if rate_s:
        try:
            out["institutional_rate"] = float(rate_s.replace(",", ""))
        except ValueError:
            pass

    # 일반청약자 증거금율: '청약 최고한도' 직전에 오는 비율 (기관은 별도)
    rate = find(r"청약증거금율\s*:\s*([\d.]+)%\s*청약\s*최고한도")
    if rate is None:
        # 폴백: 마지막 증거금율 항목
        all_rates = re.findall(r"청약증거금율\s*:\s*([\d.]+)%", text)
        rate = all_rates[-1] if all_rates else None
    out["deposit_rate"] = (float(rate) / 100) if rate else None

    # 인수회사별 배정 물량 (공동주관 유불리 판단용)
    conds = parse_underwriter_table(soup)
    if conds:
        out["broker_conditions"] = conds

    return {k: v for k, v in out.items() if v is not None}


def enrich_with_details(records):
    """각 종목의 상세 페이지를 조회해 필드 보강. 실패해도 목록 데이터는 유지."""
    fees = load_broker_fees()
    default_fee = fees.get("default", 2000)
    for rec in records:
        no = rec.get("detail_no")
        if not no:
            continue
        try:
            detail = parse_detail(_fetch(DETAIL_URL.format(no=no)))
            rec.update(detail)
        except Exception as e:
            print(f"[경고] 상세 실패 {rec['name']}: {type(e).__name__}")
        # 인수회사 표가 없으면 주간사 목록으로라도 골격 생성
        if "broker_conditions" not in rec and rec.get("underwriters"):
            rec["broker_conditions"] = [
                {"broker": u} for u in rec["underwriters"]
            ]
        # 증권사별 수수료 merge (수동 큐레이션 테이블)
        for c in rec.get("broker_conditions", []):
            c["fee"] = fees.get(c["broker"], default_fee)
        time.sleep(0.4)  # 예의상 간격
    return records


def parse_listings(html):
    """신규상장 표 파싱 (상장일 + 성과)."""
    soup = BeautifulSoup(html, "html.parser")
    target = None
    for table in soup.find_all("table"):
        head = table.get_text()
        if "신규상장일" in head and "공모가" in head and "시초가" in head:
            target = table
            break
    if target is None:
        return []

    records = []
    for tr in target.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if len(cells) < 8:
            continue
        name, listing_date = cells[0], cells[1]
        if not re.fullmatch(r"\d{4}/\d{2}/\d{2}", listing_date):
            continue
        records.append({
            "name": name,
            "listing_date": listing_date.replace("/", "."),
            "current_price": _num(cells[2]),
            "offer_price": _num(cells[4]),
            "opening_price": _num(cells[6]),
            "opening_vs_offer": _clean(cells[7]),
            "first_day_close": _num(cells[8]) if len(cells) > 8 else None,
        })
    return records


def save(name, obj):
    path = os.path.join(OUT_DIR, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    print(f"[저장] {path}")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    schedule = parse_schedule(_fetch(SCHEDULE_URL))
    print(f"[수집] 청약일정 {len(schedule)}종목")
    schedule = enrich_with_details(schedule)
    enriched = sum(1 for r in schedule if "deposit_rate" in r or "refund_date" in r)
    print(f"[보강] 상세 정보 {enriched}/{len(schedule)}종목")
    listings = parse_listings(_fetch(LISTING_URL))
    print(f"[수집] 신규상장 {len(listings)}종목")

    if not schedule and not listings:
        # 파서가 완전히 깨진 경우 기존 데이터를 지우지 않도록 실패 처리
        print("[오류] 수집 결과 0건 - 페이지 구조 변경 가능성. 저장하지 않음.")
        sys.exit(1)

    save("ipos.json", schedule)
    save("listings.json", listings)
    save("meta.json", {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "source": "38communication",
        "ipos_count": len(schedule),
        "listings_count": len(listings),
    })
    print("[완료]")


if __name__ == "__main__":
    main()
