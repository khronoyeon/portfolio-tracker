# -*- coding: utf-8 -*-
"""
기존 트래커 데이터베이스에 세부 지표 열을 추가하는 스크립트 (1회 실행)

추가되는 열:
    증가율        전일 대비 증가율 (%)
    시간당 조회수  최근 집계 구간의 시간당 평균 증가량
    주간 증가      7일 전 대비 증가량
    참여율        (좋아요+댓글)/조회수 — 노션 수식, 실시간
    게시 경과일    업로드 후 며칠 지났는지 — 노션 수식, 실시간
    일평균 조회수  조회수/경과일 — 노션 수식, 실시간
    기록 (자동)    일별 조회수 히스토리(스크립트 전용, 편집 금지)

사용법:
    NOTION_TOKEN=... NOTION_DATABASE_ID=... python3 upgrade_database.py
"""
import json
import os
import sys
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("NOTION_TOKEN 과 NOTION_DATABASE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    days = 'dateBetween(now(), prop("업로드 날짜"), "days")'
    properties = {
        "증가율": {"number": {"format": "percent"}},
        "시간당 조회수": {"number": {"format": "number_with_commas"}},
        "주간 증가": {"number": {"format": "number_with_commas"}},
        "참여율": {
            "formula": {
                "expression": (
                    'if(prop("조회수") > 0, '
                    'round((prop("좋아요") + prop("댓글")) / prop("조회수") * 10000) / 100, 0)'
                )
            }
        },
        "게시 경과일": {"formula": {"expression": days}},
        "일평균 조회수": {
            "formula": {
                "expression": (
                    "if({d} < 1, prop(\"조회수\"), round(prop(\"조회수\") / {d}))".format(d=days)
                )
            }
        },
        "기록 (자동)": {"rich_text": {}},
    }

    req = urllib.request.Request(
        "{}/databases/{}".format(NOTION_API, db_id),
        data=json.dumps({"properties": properties}).encode("utf-8"),
        headers={
            "Authorization": "Bearer " + token,
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        },
        method="PATCH",
    )
    with urllib.request.urlopen(req) as res:
        json.loads(res.read().decode("utf-8"))
    print("데이터베이스 업그레이드 완료! 추가된 열:", ", ".join(properties.keys()))


if __name__ == "__main__":
    main()
