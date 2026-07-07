# 📊 포트폴리오 조회수 트래커

노션 데이터베이스에 유튜브/인스타그램 릴스 **링크만 붙여넣으면**,
매일 아침 9시에 조회수·좋아요·댓글 수가 자동으로 업데이트됩니다.

### 자동 수집 지표 (매일 아침 9시 업데이트)

| 열 | 의미 |
|---|---|
| 조회수 / 좋아요 / 댓글 | 최신 값 |
| 일일 증가 | 어제 대비 조회수 증가량 |
| 증가율 | 어제 대비 증가율 (%) |
| 시간당 조회수 | 최근 24시간 기준 시간당 평균 증가량 |
| 주간 증가 | 7일 전 대비 증가량 (기록이 쌓이면 자동 계산) |
| 제목 / 업로드 날짜 / 플랫폼 | 비워두면 영상 정보에서 자동 입력 |

### 실시간 계산 지표 (노션 수식 — 항상 최신)

| 열 | 의미 |
|---|---|
| 참여율 | (좋아요+댓글) ÷ 조회수 × 100 |
| 게시 경과일 | 업로드 후 지난 일수 |
| 일평균 조회수 | 조회수 ÷ 경과일 — 오래된 영상과 신작을 공평하게 비교 |

> ⚠️ **"기록 (자동)"** 열은 일별 히스토리 저장용입니다. 직접 수정하지 마세요. (뷰에서 숨김 처리 권장)

노션 기본 기능으로 조회수순 정렬, 업로드 날짜·클라이언트 필터 가능

파이썬 표준 라이브러리만 사용하므로 별도 패키지 설치가 필요 없습니다.

---

## 설정 순서 (최초 1회, 약 20분)

### 1. 노션 통합(Integration) 만들기

1. https://www.notion.so/my-integrations 접속 → **새 API 통합 만들기**
2. 이름: `조회수 트래커` (아무거나), 워크스페이스 선택 → 저장
3. **시크릿(Internal Integration Secret)** 복사 → 이것이 `NOTION_TOKEN`

### 2. 노션에 데이터베이스 만들기

1. 노션에서 트래커를 넣을 페이지를 하나 만들기 (예: "포트폴리오")
2. 그 페이지 우측 상단 `...` → **연결(Connections)** → 위에서 만든 통합 추가
3. 페이지 URL에서 32자리 ID 복사 (예: `notion.so/포트폴리오-a1b2c3d4...` 뒷부분)
4. 터미널에서 실행:

```bash
cd 이_폴더_경로
NOTION_TOKEN=시크릿값 NOTION_PARENT_PAGE_ID=페이지ID python3 setup_database.py
```

→ 데이터베이스가 자동 생성되고 `DATABASE_ID`가 출력됩니다. 복사해두세요.

### 3. 유튜브 API 키 발급 (무료)

1. https://console.cloud.google.com 접속 → 새 프로젝트 생성
2. **API 및 서비스 → 라이브러리** → `YouTube Data API v3` 검색 → 사용 설정
3. **API 및 서비스 → 사용자 인증 정보** → **API 키 만들기**
4. 키 복사 → 이것이 `YOUTUBE_API_KEY`

### 4. Apify 토큰 발급 (인스타그램용)

1. https://apify.com 가입 (매달 $5 무료 크레딧 제공 — 릴스 수백 개 수준이면 무료 범위로 충분할 수 있음)
2. **Settings → API & Integrations** → Personal API token 복사 → 이것이 `APIFY_TOKEN`

### 5. GitHub에 올리고 자동 실행 설정

1. https://github.com/new 에서 **Private** 저장소 생성 (예: `portfolio-tracker`)
2. 이 폴더를 업로드:

```bash
cd 이_폴더_경로
git init && git add . && git commit -m "포트폴리오 트래커"
git branch -M main
git remote add origin https://github.com/내계정/portfolio-tracker.git
git push -u origin main
```

3. 저장소 페이지 → **Settings → Secrets and variables → Actions** → **New repository secret** 으로 4개 등록:

| 이름 | 값 |
|---|---|
| `NOTION_TOKEN` | 1번에서 복사한 시크릿 |
| `NOTION_DATABASE_ID` | 2번에서 출력된 DATABASE_ID |
| `YOUTUBE_API_KEY` | 3번의 API 키 |
| `APIFY_TOKEN` | 4번의 토큰 |

4. 저장소 → **Actions** 탭 → "포트폴리오 조회수 업데이트" → **Run workflow** 버튼으로 즉시 한 번 실행해서 테스트

이후 **매일 아침 9시(한국시간)** 자동 실행됩니다.
(실행 시간을 바꾸려면 `.github/workflows/daily-update.yml`의 cron 값 수정 — UTC 기준이라 한국시간 −9시간)

---

## 일상 사용법

1. 영상 업로드 후 노션 데이터베이스에 **새 행 추가 → 링크 붙여넣기 → 클라이언트 선택**
2. 끝. 다음 날 아침부터 조회수가 자동으로 채워지고, 제목·업로드 날짜·플랫폼도 자동 입력됩니다.

지원 링크 형식:
- 유튜브: `youtube.com/watch?v=...`, `youtu.be/...`, `youtube.com/shorts/...`
- 인스타그램: `instagram.com/reel/...`, `instagram.com/p/...`

### 추천 노션 뷰 설정

데이터베이스에서 뷰를 추가해서 쓰면 편합니다:

- **🔥 인기순** — 표 뷰, `조회수` 내림차순 정렬
- **📈 오늘 뜨는 영상** — 표 뷰, `일일 증가` 내림차순 정렬 (`시간당 조회수`·`증가율` 열 표시)
- **🗓 이번 달 제작물** — `업로드 날짜` 필터: 이번 달
- **클라이언트별 갤러리** — 갤러리 뷰, `클라이언트`로 그룹화 → 클라이언트 보고용

각 뷰에서 `기록 (자동)` 열은 숨기는 것을 추천합니다 (열 우클릭 → 보기에서 숨기기).

---

## 수동으로 즉시 업데이트하고 싶을 때

GitHub 저장소 → Actions → Run workflow 버튼을 누르거나, 내 컴퓨터에서:

```bash
NOTION_TOKEN=... NOTION_DATABASE_ID=... YOUTUBE_API_KEY=... APIFY_TOKEN=... python3 update_stats.py
```

## 비용

- 유튜브 API: 무료 (일일 쿼터 내에서 영상 수천 개 조회 가능)
- Apify: 인스타그램 게시물 1,000개당 약 $2.3. 매달 $5 무료 크레딧 제공
- GitHub Actions: Private 저장소 무료 사용량(월 2,000분)으로 충분
