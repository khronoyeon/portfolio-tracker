# -*- coding: utf-8 -*-
"""
노션 "계정 관리" DB의 계정들의 팔로워/구독자 수를 수집합니다. (주 1회 실행)

- 유튜브: 공식 API (무료) — 채널 링크(@핸들 또는 /channel/UC...) 지원
- 인스타그램: Apify 프로필 스크래퍼 — instagram.com/계정명 링크 지원
- 팔로워, 주간 증감, 히스토리("기록 (자동)")를 노션에 업데이트

필요 환경변수: NOTION_TOKEN (+ YOUTUBE_API_KEY / APIFY_TOKEN)
"""
import json
import os
import re
import sys
import time

import update_stats as core

APIFY_PROFILE_ACTOR = "apify~instagram-profile-scraper"


def parse_account(link):
    """링크 → (플랫폼, 식별자)"""
    if not link:
        return None, None
    m = re.search(r"youtube\.com/channel/(UC[A-Za-z0-9_-]+)", link)
    if m:
        return "youtube_id", m.group(1)
    m = re.search(r"youtube\.com/(@[A-Za-z0-9_.\-]+)", link)
    if m:
        return "youtube_handle", m.group(1)
    m = re.search(r"instagram\.com/([A-Za-z0-9_.]+)", link)
    if m and m.group(1) not in ("reel", "reels", "p", "tv"):
        return "instagram", m.group(1).lower()
    if link.startswith("@"):
        return "youtube_handle", link
    return None, None


def fetch_youtube(kind, ident, api_key):
    """채널 구독자 수 + 채널명"""
    param = "id=" + ident if kind == "youtube_id" else "forHandle=" + ident.lstrip("@")
    res = core.http_json(
        "https://www.googleapis.com/youtube/v3/channels?part=statistics,snippet&{}&key={}".format(
            param, api_key))
    items = res.get("items") or []
    if not items:
        return None
    st, sn = items[0].get("statistics", {}), items[0].get("snippet", {})
    return {"followers": int(st.get("subscriberCount", 0)), "name": sn.get("title")}


def fetch_instagram(usernames, apify_token):
    """username(소문자) → {followers, name}"""
    run = core.http_json(
        "https://api.apify.com/v2/acts/{}/runs?token={}".format(APIFY_PROFILE_ACTOR, apify_token),
        method="POST", payload={"usernames": list(usernames)})["data"]
    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    started = time.time()
    while True:
        status = core.http_json(
            "https://api.apify.com/v2/actor-runs/{}?token={}".format(run_id, apify_token)
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
            dataset_id, apify_token))
    out = {}
    for it in items:
        u = (it.get("username") or "").lower()
        if u:
            out[u] = {"followers": it.get("followersCount") or 0,
                      "name": it.get("fullName") or u}
    return out


def update_page(page, stats, token):
    now_ts = int(time.time())
    hist = core.load_history(page)
    prev = hist[-1][1] if hist else None
    followers = stats["followers"]
    hist.append([now_ts, followers])
    hist = hist[-60:]
    props = {
        "팔로워": {"number": followers},
        "기록 (자동)": {"rich_text": [{"text": {"content": json.dumps(hist, separators=(",", ":"))}}]},
        "마지막 업데이트": {"date": {"start": time.strftime("%Y-%m-%d")}},
    }
    if prev is not None:
        props["주간 증감"] = {"number": followers - prev}
    if stats.get("name") and core.get_text(page, "계정명") == "":
        props["계정명"] = {"title": [{"text": {"content": stats["name"][:100]}}]}
    core.notion_update_page(page["id"], props, token)


def main():
    token = os.environ.get("NOTION_TOKEN")
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    apify_token = os.environ.get("APIFY_TOKEN")
    if not token:
        print("NOTION_TOKEN 환경변수가 필요합니다.")
        sys.exit(1)

    pages = core.notion_query_all(core.ACCOUNTS_DB_ID, token)
    yt_pages, ig_pages, skipped = [], [], 0
    for pg in pages:
        kind, ident = parse_account(core.prop_url(pg, "링크"))
        if kind in ("youtube_id", "youtube_handle"):
            yt_pages.append((pg, kind, ident))
        elif kind == "instagram":
            ig_pages.append((pg, ident))
        else:
            skipped += 1
    print("계정 {}개 (유튜브 {} / 인스타 {} / 인식 불가 {})".format(
        len(pages), len(yt_pages), len(ig_pages), skipped))

    updated = 0
    for pg, kind, ident in yt_pages:
        if not yt_key:
            print("YOUTUBE_API_KEY 없음 — 유튜브 건너뜀")
            break
        stats = fetch_youtube(kind, ident, yt_key)
        if not stats:
            print("  채널을 찾지 못함:", ident)
            continue
        update_page(pg, stats, token)
        core.notion_update_page(pg["id"], {"플랫폼": {"select": {"name": "유튜브"}}}, token)
        updated += 1

    if ig_pages:
        if not apify_token:
            print("APIFY_TOKEN 없음 — 인스타그램 건너뜀")
        else:
            print("인스타그램 팔로워 수집 중...")
            ig_stats = fetch_instagram({u for _, u in ig_pages}, apify_token)
            for pg, u in ig_pages:
                stats = ig_stats.get(u)
                if not stats:
                    print("  프로필을 찾지 못함:", u)
                    continue
                update_page(pg, stats, token)
                core.notion_update_page(pg["id"], {"플랫폼": {"select": {"name": "인스타그램"}}}, token)
                updated += 1

    print("완료: 계정 {}개 팔로워 업데이트".format(updated))


if __name__ == "__main__":
    main()
