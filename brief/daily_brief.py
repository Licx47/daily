#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""绒毛巨兽 · 每日行业简报（云端版，标准库实现）

结构：每块「1–2 条重点（展开说）+ 3–6 条简讯（一句话带过）」，
     重点放版面，简讯只留标题，整体控制在一屏半。

环境变量：DEEPSEEK_API_KEY / PUSHPLUS_TOKEN / PAGES_URL / DRY_RUN
"""
import os, re, json, html, datetime, urllib.request, urllib.parse
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

TZ = datetime.timezone(datetime.timedelta(hours=8))
NOW = datetime.datetime.now(TZ)
SINCE = NOW - datetime.timedelta(hours=24)
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36")

QUERIES = {
    "pet": ["宠物食品", "猫 冻干", "宠物营养补充剂", "宠物品牌 融资", "宠物 电商"],
    "global": ["全球经济", "美联储 利率", "芯片 供应链", "监管 新规"],
    "ai": ["AI 电商", "AI 宠物", "大模型 发布", "AI 创业"],
}
EXTRA_FEEDS = [
    ("https://36kr.com/feed", "pet", "36氪"),
    ("https://openai.com/blog/rss.xml", "ai", "OpenAI"),
]

def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def to_dt(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).astimezone(TZ)
    except Exception:
        return None

def parse_feed(raw):
    items = []
    try:
        root = ET.fromstring(raw)
    except Exception:
        return items
    for node in root.iter():
        if node.tag.split("}")[-1].lower() not in ("item", "entry"):
            continue
        d = {}
        for ch in node:
            t = ch.tag.split("}")[-1].lower()
            txt = (ch.text or "").strip()
            if t == "title":
                d["title"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", txt))
            elif t == "link":
                d["url"] = txt or ch.attrib.get("href", "")
            elif t in ("description", "summary", "content"):
                d["desc"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", txt))[:200]
            elif t in ("pubdate", "published", "updated"):
                d["pub"] = txt
        if d.get("title") and d.get("url"):
            items.append(d)
    return items

def collect():
    pools = {"pet": [], "global": [], "ai": []}
    for section, queries in QUERIES.items():
        if os.environ.get("DRY_RUN") == "1":
            break
        for q in queries:
            url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(q)
                   + "&hl=zh-CN&gl=CN&ceid=CN:zh-Hans")
            try:
                for it in parse_feed(fetch(url)):
                    it["source"] = "Google新闻"
                    pools[section].append(it)
            except Exception as e:
                print("skip", q, e)
    for url, section, name in EXTRA_FEEDS:
        try:
            for it in parse_feed(fetch(url)):
                it["source"] = name
                pools[section].append(it)
        except Exception as e:
            print("skip feed", name, e)
    out = {}
    for section, items in pools.items():
        seen, keep = set(), []
        for it in items:
            k = re.sub(r"\W+", "", it["title"])[:40]
            if k in seen:
                continue
            seen.add(k)
            dt = to_dt(it.get("pub"))
            if dt and dt < SINCE:
                continue
            keep.append(it)
        out[section] = keep[:30]
        print(section, "候选", len(out[section]))
    return out

SCHEMA = (
 '{"headline":"今日最该关注的一件事，20字内",'
 '"sections":{'
 '"pet":{"focus":[{"title":"","detail":"为什么重要，60-90字","url":""}],"brief":[{"title":"不超过18字","url":""}]},'
 '"global":{"focus":[{"title":"","detail":"影响我们哪一环，60-90字","url":""}],"brief":[{"title":"","url":""}]},'
 '"ai":{"focus":[{"title":"","detail":"能怎么用在我身上，一句具体动作","url":""}],"brief":[{"title":"","url":""}]}},'
 '"action":"今日可执行的一件事，一句具体动作"}'
)

def llm_summarize(pools):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("无 DEEPSEEK_API_KEY，走兜底")
        return None
    parts = []
    for section, items in pools.items():
        lines = ["[%s]" % section]
        for i, it in enumerate(items[:25]):
            lines.append('%d. %s | %s | %s' % (i, it["title"], it.get("desc", "")[:90], it["url"]))
        parts.append("\n".join(lines))
    prompt = (
        "你是宠物品牌创业者的一日情报助理，风格要短、要狠、要有判断。\n"
        "把下面候选新闻整理成 JSON（严格按此结构）：\n" + SCHEMA + "\n"
        "规则：\n"
        "1. 每块 focus 1–2 条（真正重要的，detail 要写清'对我们意味着什么'），brief 3–6 条（只给标题，一句话带过）；\n"
        "2. 每块总数不超过 10 条；\n"
        "3. 宠物优先猫/冻干/营养补充剂；全球优先影响商业与供应链；AI 优先今天能用的；\n"
        "4. url 只能从候选里选，不得编造；\n"
        "5. 不要流水账，宁可少一条。\n\n" + "\n\n".join(parts)
    )
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "response_format": {"type": "json_object"},
    }).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            data = json.loads(r.read().decode())
        return json.loads(data["choices"][0]["message"]["content"])
    except Exception as e:
        print("LLM 失败：", e)
        return None

def fallback(pools):
    def pack(items):
        if not items:
            return {"focus": [], "brief": []}
        return {"focus": [{"title": items[0]["title"], "detail": items[0].get("desc", "")[:80], "url": items[0]["url"]}],
                "brief": [{"title": it["title"][:18], "url": it["url"]} for it in items[1:6]]}
    return {"headline": "今日简报（自动兜底，未做 AI 归纳）",
            "sections": {k: pack(v) for k, v in pools.items()},
            "action": "挑一条与猫/冻干相关的，做成明天的选题。"}

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#FBF6EC;color:#3A2A20;font:16px/1.65 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:22px 18px 56px}
.top{font-size:12px;color:#9645E1;font-weight:700;letter-spacing:.6px}
h1{font-size:23px;line-height:1.42;margin:10px 0 6px;letter-spacing:-.2px}
.sub{color:#9a8b84;font-size:13px}
.sec{margin:26px 0 0}
.sec h2{font-size:14px;color:#9645E1;margin:0 0 10px;letter-spacing:1px;display:flex;justify-content:space-between;align-items:baseline}
.sec h2 span{font-size:11px;color:#bbb;font-weight:400;letter-spacing:0}
.focus{background:#fff;border-left:4px solid #9645E1;border-radius:10px;padding:14px 15px;margin-bottom:10px;box-shadow:0 1px 8px rgba(58,42,32,.05)}
.focus .t{font-weight:700;font-size:16px;line-height:1.45}
.focus .d{color:#5f5048;font-size:14px;margin-top:6px}
.focus a{color:#b9a7c9;font-size:11px;text-decoration:none;display:inline-block;margin-top:6px;word-break:break-all}
.brief{margin:2px 0 0;padding:0;list-style:none}
.brief li{font-size:14px;color:#4a3b34;padding:5px 0 5px 14px;position:relative}
.brief li:before{content:"·";position:absolute;left:2px;color:#9645E1;font-weight:700}
.brief a{color:inherit;text-decoration:none}
.act{background:#3A2A20;color:#fff;border-radius:12px;padding:15px 16px;margin-top:28px;font-size:15px}
.act b{color:#F2C94C;font-size:12px;letter-spacing:1px;display:block;margin-bottom:4px}
.foot{color:#c3b6ae;font-size:11px;text-align:center;margin-top:26px}
"""

def esc(s):
    return html.escape(str(s or ""))

def render(data, d):
    names = [("pet", "① 宠物"), ("global", "② 全球重大事件"), ("ai", "③ AI × 我在做的事")]
    out = []
    total = 0
    for key, name in names:
        blk = data.get("sections", {}).get(key, {}) or {}
        focus = blk.get("focus", []) or []
        brief = blk.get("brief", []) or []
        total += len(focus) + len(brief)
        fh = "".join(
            '<div class="focus"><div class="t">%s</div><div class="d">%s</div>'
            '<a href="%s" target="_blank">来源</a></div>' % (esc(x.get("title")), esc(x.get("detail")), esc(x.get("url")))
            for x in focus)
        bh = "".join('<li><a href="%s" target="_blank">%s</a></li>' % (esc(x.get("url")), esc(x.get("title")))
                     for x in brief)
        out.append('<div class="sec"><h2>%s<span>%d 条</span></h2>%s%s</div>'
                   % (name, len(focus) + len(brief), fh, ('<ul class="brief">%s</ul>' % bh) if bh else ""))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>绒毛巨兽 · %s 行业日报</title><style>%s</style></head><body><div class="wrap">
<div class="top">%s ｜ 昨日21:00 — 今日21:00 ｜ 共 %d 条</div>
<h1>%s</h1><div class="sub">只看这一句，也够用今天。</div>
%s
<div class="act"><b>今日可执行的一件事</b>%s</div>
<div class="foot">绒毛巨兽 FURRY HERO · 让每次投喂，都变成游戏</div>
</div></body></html>""" % (esc(d), CSS, esc(d), total, esc(data.get("headline")), "".join(out), esc(data.get("action")))

def push(data, url):
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token or os.environ.get("DRY_RUN") == "1":
        print("跳过推送"); return
    def blk(key):
        b = data["sections"].get(key, {}) or {}
        f = b.get("focus", [])[:1]
        s = "".join("<b>%s</b><br>%s<br>" % (esc(x.get("title")), esc(x.get("detail"))) for x in f)
        rest = "".join("· %s<br>" % esc(x.get("title")) for x in (b.get("brief", []) or [])[:4])
        return (s + rest) or "今日无重大更新<br>"
    content = ("<b>今日最该关注</b><br>%s<hr><b>① 宠物</b><br>%s<hr><b>② 全球</b><br>%s<hr>"
               "<b>③ AI × 我在做的事</b><br>%s<hr><b>今日可执行</b><br>%s%s") % (
        esc(data.get("headline")), blk("pet"), blk("global"), blk("ai"), esc(data.get("action")),
        ('<br><a href="%s">看完整日报 →</a>' % url) if url else "")
    body = {"token": token, "title": "绒毛巨兽日报 " + NOW.strftime("%m-%d"),
            "content": content, "template": "html"}
    topic = os.environ.get("PUSHPLUS_TOPIC")
    if topic:
        body["topic"] = topic          # 群组推送：组内所有人都能收到
    payload = json.dumps(body).encode()
    req = urllib.request.Request("https://www.pushplus.plus/send", data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            print("推送：", r.read().decode()[:160])
    except Exception as e:
        print("推送失败：", e)

def main():
    pools = collect()
    data = llm_summarize(pools) or fallback(pools)
    d = NOW.strftime("%Y-%m-%d")
    os.makedirs("docs", exist_ok=True)
    page = render(data, d)
    open("docs/index.html", "w", encoding="utf-8").write(page)
    open("docs/%s.html" % d, "w", encoding="utf-8").write(page)
    olds = sorted(x for x in os.listdir("docs") if re.match(r"\d{4}-\d{2}-\d{2}\.html$", x))
    for x in olds[:-30]:
        os.remove(os.path.join("docs", x))
    print("已生成 docs/index.html 与 docs/%s.html" % d)
    push(data, os.environ.get("PAGES_URL", ""))

if __name__ == "__main__":
    main()
