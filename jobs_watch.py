import os, json, re, html as html_lib
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup

ROOT=Path(__file__).parent
STATE=ROOT/"jobs_state.json"
state=json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
token=os.getenv("LINE_CHANNEL_ACCESS_TOKEN","")
target=os.getenv("LINE_TARGET_ID","")
groups_url=os.getenv("LINE_GROUPS_URL","")
groups_key=os.getenv("LINE_GROUPS_API_KEY","")
HEAD={"User-Agent":"Mozilla/5.0 Chrome/152 Safari/537.36","Accept-Language":"zh-TW,zh;q=0.9"}
KEYWORDS=["都市計畫","都市規劃","都市更新","都更","危老","土地開發","不動產開發","建設開發","開發評估"]
EXCLUDE=["房仲","仲介業務","電話開發","陌生開發","純業務"]
# Only notify jobs whose posting explicitly requires 4+ years of experience.
EXP_PATTERNS=[r"([4-9]|[1-9]\d)\s*年以上", r"工作經歷.{0,12}([4-9]|[1-9]\d)\s*年", r"經驗.{0,12}([4-9]|[1-9]\d)\s*年"]

def norm(s): return re.sub(r"\s+"," ",html_lib.unescape(s or "")).strip()
def targets():
    out=[]
    if groups_url and groups_key:
        try:
            r=requests.get(groups_url,headers={"Authorization":f"Bearer {groups_key}"},timeout=20); r.raise_for_status()
            out += r.json().get("groups",[])
        except Exception as e: print("group registry:",e)
    if target: out.append(target)
    return list(dict.fromkeys(out))
def push(text):
    for gid in targets():
        try:
            r=requests.post("https://api.line.me/v2/bot/message/push",headers={"Authorization":f"Bearer {token}","Content-Type":"application/json"},json={"to":gid,"messages":[{"type":"text","text":text[:5000]}]},timeout=20); r.raise_for_status()
        except Exception as e: print("LINE:",e)
def relevant(text):
    return any(k in text for k in KEYWORDS) and not any(x in text for x in EXCLUDE)
def exp4plus(text):
    return any(re.search(p, text) for p in EXP_PATTERNS)
def job_page_ok(url):
    try:
        r=requests.get(url,headers=HEAD,timeout=25); r.raise_for_status()
        text=norm(BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True))
        return exp4plus(text)
    except Exception as e:
        print("experience check:",e)
        return False
def google_news(query):
    import xml.etree.ElementTree as ET
    u="https://news.google.com/rss/search?q="+quote_plus(query)+"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant"
    r=requests.get(u,headers=HEAD,timeout=30); r.raise_for_status()
    root=ET.fromstring(r.content); out=[]
    for it in root.findall(".//item"):
        t=norm(it.findtext("title")); link=norm(it.findtext("link"))
        if relevant(t): out.append({"title":t,"url":link})
    return out
def fetch104():
    urls=[
      "https://www.104.com.tw/jobs/search/?keyword="+quote_plus("都市更新"),
      "https://www.104.com.tw/jobs/search/?keyword="+quote_plus("都市計畫"),
      "https://www.104.com.tw/jobs/search/?keyword="+quote_plus("土地開發"),
      "https://www.104.com.tw/jobs/search/?keyword="+quote_plus("不動產開發")]
    out=[]
    for u in urls:
        try:
            r=requests.get(u,headers=HEAD,timeout=30); r.raise_for_status()
            s=BeautifulSoup(r.text,"html.parser")
            for a in s.find_all("a",href=True):
                t=norm(a.get_text(" ",strip=True)); href=a["href"]
                if relevant(t) and ("job" in href or "jobs" in href):
                    if href.startswith("//"): href="https:"+href
                    elif href.startswith("/"): href="https://www.104.com.tw"+href
                    out.append({"title":t,"url":href})
        except Exception as e: print("104:",e)
    return out
def fetch1111():
    u="https://www.1111.com.tw/search/job?ks="+quote_plus("都市更新 土地開發")
    out=[]
    try:
        r=requests.get(u,headers=HEAD,timeout=30); r.raise_for_status()
        s=BeautifulSoup(r.text,"html.parser")
        for a in s.find_all("a",href=True):
            t=norm(a.get_text(" ",strip=True)); href=a["href"]
            if relevant(t) and "/job/" in href:
                if href.startswith("/"): href="https://www.1111.com.tw"+href
                out.append({"title":t,"url":href})
    except Exception as e: print("1111:",e)
    return out

rows=[]
for source,fn in [("104",fetch104),("1111",fetch1111)]:
    got=fn()
    if not got:
        try: got=google_news(f"site:{'104.com.tw' if source=='104' else '1111.com.tw'} (都市更新 OR 土地開發 OR 都市計畫 OR 不動產開發)")
        except Exception as e: print(source,"fallback:",e)
    for x in got:
        x["source"]=source
        if job_page_ok(x["url"]): rows.append(x)
seen=set(); unique=[]
for x in rows:
    key=x["title"]
    if key not in seen: seen.add(key); unique.append(x)
# Permanent history prevents an old/relisted/reordered job from being sent again.
old=set(state.get("seen_titles", state.get("titles",[])))
new=[x for x in unique if x["title"] not in old]
if not state:
    print(f"Jobs baseline created: {len(unique)}")
elif new:
    for i in range(0,len(new),8):
        batch=new[i:i+8]
        msg="💼【新職缺】\n\n"+"\n\n".join(f"• [{x['source']}] {x['title']}\n{x['url']}" for x in batch)
        push(msg)
    print("New jobs:",len(new))
else: print("No new jobs")
all_seen=list(dict.fromkeys(list(old)+[x["title"] for x in unique]))
STATE.write_text(json.dumps({"titles":[x["title"] for x in unique],"seen_titles":all_seen},ensure_ascii=False,indent=2),encoding="utf-8")
