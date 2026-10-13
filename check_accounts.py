# -*- coding: utf-8 -*-
"""
인스타그램 계정 접근 상태를 점검하고, 접근이 안 되는 계정(비활성화 의심)을 슬랙으로 알립니다. (매일 실행)

- 점검 대상: 트래커 DB의 "계정" 열(조회수 수집 시 자동 기록) + 계정 관리 DB의 인스타그램 계정
  → 트래커에만 있는 계정은 계정 관리 DB에 자동 등록 (클라이언트 함께 기록)
- Apify 프로필 스크래퍼가 "Profile does not exist" 를 돌려주면 1분 뒤 재확인,
  그래도 안 되면 계정 관리 DB "상태" 를 "접근 불가" 로 바꾸고 슬랙 알림 (같은 계정은 1번만)
- 접근 불가 계정은 그 계정의 최근 게시물을 열어 봐서 구분:
  게시물이 열리고 작성자 이름이 다르면 → 계정 이름 변경 (점검 목록·트래커 자동 갱신 + 알림)
  게시물도 안 열리면 → 비활성화/삭제 의심 알림
- 다시 접근 가능해지면 "정상" 으로 되돌리고 복구 알림

필요 환경변수: NOTION_TOKEN, NOTION_DATABASE_ID, APIFY_TOKEN (+ SLACK_WEBHOOK_URL)
"""
import collections
import os
import re
import sys
import time

import update_stats as core

APIFY_PROFILE_ACTOR = "apify~instagram-profile-scraper"
RETRY_WAIT = 60  # 접근 불가 계정 재확인까지 대기 (초) — 일시적 오류 걸러내기

STATUS_OK = "정상"
STATUS_DOWN = "접근 불가"


def ensure_properties(token, tracker_db_id):
    """필요한 열이 없으면 추가 (이미 있으면 변화 없음)"""
    core.http_json(core.NOTION_API + "/databases/" + tracker_db_id, method="PATCH",
                   headers=core.notion_headers(token),
                   payload={"properties": {"계정": {"rich_text": {}}}})
    core.http_json(core.NOTION_API + "/databases/" + core.ACCOUNTS_DB_ID, method="PATCH",
                   headers=core.notion_headers(token),
                   payload={"properties": {
                       "상태": {"select": {"options": [
                           {"name": STATUS_OK, "color": "green"},
                           {"name": STATUS_DOWN, "color": "red"},
                       ]}},
                       "상태 확인": {"date": {}},
                   }})


def ig_username(link):
    """인스타 링크 → 계정명 (게시물 링크에 계정명이 들어간 경우 포함)"""
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", link or "")
    if m and m.group(1).lower() not in ("reel", "reels", "p", "tv", "stories", "explore"):
        return m.group(1).lower()
    return None


def check_profiles(usernames, apify_token):
    """username → True(접근 가능) / False(존재하지 않음). 응답이 애매한 계정은 결과에서 제외"""
    run = core.http_json(
        "https://api.apify.com/v2/acts/{}/runs?token={}".format(APIFY_PROFILE_ACTOR, apify_token),
        method="POST", payload={"usernames": list(usernames)})["data"]
    started = time.time()
    while True:
        status = core.http_json(
            "https://api.apify.com/v2/actor-runs/{}?token={}".format(run["id"], apify_token)
        )["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError("Apify 실행 실패: " + status)
        if time.time() - started > 600:
            raise RuntimeError("Apify 대기 시간 초과")
        time.sleep(10)
    items = core.http_json(
        "https://api.apify.com/v2/datasets/{}/items?token={}&clean=true".format(
            run["defaultDatasetId"], apify_token))
    out = {}
    for it in items:
        u = (it.get("username") or "").lower()
        if not u:
            continue
        if it.get("error") == "not_found":
            out[u] = False
        elif it.get("followersCount") is not None or it.get("id"):
            out[u] = True
    return out


def probe_rename(codes, apify_token):
    """접근 불가 계정의 게시물들을 열어 현재 작성자 계정명 확인.
    'renamed:새이름' / 'gone'(게시물도 안 열림) / None(판단 불가)"""
    if not codes:
        return None
    try:
        stats = core.fetch_instagram_stats(
            ["https://www.instagram.com/p/{}/".format(c) for c in codes], apify_token)
    except Exception as e:
        print("  게시물 확인 실패:", e)
        return None
    owners = {st.get("owner") for st in stats.values() if st.get("owner")}
    if not stats:
        return "gone"
    return "renamed:" + owners.pop() if len(owners) == 1 else None


def apply_rename(old, new, tracker_pages, account_page, token):
    """트래커 게시물의 '계정' 과 계정 관리 페이지를 새 계정명으로 변경"""
    for pg in tracker_pages:
        core.notion_update_page(pg["id"], {"계정": {"rich_text": [{"text": {"content": new}}]}}, token)
    props = {"링크": {"url": "https://www.instagram.com/{}/".format(new)}}
    if core.get_text(account_page, "계정명").lower() in ("", old):
        props["계정명"] = {"title": [{"text": {"content": new}}]}
    core.notion_update_page(account_page["id"], props, token)


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    apify_token = os.environ.get("APIFY_TOKEN")
    webhook = os.environ.get("SLACK_WEBHOOK_URL")
    if not token or not db_id or not apify_token:
        print("NOTION_TOKEN, NOTION_DATABASE_ID, APIFY_TOKEN 환경변수가 필요합니다.")
        sys.exit(1)

    ensure_properties(token, db_id)

    # 트래커의 인스타 게시물 → 계정별 클라이언트 (가장 많이 쓰인 값)
    clients = collections.defaultdict(collections.Counter)
    posts = collections.defaultdict(list)  # 계정 → [(업로드 날짜, 페이지)]
    for pg in core.notion_query_all(db_id, token):
        link = core.prop_url(pg, "링크") or ""
        if "instagram.com" not in link:
            continue
        u = core.get_text(pg, "계정").lower() or ig_username(link)
        if u:
            clients[u][core.get_select(pg, "클라이언트") or ""] += 1
            date = ((pg["properties"].get("업로드 날짜") or {}).get("date") or {}).get("start") or ""
            posts[u].append((date, pg))

    # 계정 관리 DB의 인스타 계정 + 트래커에만 있는 계정 자동 등록
    accounts = {}
    for pg in core.notion_query_all(core.ACCOUNTS_DB_ID, token):
        u = ig_username(core.prop_url(pg, "링크"))
        if u:
            accounts[u] = pg
    for u in sorted(set(clients) - set(accounts)):
        props = {
            "계정명": {"title": [{"text": {"content": u}}]},
            "링크": {"url": "https://www.instagram.com/{}/".format(u)},
            "플랫폼": {"select": {"name": "인스타그램"}},
        }
        client = clients[u].most_common(1)[0][0]
        if client:
            props["클라이언트"] = {"select": {"name": client}}
        accounts[u] = core.http_json(
            core.NOTION_API + "/pages", method="POST", headers=core.notion_headers(token),
            payload={"parent": {"database_id": core.ACCOUNTS_DB_ID}, "properties": props})
        print("  계정 관리에 자동 등록:", u)

    if not accounts:
        print("점검할 인스타그램 계정 없음")
        return
    print("인스타그램 계정 {}개 접근 상태 점검 중...".format(len(accounts)))

    result = check_profiles(accounts, apify_token)
    down = [u for u, ok in result.items() if not ok]
    if down:
        print("  접근 불가 {}개 → {}초 뒤 재확인: {}".format(len(down), RETRY_WAIT, ", ".join(down)))
        time.sleep(RETRY_WAIT)
        result.update(check_profiles(down, apify_token))
        down = [u for u, ok in result.items() if not ok]

    # 한꺼번에 다수가 사라졌다면 계정 문제가 아니라 수집기 오류일 가능성이 큼 → 상태 변경 보류
    if len(down) >= 3 and len(down) > len(result) / 2:
        print("접근 불가 계정이 비정상적으로 많음({}/{}) — 수집기 오류로 판단, 이번 점검 무시".format(
            len(down), len(result)))
        return

    today = time.strftime("%Y-%m-%d")
    alerts = []
    for u, ok in sorted(result.items()):
        pg = accounts.get(u)
        if not pg:
            continue
        prev = core.get_select(pg, "상태")
        new = STATUS_OK if ok else STATUS_DOWN
        client = core.get_select(pg, "클라이언트") or "미지정"
        link = "https://www.instagram.com/{}/".format(u)
        if new == STATUS_DOWN and prev != STATUS_DOWN:
            # 최근 게시물 2개로 이름 변경인지 확인
            recent = [p for _, p in sorted(posts.get(u, []), key=lambda x: x[0], reverse=True)]
            codes = [c for c in (core.instagram_shortcode(core.prop_url(p, "링크") or "")
                                 for p in recent[:2]) if c]
            verdict = probe_rename(codes, apify_token)
            if verdict and verdict.startswith("renamed:") and verdict[8:] != u:
                renamed = verdict[8:]
                apply_rename(u, renamed, [p for _, p in posts.get(u, [])], pg, token)
                alerts.append(":pencil2: *인스타 계정 이름 변경*  @{} → @{} ({}) · 점검 목록 자동 변경\n"
                              "https://www.instagram.com/{}/".format(u, renamed, client, renamed))
                core.notion_update_page(pg["id"], {
                    "상태": {"select": {"name": STATUS_OK}},
                    "상태 확인": {"date": {"start": today}},
                }, token)
                continue
            reason = {"gone": "게시물도 열리지 않음 → 비활성화/삭제 추정"}.get(
                verdict, "비활성화·삭제·이름 변경 중 확인 필요")
            alerts.append(":rotating_light: *인스타 계정 접근 불가*  @{} ({}) — {}\n{}".format(
                u, client, reason, link))
        elif new == STATUS_OK and prev == STATUS_DOWN:
            alerts.append(":white_check_mark: *인스타 계정 복구됨*  @{} ({})\n{}".format(u, client, link))
        core.notion_update_page(pg["id"], {
            "상태": {"select": {"name": new}},
            "상태 확인": {"date": {"start": today}},
        }, token)

    for line in alerts:
        print(" ", line.replace("\n", " "))
    if alerts and not webhook:
        print("SLACK_WEBHOOK_URL 이 없어 슬랙 알림은 보내지 못함 (노션 '상태' 열에는 기록됨)")
    core.send_slack(webhook, alerts)
    print("완료: {}개 점검, 접근 불가 {}개, 응답 없음 {}개".format(
        len(result), len(down), len(accounts) - len(result)))


if __name__ == "__main__":
    main()
