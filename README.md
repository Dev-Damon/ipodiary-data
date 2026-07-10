# ipodiary-data

[IPODiary](https://github.com/Dev-Damon) 앱이 사용하는 공모주 데이터 저장소.

GitHub Actions가 6시간마다 38커뮤니케이션에서 공모주 청약일정·신규상장 성과를
수집해 `data/` 아래 JSON으로 커밋합니다.

## 데이터

| 파일 | 내용 |
|------|------|
| `data/ipos.json` | 공모주 청약일정 (종목·청약기간·공모가·밴드·경쟁률·주간사) |
| `data/listings.json` | 신규상장 (상장일·공모가·시초가·첫날종가·현재가) |
| `data/meta.json` | 수집 시각·건수 |

앱은 아래 URL로 읽습니다:
```
https://raw.githubusercontent.com/Dev-Damon/ipodiary-data/main/data/ipos.json
https://raw.githubusercontent.com/Dev-Damon/ipodiary-data/main/data/listings.json
https://raw.githubusercontent.com/Dev-Damon/ipodiary-data/main/data/meta.json
```

## 수동 실행

```bash
pip install -r requirements.txt
python collect.py
```

또는 Actions 탭에서 `collect-ipo-data` 워크플로를 수동 실행.

## 고지

- 데이터 출처: 38커뮤니케이션 (수집·가공). 정보 제공 목적이며 투자 권유가 아닙니다.
- 원본 사이트 구조 변경 시 수집이 실패할 수 있습니다 (수집 0건이면 기존 데이터 유지).
