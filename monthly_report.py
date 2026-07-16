# -*- coding: utf-8 -*-
"""
매월 1일, 지난달 제작 콘텐츠 리포트를 노션 페이지로 자동 생성합니다.

- 지난달(업로드 날짜 기준) 영상들의 개수·총/평균 조회수·플랫폼 구성
- 조회수 TOP 3
- 클라이언트별 요약 표
- 담당자(기획자·편집자)별 요약

필요 환경변수: NOTION_TOKEN, NOTION_DATABASE_ID
"""
import datetime
import os
import sys

import update_stats as core

# 리포트 페이지를 생성할 부모 페이지 ("유니콘하우스 숏폼 데이터베이스")
PARENT_PAGE_ID = "fa02e440-0540-83bd-be7c-810717bb95f8"


def txt(s):
    return {"type": "text", "text": {"content": str(s)}}


def heading(s, level=2):
    key = "heading_{}".format(level)
    return {"object": "block", "type": key, key: {"rich_text": [txt(s)]}}


def bullet(s):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [txt(s)]}}


def paragraph(s):
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [txt(s)]}}


def table(header, rows):
    def row(cells):
        return {"object": "block", "type": "table_row",
                "table_row": {"cells": [[txt(c)] for c in cells]}}
    return {"object": "block", "type": "table",
            "table": {"table_width": len(header), "has_column_header": True,
                      "children": [row(header)] + [row(r) for r in rows]}}


def month_range(today=None):
    today = today or datetime.date.today()
    first_this = today.replace(day=1)
    last_prev = first_this - datetime.timedelta(days=1)
    first_prev = last_prev.replace(day=1)
    return first_prev, last_prev


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("NOTION_TOKEN 과 NOTION_DATABASE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    start, end = month_range()
    label = "{}년 {}월".format(start.year, start.month)

    pages = core.notion_query_all(db_id, token)
    items = []
    for page in pages:
        p = page["properties"]
        date = (((p.get("업로드 날짜") or {}).get("date")) or {}).get("start")
        views = (p.get("조회수") or {}).get("number")
        if not date or views is None:
            continue
        d = datetime.date.fromisoformat(date[:10])
        if not (start <= d <= end):
            continue
        items.append({
            "title": core.get_text(page, "제목") or "(제목 없음)",
            "client": core.get_select(page, "클라이언트") or "미지정",
            "platform": core.get_select(page, "플랫폼") or "기타",
            "planner": core.get_select(page, "기획자"),
            "editor": core.get_select(page, "편집자"),
            "views": views,
            "likes": (p.get("좋아요") or {}).get("number") or 0,
        })

    if not items:
        print("{} 업로드 영상이 없어 리포트를 생성하지 않습니다.".format(label))
        return

    total = sum(i["views"] for i in items)
    avg = round(total / len(items))
    yt = [i for i in items if i["platform"] == "유튜브"]
    ig = [i for i in items if i["platform"] == "인스타그램"]
    top3 = sorted(items, key=lambda i: -i["views"])[:3]

    by_client = {}
    for i in items:
        c = by_client.setdefault(i["client"], {"n": 0, "views": 0})
        c["n"] += 1
        c["views"] += i["views"]

    by_member = {}
    for i in items:
        for role, name in (("기획", i["planner"]), ("편집", i["editor"])):
            if not name:
                continue
            m = by_member.setdefault((name, role), {"n": 0, "views": 0})
            m["n"] += 1
            m["views"] += i["views"]

    f = "{:,}".format
    blocks = [
        heading("요약", 2),
        bullet("제작 콘텐츠: {}개 (유튜브 {} · 인스타그램 {})".format(len(items), len(yt), len(ig))),
        bullet("총 조회수: {}회".format(f(total))),
        bullet("영상당 평균 조회수: {}회".format(f(avg))),
        heading("조회수 TOP 3", 2),
    ]
    for rank, i in enumerate(top3, 1):
        blocks.append(bullet("{}위 — {} ({}) · {}회".format(rank, i["title"], i["client"], f(i["views"]))))

    blocks.append(heading("클라이언트별", 2))
    blocks.append(table(
        ["클라이언트", "영상 수", "총 조회수"],
        [[c, str(v["n"]), f(v["views"])] for c, v in
         sorted(by_client.items(), key=lambda x: -x[1]["views"])],
    ))

    if by_member:
        blocks.append(heading("담당자별", 2))
        blocks.append(table(
            ["이름", "역할", "영상 수", "총 조회수", "평균"],
            [[name, role, str(v["n"]), f(v["views"]), f(round(v["views"] / v["n"]))]
             for (name, role), v in sorted(by_member.items(), key=lambda x: -x[1]["views"])],
        ))

    blocks.append(paragraph("이 리포트는 매월 1일 자동 생성됩니다. 조회수는 생성 시점 기준입니다."))

    core.http_json(
        core.NOTION_API + "/pages", method="POST",
        payload={
            "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
            "icon": {"type": "emoji", "emoji": "📊"},
            "properties": {"title": {"title": [txt("{} 콘텐츠 리포트".format(label))]}},
            "children": blocks,
        },
        headers=core.notion_headers(token),
    )
    print("{} 리포트 생성 완료 (영상 {}개, 총 {}회)".format(label, len(items), f(total)))


if __name__ == "__main__":
    main()
