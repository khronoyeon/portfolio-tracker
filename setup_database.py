# -*- coding: utf-8 -*-
"""
노션에 포트폴리오 조회수 트래커 데이터베이스를 생성하는 스크립트 (최초 1회만 실행)

사용법:
    NOTION_TOKEN=secret_xxx NOTION_PARENT_PAGE_ID=xxxx python3 setup_database.py

- NOTION_TOKEN: https://www.notion.so/my-integrations 에서 만든 통합(Integration) 시크릿
- NOTION_PARENT_PAGE_ID: 데이터베이스를 넣을 노션 페이지 ID
  (페이지 URL 끝의 32자리 문자열, 예: notion.so/내페이지-a1b2c3... 의 a1b2c3...)
  ※ 해당 페이지에서 [...] 메뉴 → 연결(Connections) → 만든 통합을 추가해야 합니다.
"""
import json
import os
import re
import sys
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def notion_request(path, payload, token):
    req = urllib.request.Request(
        NOTION_API + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req) as res:
        return json.loads(res.read().decode("utf-8"))


def normalize_page_id(raw):
    """URL이나 하이픈 없는 ID를 표준 UUID 형태로 변환"""
    m = re.search(r"[0-9a-f]{32}", raw.replace("-", "").lower())
    if not m:
        print("페이지 ID를 인식할 수 없습니다:", raw)
        sys.exit(1)
    s = m.group(0)
    return "{}-{}-{}-{}-{}".format(s[0:8], s[8:12], s[12:16], s[16:20], s[20:32])


def main():
    token = os.environ.get("NOTION_TOKEN")
    parent = os.environ.get("NOTION_PARENT_PAGE_ID")
    if not token or not parent:
        print("NOTION_TOKEN 과 NOTION_PARENT_PAGE_ID 환경변수를 설정해주세요.")
        sys.exit(1)

    payload = {
        "parent": {"type": "page_id", "page_id": normalize_page_id(parent)},
        "is_inline": True,
        "icon": {"type": "emoji", "emoji": "📊"},
        "title": [{"type": "text", "text": {"content": "포트폴리오 조회수 트래커"}}],
        "properties": {
            "제목": {"title": {}},
            "링크": {"url": {}},
            "클라이언트": {"select": {"options": []}},
            "플랫폼": {
                "select": {
                    "options": [
                        {"name": "유튜브", "color": "red"},
                        {"name": "인스타그램", "color": "pink"},
                    ]
                }
            },
            "업로드 날짜": {"date": {}},
            "조회수": {"number": {"format": "number_with_commas"}},
            "일일 증가": {"number": {"format": "number_with_commas"}},
            "증가율": {"number": {"format": "percent"}},
            "시간당 조회수": {"number": {"format": "number_with_commas"}},
            "주간 증가": {"number": {"format": "number_with_commas"}},
            "좋아요": {"number": {"format": "number_with_commas"}},
            "댓글": {"number": {"format": "number_with_commas"}},
            "게시 경과일": {
                "formula": {"expression": 'dateBetween(now(), prop("업로드 날짜"), "days")'}
            },
            "썸네일": {"url": {}},
            "기록 (자동)": {"rich_text": {}},
            "마지막 업데이트": {"date": {}},
        },
    }

    db = notion_request("/databases", payload, token)
    print("데이터베이스 생성 완료!")
    print("  URL:", db.get("url"))
    print("  DATABASE_ID:", db["id"].replace("-", ""))
    print()
    print("위 DATABASE_ID 를 GitHub Secrets 의 NOTION_DATABASE_ID 로 등록하세요.")


if __name__ == "__main__":
    main()
