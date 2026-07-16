# -*- coding: utf-8 -*-
"""
노션 데이터를 읽어 대시보드용 암호화 데이터 파일(docs/data.json)을 생성합니다.

- 전체 데이터를 비밀번호 기반 AES-256-GCM으로 암호화 → 비밀번호 없이는 열람 불가
- docs/index.html(대시보드 앱)이 브라우저에서 비밀번호로 복호화해 표시

필요 환경변수:
    NOTION_TOKEN, NOTION_DATABASE_ID, SITE_PASSWORD
"""
import base64
import json
import os
import sys
import time
import unicodedata
import urllib.request

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

import update_stats as core

PBKDF2_ITERATIONS = 200000


def download_thumbnail(url, code, out_dir):
    """인스타그램 썸네일을 로컬에 저장 (CDN 링크는 만료되므로 한 번 받아두면 영구 보존)"""
    thumbs = os.path.join(out_dir, "thumbs")
    os.makedirs(thumbs, exist_ok=True)
    path = os.path.join(thumbs, code + ".jpg")
    if os.path.exists(path) and os.path.getsize(path) > 1000:
        return "thumbs/" + code + ".jpg"
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as res:
            data = res.read()
        if len(data) > 1000:
            with open(path, "wb") as f:
                f.write(data)
            return "thumbs/" + code + ".jpg"
    except Exception as e:
        print("  썸네일 다운로드 실패 ({}): {}".format(code, e))
    return None


def collect_items(pages, out_dir):
    items = []
    for page in pages:
        p = page["properties"]

        def num(name):
            return (p.get(name) or {}).get("number")

        link = core.prop_url(page, "링크")
        if not link or num("조회수") is None:
            continue

        title = "".join(
            t.get("plain_text", "") for t in (p.get("제목") or {}).get("title") or []
        ).strip()
        sel = lambda name: (((p.get(name) or {}).get("select")) or {}).get("name")
        client = sel("클라이언트")
        platform = sel("플랫폼")
        date = (((p.get("업로드 날짜") or {}).get("date")) or {}).get("start")

        th = None
        code = core.instagram_shortcode(link)
        if code:
            th = download_thumbnail(core.prop_url(page, "썸네일"), code, out_dir)

        items.append({
            "title": title or "(제목 없음)",
            "client": client or "미지정",
            "platform": platform or "기타",
            "url": link,
            "date": date,
            "views": num("조회수") or 0,
            "first48": num("초동 조회수"),
            "daily": num("일일 증가"),
            "weekly": num("주간 증가"),
            "rate": num("증가율"),
            "likes": num("좋아요"),
            "comments": num("댓글"),
            "hist": core.load_history(page),
            "yt": core.youtube_video_id(link),
            "th": th,
            "planner": sel("기획자"),
            "shooter": sel("촬영자"),
            "editor": sel("편집자"),
            "watch": bool((p.get("집중 관찰") or {}).get("checkbox")),
        })
    return items


def collect_codes(token):
    """접속 코드 DB → [{name, code}]"""
    out = []
    try:
        for pg in core.notion_query_all(core.CODES_DB_ID, token):
            name = core.get_text(pg, "이름")
            code = "".join(
                t.get("plain_text", "")
                for t in (pg["properties"].get("코드") or {}).get("rich_text") or []).strip()
            if name and code:
                out.append({"name": name, "code": code})
    except Exception as e:
        print("접속 코드 DB 읽기 실패:", e)
    return out


def derive_key(code, salt):
    code = unicodedata.normalize("NFC", code.strip())
    return PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                      iterations=PBKDF2_ITERATIONS).derive(code.encode("utf-8"))


def encrypt(payload, codes):
    """봉투 암호화: 데이터는 무작위 키 K로 잠그고, K를 각 팀원 코드로 감싸서 보관"""
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    data_key = os.urandom(32)
    iv = os.urandom(12)
    ct = AESGCM(data_key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)

    wraps = []
    for c in codes:
        salt = os.urandom(16)
        wiv = os.urandom(12)
        wct = AESGCM(derive_key(c["code"], salt)).encrypt(wiv, data_key, None)
        wraps.append({"name": c["name"], "salt": b64(salt), "iv": b64(wiv), "ct": b64(wct)})

    return {"v": 2, "iter": PBKDF2_ITERATIONS, "iv": b64(iv), "ct": b64(ct), "wraps": wraps}


def collect_clients(token):
    """클라이언트 관리 DB → [{name, status, quota, manager, start}]"""
    out = []
    try:
        for pg in core.notion_query_all(core.CLIENTS_DB_ID, token):
            name = core.get_text(pg, "클라이언트명")
            if not name:
                continue
            p = pg["properties"]
            out.append({
                "name": name,
                "status": core.get_select(pg, "상태") or "활성",
                "manager": core.get_select(pg, "담당자"),
                "start": (((p.get("계약 시작일") or {}).get("date")) or {}).get("start"),
            })
    except Exception as e:
        print("클라이언트 DB 읽기 실패 (건너뜀):", e)
    return out


def collect_team_goal(token):
    """팀 목표 DB → {views: 월 조회수 목표, followers: 월 팔로워 증가 목표}"""
    try:
        for pg in core.notion_query_all(core.MEMBERS_DB_ID, token):
            p = pg["properties"]
            views = (p.get("월 조회수 목표") or {}).get("number")
            followers = (p.get("월 팔로워 증가 목표") or {}).get("number")
            if views or followers:
                return {"views": views, "followers": followers}
    except Exception as e:
        print("팀 목표 DB 읽기 실패 (건너뜀):", e)
    return None


def collect_accounts(token):
    """계정 관리 DB → [{name, plat, followers, weekly, hist}]"""
    out = []
    try:
        for pg in core.notion_query_all(core.ACCOUNTS_DB_ID, token):
            followers = (pg["properties"].get("팔로워") or {}).get("number")
            name = core.get_text(pg, "계정명") or (core.prop_url(pg, "링크") or "")
            if not name:
                continue
            out.append({
                "name": name,
                "plat": core.get_select(pg, "플랫폼"),
                "client": core.get_select(pg, "클라이언트"),
                "followers": followers,
                "weekly": (pg["properties"].get("주간 증감") or {}).get("number"),
                "hist": core.load_history(pg),
            })
    except Exception as e:
        print("계정 DB 읽기 실패 (건너뜀):", e)
    return out


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    if not token or not db_id:
        print("NOTION_TOKEN, NOTION_DATABASE_ID 환경변수가 필요합니다.")
        sys.exit(1)

    codes = collect_codes(token)
    if not codes:
        # 접속 코드 DB를 못 읽으면 기존 단일 비밀번호로 대체 (안전장치)
        password = os.environ.get("SITE_PASSWORD")
        if not password:
            print("접속 코드가 없고 SITE_PASSWORD도 없어 중단합니다.")
            sys.exit(1)
        codes = [{"name": "관리자", "code": password}]
        print("접속 코드 DB가 비어 있어 SITE_PASSWORD 단일 코드로 빌드합니다.")

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(out_dir, exist_ok=True)

    pages = core.notion_query_all(db_id, token)
    items = collect_items(pages, out_dir)
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "items": items,
        "clients": collect_clients(token),
        "teamGoal": collect_team_goal(token),
        "accounts": collect_accounts(token),
    }
    with open(os.path.join(out_dir, "data.json"), "w") as f:
        json.dump(encrypt(payload, codes), f)
    print("docs/data.json 생성 완료 (영상 {}개, 접속 코드 {}개, 암호화됨)".format(
        len(items), len(codes)))


if __name__ == "__main__":
    main()
