# -*- coding: utf-8 -*-
"""
노션 포트폴리오 DB의 모든 영상 링크를 순회하며
유튜브(공식 API) / 인스타그램(Apify) 조회수·좋아요·댓글을 수집해 업데이트합니다.

- 조회수/좋아요/댓글: 최신 값으로 덮어쓰기
- 일일 증가·증가율·시간당 조회수: 직전 수집 시점과 비교해 계산
- 주간 증가: "기록 (자동)" 열에 쌓인 일별 히스토리에서 7일 전 값과 비교
- 제목/업로드 날짜가 비어 있으면 플랫폼에서 가져온 값으로 자동 입력

필요 환경변수:
    NOTION_TOKEN        노션 통합 시크릿
    NOTION_DATABASE_ID  포트폴리오 데이터베이스 ID
    YOUTUBE_API_KEY     YouTube Data API v3 키 (유튜브 링크가 있을 때만 필수)
    APIFY_TOKEN         Apify API 토큰 (인스타그램 링크가 있을 때만 필수)
"""
import calendar
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


def load_history(page):
    """'기록 (자동)' 열의 [[unix시각, 조회수], ...] 히스토리 읽기"""
    p = page["properties"].get("기록 (자동)") or {}
    text = "".join(t.get("plain_text", "") for t in p.get("rich_text") or [])
    try:
        hist = json.loads(text)
        return hist if isinstance(hist, list) else []
    except ValueError:
        return []


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
            "thumbnail": item.get("displayUrl"),
        }
    return result


# ---------------------------------------------------------------- 메인

HISTORY_KEEP = 60  # 히스토리 보관 개수 (일 단위 실행 기준 약 두 달치)

# 조회수 돌파 알림 기준 (슬랙)
MILESTONES = [100_000, 500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000, 100_000_000]

# 인스타그램 수집 비용 절약: 업로드 후 이 일수가 지난 영상은 주 1회(월요일)만 수집
OLD_AFTER_DAYS = 30
FULL_COLLECT_WEEKDAY = 0  # 0 = 월요일 (UTC 기준, KST 오전 9시 실행 시 같은 요일)

# 신작 집중 관찰: FRESH_ONLY=1 로 실행하면 이 일수 이내 신작만 수집 (3시간 주기 워크플로용)
FRESH_DAYS = 3

FIRST48_HOURS = 48  # 초동 조회수 측정 시점 (업로드 후 시간)


def upload_ts_of(date_str):
    """'YYYY-MM-DD' (KST 자정 기준) → 유닉스 시각"""
    try:
        return calendar.timegm(time.strptime(date_str[:10], "%Y-%m-%d")) - 9 * 3600
    except (ValueError, TypeError):
        return None


def is_old_video(page, now_ts):
    date = (((page["properties"].get("업로드 날짜") or {}).get("date")) or {}).get("start")
    ts = upload_ts_of(date) if date else None
    return ts is not None and now_ts - ts > OLD_AFTER_DAYS * 86400


def is_fresh_video(page, now_ts):
    """업로드 FRESH_DAYS 이내 신작 (날짜가 아직 없는 새 링크도 신작으로 취급)"""
    date = (((page["properties"].get("업로드 날짜") or {}).get("date")) or {}).get("start")
    if not date:
        return True
    ts = upload_ts_of(date)
    return ts is not None and now_ts - ts <= FRESH_DAYS * 86400


def views_at(hist, target_ts):
    """히스토리에서 특정 시점의 조회수를 선형 보간으로 계산 (불가능하면 None)"""
    if not hist:
        return None
    before = [h for h in hist if h[0] <= target_ts]
    after = [h for h in hist if h[0] > target_ts]
    if before and after:
        (t0, v0), (t1, v1) = before[-1], after[0]
        if t1 == t0:
            return v1
        return round(v0 + (v1 - v0) * (target_ts - t0) / (t1 - t0))
    if after and not before:
        # 첫 기록이 목표 시점 이후: 하루 이내 기록이면 근사치로 인정
        t1, v1 = after[0]
        return v1 if t1 - target_ts <= 86400 else None
    return None  # 아직 목표 시점 전이거나 계산 불가


def crossed_milestones(prev_views, new_views):
    """직전 수집 이후 새로 돌파한 조회수 마일스톤 목록"""
    if prev_views is None:
        return []  # 첫 수집(과거 영상 등록 시점)에는 알림 생략
    return [m for m in MILESTONES if prev_views < m <= new_views]


def milestone_label(m):
    return "{}만".format(m // 10_000) if m < 100_000_000 else "{}억".format(m // 100_000_000)


def get_text(page, name):
    p = page["properties"].get(name) or {}
    parts = p.get("title") or p.get("rich_text") or []
    return "".join(t.get("plain_text", "") for t in parts).strip()


def get_select(page, name):
    p = page["properties"].get(name) or {}
    return ((p.get("select") or {}) or {}).get("name")


def send_slack(webhook_url, lines):
    if not webhook_url or not lines:
        return
    try:
        http_json(webhook_url, method="POST", payload={"text": "\n".join(lines)})
        print("슬랙 알림 전송: {}건".format(len(lines)))
    except Exception as e:
        print("슬랙 알림 실패 (수집은 정상 완료):", e)


def build_properties(page, platform, stats):
    """수집한 통계로 노션 페이지 속성 업데이트 payload 생성"""
    now_ts = int(time.time())
    new_views = stats["views"]

    hist = load_history(page)
    if not hist:
        # 히스토리 도입 전 데이터: 기존 조회수를 24시간 전 값으로 간주
        prev_views = prop_number(page, "조회수")
        if prev_views is not None:
            hist = [[now_ts - 86400, prev_views]]

    props = {
        "조회수": {"number": new_views},
        "플랫폼": {"select": {"name": platform}},
        "마지막 업데이트": {"date": {"start": time.strftime("%Y-%m-%d")}},
    }

    prev = hist[-1] if hist else None
    hours_since = (now_ts - prev[0]) / 3600.0 if prev else None
    milestones = crossed_milestones(prev[1] if prev else None, new_views)

    if prev and hours_since >= 1:
        delta_last = new_views - prev[1]
        props["시간당 조회수"] = {"number": round(delta_last / hours_since)}

        # 일일 증가: 24시간 전 시점 대비 (수집 주기가 3시간이든 1주일이든 일관된 의미 유지)
        if hours_since > 36:
            base = prev[1]
            daily = round(delta_last * 24 / hours_since)  # 오래된 영상: 하루 평균으로 환산
        else:
            target = now_ts - 86400
            base = views_at(hist, target)
            if base is None:
                base = hist[0][1] if hist[0][0] > target else prev[1]
            daily = new_views - base
        props["일일 증가"] = {"number": daily}
        if base and base > 0:
            props["증가율"] = {"number": round(daily / base, 4)}

        # 주간 증가: 7일 이전 기록 중 가장 최근 값과 비교 (기록이 2일 이상이면 근사치라도 계산)
        week_ago = now_ts - 7 * 86400
        base = None
        for entry in hist:
            if entry[0] <= week_ago:
                base = entry
        if base is None and now_ts - hist[0][0] >= 2 * 86400:
            base = hist[0]
        if base:
            props["주간 증가"] = {"number": new_views - base[1]}

        hist.append([now_ts, new_views])
    elif prev:
        # 1시간 내 재실행: 증가 지표는 유지하고 마지막 기록의 조회수만 갱신
        hist[-1] = [prev[0], new_views]
    else:
        hist.append([now_ts, new_views])

    hist = hist[-HISTORY_KEEP:]
    props["기록 (자동)"] = {
        "rich_text": [{"text": {"content": json.dumps(hist, separators=(",", ":"))}}]
    }

    # 초동 조회수: 업로드 48시간 시점의 조회수를 1회만 기록
    if prop_number(page, "초동 조회수") is None:
        date = (((page["properties"].get("업로드 날짜") or {}).get("date")) or {}).get("start") \
            or stats.get("published")
        up_ts = upload_ts_of(date) if date else None
        if up_ts is not None:
            target = up_ts + FIRST48_HOURS * 3600
            if now_ts >= target:
                v = views_at(hist, target)
                if v:
                    props["초동 조회수"] = {"number": v}

    if stats.get("likes") is not None:
        props["좋아요"] = {"number": stats["likes"]}
    if stats.get("comments") is not None:
        props["댓글"] = {"number": stats["comments"]}
    if stats.get("thumbnail"):
        props["썸네일"] = {"url": stats["thumbnail"][:1900]}
    if stats.get("title") and prop_title_empty(page, "제목"):
        props["제목"] = {"title": [{"text": {"content": stats["title"][:200]}}]}
    if stats.get("published") and prop_date_empty(page, "업로드 날짜"):
        props["업로드 날짜"] = {"date": {"start": stats["published"]}}
    return props, milestones


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

    # 오래된 인스타 영상은 월요일에만 수집 (Apify 비용 절약, FORCE_ALL=1 로 강제 전체 수집)
    now_ts = int(time.time())
    collect_all = os.environ.get("FORCE_ALL") == "1" or time.gmtime().tm_wday == FULL_COLLECT_WEEKDAY
    fresh_only = os.environ.get("FRESH_ONLY") == "1"  # 3시간 주기 신작 집중 관찰 모드

    if fresh_only:
        pages = [p for p in pages if is_fresh_video(p, now_ts)]
        print("신작 모드: 업로드 {}일 이내 영상 {}개만 수집".format(FRESH_DAYS, len(pages)))

    yt_pages, ig_pages, skipped, rested = [], [], 0, 0
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
            if not collect_all and is_old_video(page, now_ts):
                rested += 1
                continue
            ig_pages.append((page, code, link))
        else:
            skipped += 1
            print("  링크 형식을 인식하지 못함:", link)

    print("유튜브 {}개 / 인스타그램 {}개 / 건너뜀 {}개".format(
        len(yt_pages), len(ig_pages), skipped))
    if rested:
        print("업로드 {}일 지난 인스타 영상 {}개는 월요일에만 수집 (비용 절약)".format(
            OLD_AFTER_DAYS, rested))

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

    updated, failed, alerts = 0, 0, []

    def apply(page, platform, stats, link):
        nonlocal updated, failed
        # 플랫폼이 가끔 조회수를 0이나 비정상적으로 낮게 반환하는 수집 오류 방어:
        # 기존 값 대비 급락이면 이번 수집을 건너뛰고 기존 데이터를 유지
        prev_views = prop_number(page, "조회수")
        if prev_views and prev_views > 0:
            if stats["views"] == 0 or (prev_views > 10000 and stats["views"] < prev_views * 0.3):
                failed += 1
                print("  조회수 이상값 감지({:,} → {:,}) — 수집 오류로 판단, 건너뜀: {}".format(
                    prev_views, stats["views"], link))
                return
        props, milestones = build_properties(page, platform, stats)
        notion_update_page(page["id"], props, token)
        updated += 1
        for m in milestones:
            title = get_text(page, "제목") or stats.get("title") or link
            client = get_select(page, "클라이언트") or "미지정"
            alerts.append(
                ":tada: *{} 돌파!*  {} ({}) — 현재 {:,}회\n{}".format(
                    milestone_label(m), title, client, stats["views"], link))

    for page, vid in yt_pages:
        stats = yt_stats.get(vid)
        if not stats:
            failed += 1
            print("  유튜브 통계 없음 (삭제/비공개?):", vid)
            continue
        apply(page, "유튜브", stats, prop_url(page, "링크"))

    for page, code, link in ig_pages:
        stats = ig_stats.get(code)
        if not stats:
            failed += 1
            print("  인스타그램 통계 없음 (삭제/비공개?):", link)
            continue
        apply(page, "인스타그램", stats, link)

    send_slack(os.environ.get("SLACK_WEBHOOK_URL"), alerts)
    print("완료: {}개 업데이트, {}개 실패".format(updated, failed))
    if failed and not updated:
        sys.exit(1)


if __name__ == "__main__":
    main()
