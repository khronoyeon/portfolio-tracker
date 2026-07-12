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
            "editor": sel("편집자"),
            "watch": bool((p.get("집중 관찰") or {}).get("checkbox")),
        })
    return items


def encrypt(payload, password):
    # 한글 비밀번호의 자모 분리(NFD) 문제 방지 — 브라우저 쪽도 동일하게 NFC 정규화함
    password = unicodedata.normalize("NFC", password.strip())
    salt = os.urandom(16)
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt,
                     iterations=PBKDF2_ITERATIONS)
    key = kdf.derive(password.encode("utf-8"))
    iv = os.urandom(12)
    ct = AESGCM(key).encrypt(iv, json.dumps(payload, ensure_ascii=False).encode("utf-8"), None)
    b64 = lambda b: base64.b64encode(b).decode("ascii")
    return {"v": 1, "iter": PBKDF2_ITERATIONS, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


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
                "quota": (p.get("월 계약 수량") or {}).get("number"),
                "manager": core.get_select(pg, "담당자"),
                "start": (((p.get("계약 시작일") or {}).get("date")) or {}).get("start"),
            })
    except Exception as e:
        print("클라이언트 DB 읽기 실패 (건너뜀):", e)
    return out


def collect_members(token):
    """팀원 목표 DB → [{name, role, goal}]"""
    out = []
    try:
        for pg in core.notion_query_all(core.MEMBERS_DB_ID, token):
            name = core.get_text(pg, "이름")
            if not name:
                continue
            out.append({
                "name": name,
                "role": core.get_select(pg, "역할"),
                "goal": (pg["properties"].get("월 목표") or {}).get("number"),
            })
    except Exception as e:
        print("팀원 DB 읽기 실패 (건너뜀):", e)
    return out


def main():
    token = os.environ.get("NOTION_TOKEN")
    db_id = os.environ.get("NOTION_DATABASE_ID")
    password = os.environ.get("SITE_PASSWORD")
    if not token or not db_id or not password:
        print("NOTION_TOKEN, NOTION_DATABASE_ID, SITE_PASSWORD 환경변수가 필요합니다.")
        sys.exit(1)

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs")
    os.makedirs(out_dir, exist_ok=True)

    pages = core.notion_query_all(db_id, token)
    items = collect_items(pages, out_dir)
    payload = {
        "generated": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
        "items": items,
        "clients": collect_clients(token),
        "members": collect_members(token),
    }
    with open(os.path.join(out_dir, "data.json"), "w") as f:
        json.dump(encrypt(payload, password), f)
    print("docs/data.json 생성 완료 (영상 {}개, 암호화됨)".format(len(items)))


if __name__ == "__main__":
    main()
