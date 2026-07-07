# -*- coding: utf-8 -*-
"""
노션 포트폴리오 DB의 모든 영상 링크를 순회하며
유튜브(공식 API) / 인스타그램(Apify) 조회수·좋아요·댓글을 수집해 업데이트합니다.

- 조회수: 최신 값으로 덮어쓰기
- 일일 증가: (이번 조회수 - 직전 저장된 조회수), 최초 수집 시에는 비워둠
- 제목/업로드 날짜가 비어 있으면 플랫폼에서 가져온 값으로 자동 입력

필요 환경변수:
    NOTION_TOKEN        노션 통합 시크릿
    NOTION_DATABASE_ID  포트폴리오 데이터베이스 ID
    YOUTUBE_API_KEY     YouTube Data API v3 키 (유튜브 링크가 있을 때만 필수)
    APIFY_TOKEN         Apify API 토큰 (인스타그램 링크가 있을 때만 필수)
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NOTION_API = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
APIFY_ACTOR = "apify~instagram-scraper"
APIFY_POLL_INTERVAL = 15      # 초
APIFY_TIMEOUT = 15 * 60       # 최대 15분 대기


# ---------------------------------------------------------------- HTTP 공통

def http_json(url, method="GET", payload=None, headers=None, timeout=60):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError("HTTP {} {} -> {}\n{}".format(e.code, method, url, body[:500]))


def notion_headers(token):
    return {
        "Authorization": "Bearer " + token,
        "Notion-Version": NOTION_VERSION,
    }


# ---------------------------------------------------------------- 노션

def notion_query_all(db_id, token):
    """데이터베이스의 모든 페이지를 페이지네이션 처리하며 가져오기"""
    pages, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor:
            payload["start_cursor"] = cursor
        res = http_json(
            "{}/databases/{}/query".format(NOTION_API, db_id),
            method="POST", payload=payload, headers=notion_headers(token),
        )
        pages.extend(res.get("results", []))
        if not res.get("has_more"):
            return pages
        cursor = res.get("next_cursor")


def prop_number(page, name):
    p = page["properties"].get(name) or {}
    return p.get("number")


def prop_url(page, name):
    p = page["properties"].get(name) or {}
    return p.get("url")


def prop_title_empty(page, name):
    p = page["properties"].get(name) or {}
    parts = p.get("title") or []
    return "".join(t.get("plain_text", "") for t in parts).strip() == ""


def prop_date_empty(page, name):
    p = page["properties"].get(name) or {}
    return not (p.get("date") or {}).get("start")


def notion_update_page(page_id, properties, token):
    http_json(
        "{}/pages/{}".format(NOTION_API, page_id),
        method="PATCH", payload={"properties": properties}, headers=notion_headers(token),
    )


# ---------------------------------------------------------------- 링크 파싱

def youtube_video_id(url):
    for pattern in (
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/live/([A-Za-z0-9_-]{11})",
    ):
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def instagram_shortcode(url):
    m = re.search(r"instagram\.com/(?:[^/]+/)?(?:reel|reels|p|tv)/([A-Za-z0-9_-]+)", url)
    return m.group(1) if m else None


# ---------------------------------------------------------------- 유튜브

def fetch_youtube_stats(video_ids, api_key):
    """video_id -> {views, likes, comments, title, published} (50개씩 배치 조회)"""
    result = {}
    ids = list(video_ids)
    for i in range(0, len(ids), 50):
        batch = ids[i:i + 50]
        url = (
            "https://www.googleapis.com/youtube/v3/videos"
            "?part=statistics,snippet&id={}&key={}".format(",".join(batch), api_key)
        )
        res = http_json(url)
        for item in res.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            result[item["id"]] = {
                "views": int(stats.get("viewCount", 0)),
                "likes": int(stats["likeCount"]) if stats.get("likeCount") else None,
                "comments": int(stats["commentCount"]) if stats.get("commentCount") else None,
                "title": snippet.get("title"),
                "published": (snippet.get("publishedAt") or "")[:10] or None,
            }
    return result


# ---------------------------------------------------------------- 인스타그램 (Apify)

def fetch_instagram_stats(urls, apify_token):
    """shortcode -> {views, likes, comments, title, published}"""
    run = http_json(
        "https://api.apify.com/v2/acts/{}/runs?token={}".format(APIFY_ACTOR, apify_token),
        method="POST",
        payload={
            "directUrls": list(urls),
            "resultsType": "posts",
            "resultsLimit": len(urls),
            "addParentData": False,
        },
    )["data"]

    run_id, dataset_id = run["id"], run["defaultDatasetId"]
    started = time.time()
    while True:
        status = http_json(
            "https://api.apify.com/v2/actor-runs/{}?token={}".format(run_id, apify_token)
        )["data"]["status"]
        if status == "SUCCEEDED":
            break
        if status in ("FAILED", "ABORTED", "TIMED-OUT"):
            raise RuntimeError("Apify 실행 실패: " + status)
        if time.time() - started > APIFY_TIMEOUT:
            raise RuntimeError("Apify 실행 대기 시간 초과 (15분)")
        time.sleep(APIFY_POLL_INTERVAL)

    items = http_json(
        "https://api.apify.com/v2/datasets/{}/items?token={}&clean=true".format(
            dataset_id, apify_token
        )
    )

    result = {}
    for item in items:
        code = item.get("shortCode") or instagram_shortcode(item.get("url") or "")
        if not code:
            continue
        views = max(
            item.get("videoPlayCount") or 0,
            item.get("videoViewCount") or 0,
            item.get("playCount") or 0,
        )
        likes = item.get("likesCount")
        caption = (item.get("caption") or "").strip().splitlines()
        result[code] = {
            "views": views,
            "likes": likes if isinstance(likes, int) and likes >= 0 else None,
            "comments": item.get("commentsCount"),
            "title": caption[0][:80] if caption else None,
            "published": (item.get("timestamp") or "")[:10] or None,
        }
    return result


# ---------------------------------------------------------------- 메인

def build_properties(page, platform, stats):
    """수집한 통계로 노션 페이지 속성 업데이트 payload 생성"""
    prev_views = prop_number(page, "조회수")
    new_views = stats["views"]

    props = {
        "조회수": {"number": new_views},
        "플랫폼": {"select": {"name": platform}},
        "마지막 업데이트": {"date": {"start": time.strftime("%Y-%m-%d")}},
    }
    if prev_views is not None:
        props["일일 증가"] = {"number": new_views - prev_views}
    if stats.get("likes") is not None:
        props["좋아요"] = {"number": stats["likes"]}
    if stats.get("comments") is not None:
        props["댓글"] = {"number": stats["comments"]}
    if stats.get("title") and prop_title_empty(page, "제목"):
        props["제목"] = {"title": [{"text": {"content": stats["title"][:200]}}]}
    if stats.get("published") and prop_date_empty(page, "업로드 날짜"):
        props["업로드 날짜"] = {"date": {"start": stats["published"]}}
    return props


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    yt_key = os.environ.get("YOUTUBE_API_KEY")
    apify_token = os.environ.get("APIFY_TOKEN")

    if not token or not db_id:
        print("NOTION_TOKEN 과 NOTION_DATABASE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    pages = notion_query_all(db_id, token)
    print("노션 페이지 {}개 조회".format(len(pages)))

    yt_pages, ig_pages, skipped = [], [], 0
    for page in pages:
        link = prop_url(page, "링크")
        if not link:
            skipped += 1
            continue
        vid = youtube_video_id(link)
        if vid:
            yt_pages.append((page, vid))
            continue
        code = instagram_shortcode(link)
        if code:
            ig_pages.append((page, code, link))
        else:
            skipped += 1
            print("  링크 형식을 인식하지 못함:", link)

    print("유튜브 {}개 / 인스타그램 {}개 / 건너뜀 {}개".format(
        len(yt_pages), len(ig_pages), skipped))

    yt_stats, ig_stats = {}, {}
    if yt_pages:
        if not yt_key:
            print("YOUTUBE_API_KEY 가 없어 유튜브 링크를 건너뜁니다.")
            yt_pages = []
        else:
            yt_stats = fetch_youtube_stats({vid for _, vid in yt_pages}, yt_key)
    if ig_pages:
        if not apify_token:
            print("APIFY_TOKEN 이 없어 인스타그램 링크를 건너뜁니다.")
            ig_pages = []
        else:
            print("Apify로 인스타그램 데이터 수집 중... (수 분 소요)")
            ig_stats = fetch_instagram_stats([link for _, _, link in ig_pages], apify_token)

    updated, failed = 0, 0
    for page, vid in yt_pages:
        stats = yt_stats.get(vid)
        if not stats:
            failed += 1
            print("  유튜브 통계 없음 (삭제/비공개?):", vid)
            continue
        notion_update_page(page["id"], build_properties(page, "유튜브", stats), token)
        updated += 1

    for page, code, link in ig_pages:
        stats = ig_stats.get(code)
        if not stats:
            failed += 1
            print("  인스타그램 통계 없음 (삭제/비공개?):", link)
            continue
        notion_update_page(page["id"], build_properties(page, "인스타그램", stats), token)
        updated += 1

    print("완료: {}개 업데이트, {}개 실패".format(updated, failed))
    if failed and not updated:
        sys.exit(1)


if __name__ == "__main__":
    main()
