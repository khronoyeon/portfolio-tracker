// 접속 기록: 대시보드 로그인 성공 시 노션 "접속 기록" DB에 한 줄 기록 (Vercel 서버 함수)
// 필요 환경변수(Vercel 프로젝트 설정): NOTION_TOKEN, LOG_DB_ID

function deviceOf(ua) {
  if (/iPhone/.test(ua)) return "아이폰";
  if (/iPad/.test(ua)) return "아이패드";
  if (/Android/.test(ua)) return "안드로이드";
  if (/Macintosh/.test(ua)) return "맥";
  if (/Windows/.test(ua)) return "윈도우";
  return "기타";
}

export default async function handler(req, res) {
  if (req.method !== "POST") return res.status(405).json({ error: "method" });
  const token = process.env.NOTION_TOKEN;
  const dbId = process.env.LOG_DB_ID;
  if (!token || !dbId) return res.status(200).json({ ok: false, reason: "not-configured" });

  const name = String((req.body && req.body.name) || "알 수 없음").slice(0, 50);
  const ua = String(req.headers["user-agent"] || "");

  try {
    await fetch("https://api.notion.com/v1/pages", {
      method: "POST",
      headers: {
        Authorization: "Bearer " + token,
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        parent: { database_id: dbId },
        properties: {
          "이름": { title: [{ text: { content: name } }] },
          "접속 시각": { date: { start: new Date().toISOString() } },
          "기기": { rich_text: [{ text: { content: deviceOf(ua) + " · " + ua.slice(0, 150) } }] },
        },
      }),
    });
    res.status(200).json({ ok: true });
  } catch (e) {
    res.status(200).json({ ok: false });
  }
}
