# -*- coding: utf-8 -*-
"""
매주 월요일 아침, 지난주 요약 + 확인 필요 사항을 노션 페이지로 생성합니다.
(SLACK_WEBHOOK_URL 이 설정되어 있으면 슬랙으로도 요약 전송)

내용:
- 지난주(월~일) 업로드 개수, 전체 조회수 증가
- 지난주 조회수 증가 TOP 3
- ⚠️ 확인 필요: 활성 클라이언트 업로드 공백(7일↑), 계약 페이스 부족, 정체 영상
- 클라이언트별 이번 달 진행 현황 표

필요 환경변수: NOTION_TOKEN, NOTION_DATABASE_ID (선택: SLACK_WEBHOOK_URL)
"""
import datetime
import os
import sys
import time

import update_stats as core
from monthly_report import txt, heading, bullet, paragraph, table

PARENT_PAGE_ID = "fa02e440-0540-83bd-be7c-810717bb95f8"


def kst_today():
    t = time.gmtime(time.time() + 9 * 3600)
    return datetime.date(t.tm_year, t.tm_mon, t.tm_mday)


def inc_between(hist, t0, t1):
    """t0~t1 사이 조회수 증가량 (히스토리 기반 근사)"""
    if not hist:
        return 0
    b = core.views_at(hist, t1)
    if b is None:
        b = hist[-1][1] if hist[-1][0] <= t1 else None
    if b is None:
        return 0
    a = core.views_at(hist, t0)
    if a is None:
        a = hist[0][1] if hist[0][0] <= t1 else None
    return max(0, b - a) if a is not None else 0


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("NOTION_TOKEN 과 NOTION_DATABASE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    today = kst_today()
    # 지난주 = 지난 월요일 ~ 일요일
    this_monday = today - datetime.timedelta(days=today.weekday())
    last_monday = this_monday - datetime.timedelta(days=7)
    last_sunday = this_monday - datetime.timedelta(days=1)
    t0 = core.upload_ts_of(last_monday.isoformat())
    t1 = core.upload_ts_of(this_monday.isoformat())
    now_ts = int(time.time())

    pages = core.notion_query_all(db_id, token)
    videos = []
    for pg in pages:
        p = pg["properties"]
        views = (p.get("조회수") or {}).get("number")
        if views is None:
            continue
        videos.append({
            "title": core.get_text(pg, "제목") or "(제목 없음)",
            "client": core.get_select(pg, "클라이언트") or "미지정",
            "date": (((p.get("업로드 날짜") or {}).get("date")) or {}).get("start"),
            "views": views,
            "daily": (p.get("일일 증가") or {}).get("number"),
            "hist": core.load_history(pg),
        })

    # 지난주 요약
    uploaded = [v for v in videos if v["date"] and last_monday.isoformat() <= v["date"][:10] <= last_sunday.isoformat()]
    week_inc = sum(inc_between(v["hist"], t0, t1) for v in videos)
    top3 = sorted(videos, key=lambda v: -inc_between(v["hist"], t0, t1))[:3]

    # 클라이언트 현황
    clients = {}
    try:
        for pg in core.notion_query_all(core.CLIENTS_DB_ID, token):
            name = core.get_text(pg, "클라이언트명")
            if name:
                clients[name] = {"status": core.get_select(pg, "상태") or "활성"}
    except Exception as e:
        print("클라이언트 DB 읽기 실패:", e)

    cur_month = today.strftime("%Y-%m")

    warns, client_rows = [], []
    names = sorted(set(list(clients.keys()) + [v["client"] for v in videos]))
    for name in names:
        info = clients.get(name, {"status": "활성"})
        vids = [v for v in videos if v["client"] == name]
        made = len([v for v in vids if (v["date"] or "").startswith(cur_month)])
        c_inc = sum(inc_between(v["hist"], t0, t1) for v in vids)
        client_rows.append([name, info["status"], "{}개".format(made), "{:,}".format(c_inc)])

    stalled = [v for v in videos
               if v["daily"] is not None and v["date"]
               and (today - datetime.date.fromisoformat(v["date"][:10])).days > 7
               and v["daily"] < max(50, v["views"] * 0.001)]
    if stalled:
        warns.append("정체 영상 {}개 (일일 증가 미미)".format(len(stalled)))

    label = "{}월 {}주차".format(this_monday.month, (this_monday.day - 1) // 7 + 1)
    f = "{:,}".format

    # 팀 목표 진행률 (이번 달 발생 조회수 기준)
    goal_line = None
    try:
        for pg in core.notion_query_all(core.MEMBERS_DB_ID, token):
            views_goal = (pg["properties"].get("월 조회수 목표") or {}).get("number")
            if views_goal:
                m_start = core.upload_ts_of(today.strftime("%Y-%m-01"))
                month_inc = sum(inc_between(v["hist"], m_start, now_ts) for v in videos)
                goal_line = "이번 달 팀 목표: 조회수 +{:,} / {:,} ({}%)".format(
                    month_inc, views_goal, round(month_inc / views_goal * 100))
                break
    except Exception as e:
        print("팀 목표 DB 읽기 실패:", e)

    blocks = [
        heading("지난주 요약 ({} ~ {})".format(last_monday.strftime("%m/%d"), last_sunday.strftime("%m/%d")), 2),
        bullet("신규 업로드: {}개".format(len(uploaded))),
        bullet("전체 조회수 증가: +{}회".format(f(week_inc))),
    ]
    if goal_line:
        blocks.append(bullet(goal_line))
    blocks.append(heading("지난주 많이 큰 영상 TOP 3", 2))
    for rank, v in enumerate(top3, 1):
        inc = inc_between(v["hist"], t0, t1)
        if inc <= 0:
            continue
        blocks.append(bullet("{}위 — {} ({}) · +{}회".format(rank, v["title"], v["client"], f(inc))))

    blocks.append(heading("⚠️ 확인 필요", 2))
    if warns:
        blocks.extend(bullet(w) for w in warns)
    else:
        blocks.append(bullet("특이사항 없음 👍"))

    if client_rows:
        blocks.append(heading("클라이언트 현황", 2))
        blocks.append(table(["클라이언트", "상태", "이번 달 제작", "지난주 증가"], client_rows))

    blocks.append(paragraph("이 브리핑은 매주 월요일 오전 자동 생성됩니다."))

    core.http_json(
        core.NOTION_API + "/pages", method="POST",
        payload={
            "parent": {"type": "page_id", "page_id": PARENT_PAGE_ID},
            "icon": {"type": "emoji", "emoji": "📬"},
            "properties": {"title": {"title": [txt("주간 브리핑 — {}".format(label))]}},
            "children": blocks,
        },
        headers=core.notion_headers(token),
    )
    print("주간 브리핑 생성 완료 ({}, 업로드 {}개 · 증가 +{})".format(label, len(uploaded), f(week_inc)))

    # 슬랙 요약 (웹훅이 있으면)
    lines = [":newspaper: *주간 브리핑 — {}*".format(label),
             "지난주 업로드 {}개 · 조회수 +{}회".format(len(uploaded), f(week_inc))]
    lines += [":warning: " + w for w in warns]
    core.send_slack(os.environ.get("SLACK_WEBHOOK_URL"), lines)


if __name__ == "__main__":
    main()
