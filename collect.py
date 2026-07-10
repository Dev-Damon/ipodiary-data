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
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

SCHEDULE_URL = "http://www.38.co.kr/html/fund/index.htm?o=k"
LISTING_URL = "http://www.38.co.kr/html/fund/index.htm?o=nw"
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
        records.append({
            "name": name,
            "subscription_period": period,
            "confirmed_price": _num(cells[2].get_text()),
            "band": _clean(cells[3].get_text()),
            "competition_rate": _clean(cells[4].get_text()),
            "underwriters": underwriters,
        })
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
