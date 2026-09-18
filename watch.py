import os, json, hashlib, re, html as html_lib
from pathlib import Path
from urllib.parse import urljoin, quote_plus
import xml.etree.ElementTree as ET
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).parent
STATE = ROOT / "state.json"
items = json.loads((ROOT / "watchlist.json").read_text(encoding="utf-8"))
state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
token = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
target = os.getenv("LINE_TARGET_ID", "")
groups_url = os.getenv("LINE_GROUPS_URL", "")
groups_api_key = os.getenv("LINE_GROUPS_API_KEY", "")
line_test = os.getenv("LINE_TEST", "").lower() == "true"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152.0.0.0 Safari/537.36",
    "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
}


def get_targets():
    targets = []
    if groups_url and groups_api_key:
        try:
            r = requests.get(groups_url, headers={"Authorization": f"Bearer {groups_api_key}"}, timeout=20)
            r.raise_for_status()
            targets.extend(r.json().get("groups", []))
        except Exception as e:
            print(f"Group registry unavailable: {e}; using LINE_TARGET_ID fallback")
    if target:
        targets.append(target)
    return list(dict.fromkeys(x for x in targets if isinstance(x, str) and x))


def line(text):
    if not token:
        print("LINE token not configured; notification skipped."); return
    targets = get_targets()
    if not targets:
        print("No LINE target groups configured; notification skipped."); return
    for group_id in targets:
        try:
            r = requests.post("https://api.line.me/v2/bot/message/push",
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json={"to": group_id, "messages": [{"type": "text", "text": text[:5000]}]}, timeout=20)
            r.raise_for_status()
        except Exception as e:
            print(f"LINE push failed for {group_id[:8]}...: {e}")


def normalize(text): return re.sub(r"\s+", " ", html_lib.unescape(text or "")).strip()


def dedupe(rows):
    seen, out = set(), []
    for row in rows:
        key = row["title"]
        if key not in seen:
            seen.add(key); out.append(row)
    return out


def all_links(soup, url, min_len=8):
    rows = []
    for a in soup.find_all("a", href=True):
        title = normalize(a.get_text(" ", strip=True)); href = urljoin(url, a["href"])
        if len(title) >= min_len and not href.startswith(("javascript:", "#")) and href != url:
            rows.append({"title": title, "url": href})
    return dedupe(rows)


def direct_fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    r.raise_for_status(); return r.text


def rss_rows(feed_url, title_filter=None):
    r = requests.get(feed_url, headers={**HEADERS, "Accept": "application/rss+xml, application/xml, text/xml"}, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    rows = []
    for item in root.findall(".//item"):
        title = normalize(item.findtext("title")); link = normalize(item.findtext("link"))
        if len(title) < 6 or not link: continue
        if title_filter and title_filter not in title: continue
        rows.append({"title": title, "url": link})
    return dedupe(rows)


def google_news_rows(query, title_filter=None):
    feed = "https://news.google.com/rss/search?q=" + quote_plus(query) + "&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    return rss_rows(feed, title_filter=title_filter)[:50]


def extract_html(url, text):
    soup = BeautifulSoup(text, "html.parser")
    rows = all_links(soup, url)

    if "nlma.gov.tw/ch/titlelist/latestnews" in url:
        # NLMA uses relative/detail links that do not always contain the hostname.
        # The page itself is server-rendered, so keep long links after removing navigation/footer items.
        bad = ("網站導覽", "隱私權", "資訊安全", "政府網站資料開放", "聯絡資訊",
               "回首頁", "首頁", "上一頁", "下一頁", "最新消息")
        filtered = []
        for x in rows:
            title = x["title"]
            href = x["url"]
            if len(title) < 12 or any(b in title for b in bad):
                continue
            if "nlma.gov.tw" not in href:
                continue
            # Exclude category/list/navigation links; retain announcement/detail-like links.
            if "/ch/titlelist/" in href and href.rstrip("/") == url.rstrip("/"):
                continue
            filtered.append(x)
        return dedupe(filtered)[:80]

    if "eyesonplace.net" in url:
        # Homepage has a 最新文章 section; WordPress article links are kept while navigation is excluded.
        marker = soup.find(lambda t: t.name in ("h1","h2","h3","h4","h5","h6") and normalize(t.get_text()) == "最新文章")
        if marker:
            found = []
            for node in marker.find_all_next():
                if node is not marker and node.name in ("h1","h2","h3","h4","h5","h6") and normalize(node.get_text()) in ("專題報導", "關於眼底城事"):
                    break
                if node.name == "a" and node.get("href"):
                    title = normalize(node.get_text(" ", strip=True)); href = urljoin(url, node["href"])
                    if len(title) >= 8 and "eyesonplace.net" in href:
                        found.append({"title": title, "url": href})
            if found: return dedupe(found)[:30]
        return [x for x in rows if "eyesonplace.net" in x["url"] and len(x["title"]) >= 10][:30]

    if "leju.com.tw/page_blog" in url:
        return [x for x in rows if "/page_blog/view/" in x["url"] and "看屋筆記" in x["title"]][:50]
    if "estate.ltn.com.tw/news" in url:
        bad = ("熱門新聞", "即時新聞", "地產天下", "自由時報", "關於我們", "服務條款")
        return [x for x in rows if len(x["title"]) >= 12 and not any(b in x["title"] for b in bad)][:60]
    if "urban-web.kcg.gov.tw" in url: return [x for x in rows if len(x["title"]) >= 10][:80]
    if "tiup.org.tw" in url or "rer.nccu.edu.tw" in url: return [x for x in rows if len(x["title"]) >= 10][:60]
    return rows[:50]


def fetch_rows(url):
    errors = []
    try:
        rows = extract_html(url, direct_fetch(url))
        if rows: return rows, "direct"
        errors.append("direct: no titles")
    except Exception as e: errors.append("direct: " + str(e))

    # NLMA fallback: Google News is only used if the official page unexpectedly changes layout.
    if "nlma.gov.tw/ch/titlelist/latestnews" in url:
        try:
            rows = google_news_rows("site:nlma.gov.tw 內政部國土署")
            if rows: return rows, "Google News RSS"
            errors.append("Google: no NLMA titles")
        except Exception as e: errors.append("Google: " + str(e))

    # Eyes on Place is WordPress: try its native RSS feed before search fallback.
    if "eyesonplace.net" in url:
        try:
            rows = rss_rows("https://eyesonplace.net/feed/")
            if rows: return rows[:30], "RSS"
            errors.append("RSS: no titles")
        except Exception as e: errors.append("RSS: " + str(e))
        try:
            rows = google_news_rows("site:eyesonplace.net")
            if rows: return rows, "Google News RSS"
        except Exception as e: errors.append("Google: " + str(e))

    # Leju blocks GitHub runner IPs. Use a public Google News RSS search as the independent fallback.
    if "leju.com.tw/page_blog" in url:
        try:
            rows = google_news_rows('site:leju.com.tw/page_blog/view "看屋筆記"', title_filter="看屋筆記")
            if rows: return rows, "Google News RSS"
            errors.append("Google: no 看屋筆記 titles")
        except Exception as e: errors.append("Google: " + str(e))

    raise RuntimeError("; ".join(errors))


def check_source(item, notify=True):
    url, name = item["url"], item.get("name", item["url"])
    try:
        rows, method = fetch_rows(url)
        current_titles = [x["title"] for x in rows]
        current_hash = hashlib.sha256("\n".join(current_titles).encode()).hexdigest()
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
        print(f"{name}: ERROR {e}"); return False, 0, False, str(e)


changed = False; results = []
for item in items:
    if not item.get("enabled", True): continue
    ok, count, changed_here, detail = check_source(item, notify=not line_test)
    changed = changed or changed_here
    results.append((item.get("name", item["url"]), ok, count, detail))

if line_test:
    lines = ["🧪 Web Watch 測試完成", "LINE 群組通知已正常連線", "", "實際抓取結果："]
    for name, ok, count, detail in results:
        if ok: lines.append(f"✓ {name}（{count} 則｜{detail}）")
        else: lines.append(f"✗ {name}（{(detail or '抓取失敗')[:55]}）")
    line("\n".join(lines)); print("LINE test/status notification sent")

if changed:
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
