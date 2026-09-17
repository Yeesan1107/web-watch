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
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def line(text):
    if not token or not target:
        print("LINE secrets not configured; notification skipped.")
        return
    r = requests.post("https://api.line.me/v2/bot/message/push",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": target, "messages": [{"type": "text", "text": text[:5000]}]}, timeout=20)
    r.raise_for_status()


def normalize(text):
    return re.sub(r"\s+", " ", text or "").strip()


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


def direct_fetch(url):
    s = requests.Session(); s.headers.update(HEADERS)
    if "leju.com.tw" in url:
        try: s.get("https://www.leju.com.tw/", timeout=15)
        except Exception: pass
    r = s.get(url, timeout=30, allow_redirects=True)
    r.raise_for_status()
    return r.text


def reader_fetch(url):
    # Public reader fallback for sites that block GitHub-hosted IPs.
    r = requests.get("https://r.jina.ai/" + url, headers={"Accept": "text/plain", "User-Agent": HEADERS["User-Agent"]}, timeout=45)
    r.raise_for_status()
    return r.text


def markdown_links(text, base_url):
    rows = []
    for title, href in re.findall(r"\[([^\]\n]{4,300})\]\((https?://[^)\s]+)\)", text):
        title = normalize(re.sub(r"!\[[^\]]*\]", "", title))
        if len(title) >= 8:
            rows.append({"title": title, "url": href})
    return dedupe(rows)


def extract_html(url, html):
    soup = BeautifulSoup(html, "html.parser")
    if "nlma.gov.tw/ch/titlelist/news" in url:
        rows = []
        for a in soup.find_all("a", href=True):
            title = normalize(a.get_text(" ", strip=True)); href = urljoin(url, a["href"])
            if len(title) >= 8 and "nlma.gov.tw" in href and href != url:
                rows.append({"title": title, "url": href})
        return dedupe(rows)[:80]

    if "eyesonplace.net" in url:
        # WordPress article permalinks contain YYYY/MM/DD/article-id.
        rows = [x for x in all_links(soup, url) if re.search(r"eyesonplace\.net/20\d\d/\d\d/\d\d/\d+", x["url"])]
        return rows[:30]

    rows = all_links(soup, url)
    if "leju.com.tw/page_blog" in url:
        return dedupe([x for x in rows if "/page_blog/view/" in x["url"] and "看屋筆記" in x["title"]])[:50]
    if "estate.ltn.com.tw/news" in url:
        bad = ("熱門新聞", "即時新聞", "地產天下", "自由時報", "關於我們", "服務條款")
        return [x for x in rows if len(x["title"]) >= 12 and not any(b in x["title"] for b in bad)][:60]
    if "urban-web.kcg.gov.tw" in url:
        return [x for x in rows if len(x["title"]) >= 10][:80]
    if "tiup.org.tw" in url or "rer.nccu.edu.tw" in url:
        return [x for x in rows if len(x["title"]) >= 10][:60]
    return rows[:50]


def extract_reader(url, text):
    rows = markdown_links(text, url)
    if "nlma.gov.tw/ch/titlelist/news" in url:
        # Reader output reliably contains the Latest News section. Prefer NLMA content links.
        return [x for x in rows if "nlma.gov.tw" in x["url"] and x["url"] != url][:80]
    if "eyesonplace.net" in url:
        return [x for x in rows if re.search(r"eyesonplace\.net/20\d\d/\d\d/\d\d/\d+", x["url"])][:30]
    if "leju.com.tw/page_blog" in url:
        return [x for x in rows if "/page_blog/view/" in x["url"] and "看屋筆記" in x["title"]][:50]
    return rows[:50]


def fetch_rows(url):
    direct_error = None
    try:
        html = direct_fetch(url)
        rows = extract_html(url, html)
        if rows:
            return rows, "direct"
        direct_error = "no titles found"
    except Exception as e:
        direct_error = str(e)

    # Only use the reader fallback when direct access fails or yields no titles.
    try:
        text = reader_fetch(url)
        rows = extract_reader(url, text)
        if rows:
            return rows, "reader fallback"
        raise RuntimeError("fallback returned no titles")
    except Exception as e:
        raise RuntimeError(f"direct: {direct_error}; fallback: {e}")


def check_source(item, notify=True):
    url, name = item["url"], item.get("name", item["url"])
    try:
        rows, method = fetch_rows(url)
        current_titles = [x["title"] for x in rows]
        current_hash = hashlib.sha256("\n".join(current_titles).encode("utf-8")).hexdigest()
        old = state.get(url)
        if not isinstance(old, dict):
            print(f"{name}: baseline created ({len(rows)} titles, {method})")
        else:
            old_titles = old.get("titles", [])
            new_rows = [x for x in rows if x["title"] not in old_titles]
            if new_rows and notify:
                shown = new_rows[:10]
                msg = f"🔔 {name}\n新增 {len(new_rows)} 則：\n\n" + "\n\n".join(f"• {x['title']}\n{x['url']}" for x in shown)
                if len(new_rows) > 10: msg += f"\n\n另有 {len(new_rows)-10} 則新內容"
                line(msg)
            print(f"{name}: {len(new_rows)} new title(s), {method}" if new_rows else f"{name}: no new titles, {method}")
        new_state = {"hash": current_hash, "titles": current_titles}
        changed_here = old != new_state
        if changed_here: state[url] = new_state
        return True, len(rows), changed_here, method
    except Exception as e:
        print(f"{name}: ERROR {e}")
        return False, 0, False, str(e)


changed = False; results = []
for item in items:
    if not item.get("enabled", True): continue
    ok, count, changed_here, detail = check_source(item, notify=not line_test)
    changed = changed or changed_here
    results.append((item.get("name", item["url"]), ok, count, detail))

if line_test:
    lines = ["🧪 Web Watch 測試完成", "LINE 群組通知已正常連線", "", "實際抓取結果："]
    for name, ok, count, detail in results:
        if ok:
            suffix = "／備援" if detail == "reader fallback" else ""
            lines.append(f"✓ {name}（{count} 則{suffix}）")
        else:
            lines.append(f"✗ {name}（{(detail or '抓取失敗')[:38]}）")
    line("\n".join(lines)); print("LINE test/status notification sent")

if changed:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
