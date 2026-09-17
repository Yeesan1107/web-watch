import os, json, hashlib, re
from pathlib import Path
from urllib.parse import urljoin
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
items = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
target = os.getenv("LINE_TARGET_ID", "")
line_test = os.getenv("LINE_TEST", "").lower() == "true"
HEADERS = {"User-Agent": "Mozilla/5.0 Web-Watch/3.0"}


def line(text):
    if not token or not target:
        print("LINE secrets not configured; notification skipped.")
        return
    r = requests.post("https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": target, "messages": [{"type": "text", "text": text[:5000]}]}, timeout=20)
    r.raise_for_status()


def normalize(text): return re.sub(r"\s+", " ", text or "").strip()


def dedupe(rows):
    seen, out = set(), []
    for row in rows:
        key = (row["title"], row["url"])
        if key not in seen:
            seen.add(key); out.append(row)
    return out


def all_links(soup, url, min_len=8):
    rows = []
    for a in soup.find_all("a", href=True):
        title = normalize(a.get_text(" ", strip=True))
        href = urljoin(url, a.get("href", ""))
        if len(title) < min_len or href.startswith(("javascript:", "#")) or href == url:
            continue
        rows.append({"title": title, "url": href})
    return dedupe(rows)


def extract_titles(url, html):
    soup = BeautifulSoup(html, "html.parser")

    if "nlma.gov.tw/ch/titlelist/news" in url:
        h = soup.find(lambda t: t.name in ("h1","h2","h3","h4","h5") and normalize(t.get_text()) == "最新消息")
        rows = all_links(h.parent if h else soup, url)
        return [x for x in rows if x["title"] not in {"最新消息","首頁","公告資訊"}][:80]

    if "eyesonplace.net" in url:
        marker = soup.find(lambda t: t.name in ("h1","h2","h3","h4","h5","h6") and normalize(t.get_text()) == "最新文章")
        rows = []
        if marker:
            for node in marker.find_all_next():
                if node is not marker and node.name in ("h1","h2","h3","h4","h5","h6"):
                    heading = normalize(node.get_text())
                    if heading and heading != "最新文章" and not node.find("a"): break
                if node.name == "a" and node.get("href"):
                    title = normalize(node.get_text(" ", strip=True))
                    if len(title) >= 8: rows.append({"title": title, "url": urljoin(url,node["href"])})
        return dedupe(rows)[:30]

    rows = all_links(soup, url)

    if "leju.com.tw/page_blog" in url:
        # 此網址已由 category=看屋筆記限定內容；優先保留看屋筆記文章。
        filtered = [x for x in rows if "看屋筆記" in x["title"]]
        return (filtered or rows)[:50]

    if "estate.ltn.com.tw/news" in url:
        # 新聞列表：排除常見導覽與功能連結，保留較長新聞標題。
        bad = ("熱門新聞", "即時新聞", "地產天下", "自由時報", "關於我們", "服務條款")
        filtered = [x for x in rows if len(x["title"]) >= 12 and not any(b in x["title"] for b in bad)]
        return filtered[:60]

    if "urban-web.kcg.gov.tw" in url:
        # 高雄都發局公告頁：保留較長的公告/計畫標題。
        filtered = [x for x in rows if len(x["title"]) >= 10]
        return filtered[:80]

    if "tiup.org.tw" in url or "rer.nccu.edu.tw" in url:
        # 學會/研究中心首頁：監看主要文章、活動、公告標題，排除短導覽文字。
        filtered = [x for x in rows if len(x["title"]) >= 10]
        return filtered[:60]

    return rows[:50]


if line_test:
    active_names = [x.get("name", x.get("url", "網站")) for x in items if x.get("enabled", True)]
    msg = "🧪 Web Watch 測試成功\nLINE 群組通知已正常連線"
    if active_names: msg += "\n\n目前監看：\n" + "\n".join(f"✓ {name}" for name in active_names)
    line(msg); print("LINE test notification sent")

changed = False
for item in items:
    if not item.get("enabled", True): continue
    url, name = item["url"], item.get("name", item["url"])
    try:
        r = requests.get(url, headers=HEADERS, timeout=30); r.raise_for_status()
        rows = extract_titles(url, r.text)
        if not rows:
            print(f"{name}: ERROR no titles found; state unchanged"); continue
        current_titles = [x["title"] for x in rows]
        current_hash = hashlib.sha256("\n".join(current_titles).encode("utf-8")).hexdigest()
        old = state.get(url)
        if not isinstance(old, dict):
            print(f"{name}: title-list baseline created")
        else:
            old_titles = old.get("titles", [])
            new_rows = [x for x in rows if x["title"] not in old_titles]
            if new_rows:
                shown = new_rows[:10]
                msg = f"🔔 {name}\n新增 {len(new_rows)} 則：\n\n" + "\n\n".join(f"• {x['title']}\n{x['url']}" for x in shown)
                if len(new_rows) > 10: msg += f"\n\n另有 {len(new_rows)-10} 則新內容"
                line(msg); print(f"{name}: {len(new_rows)} new title(s)")
            else: print(f"{name}: no new titles")
        new_state = {"hash": current_hash, "titles": current_titles}
        if old != new_state:
            state[url] = new_state; changed = True
    except Exception as e:
        print(f"{name}: ERROR {e}")

if changed:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
