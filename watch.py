import os,json,hashlib,re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).parent
STATE=ROOT/"state.json"
items=json.loads((ROOT/"watchlist.json").read_text(encoding="utf-8"))
state=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","")
target=os.getenv("LINE_TARGET_ID","")

def clean(html):
    s=BeautifulSoup(html,"html.parser")
    for x in s(["script","style","noscript"]): x.decompose()
    return re.sub(r"\s+"," ",s.get_text(" ",strip=True))

def line(text):
    if not token or not target:
        print("LINE secrets not configured; notification skipped.")
        return
    r=requests.post("https://api.line.me/v2/bot/message/push",
      headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},
      json={"to":target,"messages":[{"type":"text","text":text}]},timeout=20)
    r.raise_for_status()

changed=False
for item in items:
    if not item.get("enabled",True): continue
    url=item["url"]; name=item.get("name",url); keyword=item.get("keyword","").strip()
    try:
        r=requests.get(url,headers={"User-Agent":"Mozilla/5.0 Web-Watch/1.0"},timeout=30); r.raise_for_status()
        text=clean(r.text)
        if keyword and keyword not in text:
            print(f"{name}: keyword not found"); continue
        digest=hashlib.sha256(text.encode()).hexdigest()
        old=state.get(url)
        if old and old!=digest:
            line(f"🔔 網站更新通知\n{name}\n偵測到頁面內容變更\n{url}")
            print(f"{name}: changed")
        elif not old: print(f"{name}: baseline created")
        else: print(f"{name}: no change")
        if old!=digest: state[url]=digest; changed=True
    except Exception as e: print(f"{name}: ERROR {e}")
if changed: STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
