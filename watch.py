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

HEADERS = {"User-Agent": "Mozilla/5.0 Web-Watch/2.0"}


def line(text):
    if not token or not target:
        print("LINE secrets not configured; notification skipped.")
        return
    r = requests.post(
        "https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": target, "messages": [{"type": "text", "text": text[:5000]}]},
        timeout=20,
    )
    r.raise_for_status()


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


def extract_titles(url, html):
    soup = BeautifulSoup(html, "html.parser")

    # 內政部國土署：只抓「最新消息」清單中的公告標題。
    if "nlma.gov.tw/ch/titlelist/news" in url:
        h = soup.find(lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5") and normalize(tag.get_text()) == "最新消息")
        root = h.parent if h else soup
        links = []
        for a in root.find_all("a", href=True):
            title = normalize(a.get_text(" ", strip=True))
            href = urljoin(url, a["href"])
            if not title or title in {"最新消息", "首頁", "公告資訊"}:
                continue
            if href == url or href.startswith("javascript:") or href.startswith("#"):
                continue
            links.append({"title": title, "url": href})
        # 最新消息頁面的公告標題通常較長；排除導覽選單等短文字。
        links = [x for x in links if len(x["title"]) >= 8]
        return dedupe(links)[:80]

    # 眼底城事：只抓「最新文章」區塊，遇到下一個區塊標題即停止。
    if "eyesonplace.net" in url:
        marker = soup.find(lambda tag: tag.name in ("h1", "h2", "h3", "h4", "h5", "h6") and normalize(tag.get_text()) == "最新文章")
        links = []
        if marker:
            for node in marker.find_all_next():
                if node is not marker and node.name in ("h1", "h2", "h3", "h4", "h5", "h6"):
                    heading = normalize(node.get_text())
                    if heading and heading != "最新文章" and not node.find("a"):
                        break
                if node.name == "a" and node.get("href"):
                    title = normalize(node.get_text(" ", strip=True))
                    if title:
                        links.append({"title": title, "url": urljoin(url, node["href"])})
        return dedupe(links)[:30]

    return []


def dedupe(rows):
    seen = set()
    out = []
    for row in rows:
        key = (row["title"], row["url"])
        if key not in seen:
            seen.add(key)
            out.append(row)
    return out


changed = False
for item in items:
    if not item.get("enabled", True):
        continue

    url = item["url"]
    name = item.get("name", url)
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        rows = extract_titles(url, r.text)
        if not rows:
            print(f"{name}: ERROR no titles found; state unchanged")
            continue

        current_titles = [x["title"] for x in rows]
        current_hash = hashlib.sha256("\n".join(current_titles).encode("utf-8")).hexdigest()
        old = state.get(url)

        # 相容舊版 state（舊版只存整頁 hash）。第一次升級不推播，避免一次洗版。
        if not isinstance(old, dict):
            print(f"{name}: title-list baseline created")
        else:
            old_titles = old.get("titles", [])
            new_rows = [x for x in rows if x["title"] not in old_titles]
            if new_rows:
                shown = new_rows[:10]
                msg = f"🔔 {name}\n新增 {len(new_rows)} 則：\n\n"
                msg += "\n\n".join(f"• {x['title']}\n{x['url']}" for x in shown)
                if len(new_rows) > 10:
                    msg += f"\n\n另有 {len(new_rows) - 10} 則新公告"
                line(msg)
                print(f"{name}: {len(new_rows)} new title(s)")
            else:
                print(f"{name}: no new titles")

        new_state = {"hash": current_hash, "titles": current_titles}
        if old != new_state:
            state[url] = new_state
            changed = True

    except Exception as e:
        print(f"{name}: ERROR {e}")

if changed:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
