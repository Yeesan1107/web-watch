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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
}


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


def dedupe(rows):
    seen, out = set(), []
    for row in rows:
        key = (row["title"], row["url"])
        if key not in seen:
            seen.add(key)
            out.append(row)
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


def fetch_html(url):
    s = requests.Session()
    s.headers.update(HEADERS)
    # A homepage warm-up helps sites that set a cookie before the listing page.
    if "leju.com.tw" in url:
        try:
            s.get("https://www.leju.com.tw/", timeout=20)
        except Exception:
            pass
    r = s.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text


def extract_titles(url, html):
    soup = BeautifulSoup(html, "html.parser")

    if "nlma.gov.tw/ch/titlelist/news" in url:
        # The NLMA page does not consistently wrap the list under the heading's parent.
        # Select announcement/detail links from the whole page instead.
        rows = []
        for a in soup.find_all("a", href=True):
            title = normalize(a.get_text(" ", strip=True))
            href = urljoin(url, a["href"])
            if len(title) < 8:
                continue
            if any(x in title for x in ("網站導覽", "隱私權", "資訊安全", "政府網站資料開放宣告")):
                continue
            # Keep links under the NLMA site that look like content/detail pages.
            if "nlma.gov.tw" in href and href != url:
                rows.append({"title": title, "url": href})
        return dedupe(rows)[:80]

    if "eyesonplace.net" in url:
        marker = soup.find(lambda t: t.name in ("h1","h2","h3","h4","h5","h6") and normalize(t.get_text()) == "最新文章")
        rows = []
        if marker:
            for node in marker.find_all_next():
                if node is not marker and node.name in ("h1","h2","h3","h4","h5","h6"):
                    heading = normalize(node.get_text())
                    if heading and heading != "最新文章" and not node.find("a"):
                        break
                if node.name == "a" and node.get("href"):
                    title = normalize(node.get_text(" ", strip=True))
                    if len(title) >= 8:
                        rows.append({"title": title, "url": urljoin(url, node["href"])})
        if rows:
            return dedupe(rows)[:30]
        # Fallback for layout changes.
        return [x for x in all_links(soup, url) if "eyesonplace.net" in x["url"]][:30]

    rows = all_links(soup, url)

    if "leju.com.tw/page_blog" in url:
        filtered = [x for x in rows if "看屋筆記" in x["title"] or "/page_blog/view/" in x["url"]]
        return dedupe(filtered)[:50]

    if "estate.ltn.com.tw/news" in url:
        bad = ("熱門新聞", "即時新聞", "地產天下", "自由時報", "關於我們", "服務條款")
        return [x for x in rows if len(x["title"]) >= 12 and not any(b in x["title"] for b in bad)][:60]

    if "urban-web.kcg.gov.tw" in url:
        return [x for x in rows if len(x["title"]) >= 10][:80]

    if "tiup.org.tw" in url or "rer.nccu.edu.tw" in url:
        return [x for x in rows if len(x["title"]) >= 10][:60]

    return rows[:50]


def check_source(item, notify=True):
    url, name = item["url"], item.get("name", item["url"])
    try:
        html = fetch_html(url)
        rows = extract_titles(url, html)
        if not rows:
            raise RuntimeError("no titles found")

        current_titles = [x["title"] for x in rows]
        current_hash = hashlib.sha256("\n".join(current_titles).encode("utf-8")).hexdigest()
        old = state.get(url)

        if not isinstance(old, dict):
            print(f"{name}: title-list baseline created ({len(rows)} titles)")
        else:
            old_titles = old.get("titles", [])
            new_rows = [x for x in rows if x["title"] not in old_titles]
            if new_rows and notify:
                shown = new_rows[:10]
                msg = f"🔔 {name}\n新增 {len(new_rows)} 則：\n\n" + "\n\n".join(
                    f"• {x['title']}\n{x['url']}" for x in shown
                )
                if len(new_rows) > 10:
                    msg += f"\n\n另有 {len(new_rows)-10} 則新內容"
                line(msg)
            print(f"{name}: {len(new_rows)} new title(s)" if new_rows else f"{name}: no new titles")

        new_state = {"hash": current_hash, "titles": current_titles}
        changed_here = old != new_state
        if changed_here:
            state[url] = new_state
        return True, len(rows), changed_here, None
    except Exception as e:
        print(f"{name}: ERROR {e}")
        return False, 0, False, str(e)


changed = False
results = []
for item in items:
    if not item.get("enabled", True):
        continue
    ok, count, changed_here, error = check_source(item, notify=not line_test)
    changed = changed or changed_here
    results.append((item.get("name", item["url"]), ok, count, error))

if line_test:
    lines = ["🧪 Web Watch 測試完成", "LINE 群組通知已正常連線", "", "實際抓取結果："]
    for name, ok, count, error in results:
        if ok:
            lines.append(f"✓ {name}（{count} 則）")
        else:
            short_error = (error or "抓取失敗")[:45]
            lines.append(f"✗ {name}（{short_error}）")
    line("\n".join(lines))
    print("LINE test/status notification sent")

if changed:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
