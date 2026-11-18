#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""宠物行业热点日报（云端版，标准库实现）

结构：三块（宠物行业热点 / 全球热点 / AI 热点）
     每块 1–3 张重点卡（标题 + 来源 + 事实摘要 + 影响 + 建议）+ 3–6 条简讯

环境变量：DEEPSEEK_API_KEY / PUSHPLUS_TOKEN / PUSHPLUS_TOPIC / PAGES_URL / DRY_RUN
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
            it["time"] = dt.strftime("%m-%d %H:%M") if dt else ""
            keep.append(it)
        out[section] = keep[:30]
        print(section, "候选", len(out[section]))
    return out

SCHEMA = (
 '{"headline":"今日判断，一句话，25字内",'
 '"summary":"今日总览，40-60字，说清这一天的主线",'
 '"sections":{'
 '"pet":{"focus":[{"title":"","source":"","summary":"事实，60-90字","impact":"行业影响，一句","trend":"趋势判断，一句","advice":"创始人视角/建议，一句具体动作","url":""}],"brief":[{"title":"","url":""}]},'
 '"global":{"focus":[...同上...],"brief":[{"title":"","url":""}]},'
 '"ai":{"focus":[...同上...],"brief":[{"title":"","url":""}]}},'
 '"action":"今日可执行的一件事"}'
)

def llm_summarize(pools):
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        print("无 key，走兜底")
        return None
    parts = []
    for section, items in pools.items():
        lines = ["[%s]" % section]
        for i, it in enumerate(items[:25]):
            lines.append('%d. %s | %s | %s | %s' % (i, it["title"], it.get("desc", "")[:90], it.get("time", ""), it["url"]))
        parts.append("\n".join(lines))
    prompt = (
        "你是绒毛巨兽（宠物营养补充剂品牌）的品牌顾问，每天给创始人写一份内部简报。\n"
        "口吻：顾问——冷静、直接、有立场；先判断，再建议，不绕弯子。不喊口号、不堆形容词、不标题党。\n"
        "把候选新闻整理成 JSON，严格按此结构：\n" + SCHEMA + "\n"
        "规则：\n"
        "1. 每块 focus 1–3 条（真正重要的），brief 3–6 条（只给标题）；每块总数 ≤10；\n"
        "2. focus 每条四段：summary 写事实（谁、做了什么、数字）；impact 写行业影响（这件事改变了什么）；"
        "trend 写趋势判断（往哪走）；advice 写创始人视角（我们该做什么，具体动作）；\n"
        "3. 宠物优先猫/冻干/营养补充剂/渠道；全球优先影响商业与供应链；AI 优先今天能用的；\n"
        "4. url 只能从候选里选，不得编造；source 写媒体名或“Google新闻”；\n"
        "5. 没有够格的新闻就少写，不凑数。\n\n" + "\n\n".join(parts)
    )
    body = json.dumps({"model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=150) as r:
            data = json.loads(r.read().decode())
        return json.loads(data["choices"][0]["message"]["content"])
    except Exception as e:
        print("LLM 失败：", e)
        return None

def fallback(pools):
    def pack(items):
        if not items:
            return {"focus": [], "brief": []}
        it = items[0]
        return {"focus": [{"title": it["title"], "source": it.get("source", ""), "summary": it.get("desc", "")[:90],
                            "impact": "", "advice": "", "url": it["url"]}],
                "brief": [{"title": x["title"][:20], "url": x["url"]} for x in items[1:6]]}
    return {"headline": "今日简报（兜底版）", "summary": "未接 AI，仅按源抓取，建议先看重点卡。",
            "sections": {k: pack(v) for k, v in pools.items()},
            "action": "挑一条与猫/冻干相关的，做成明天的一条选题。"}

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#FBF6EC;color:#2E2A28;font:16px/1.7 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.wrap{max-width:720px;margin:0 auto;padding:26px 18px 60px}
.head{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #9645E1;padding-bottom:14px}
.brand{font-size:25px;font-weight:800;letter-spacing:-.5px;line-height:1.2}
.brand em{font-style:normal;color:#9645E1}
.cov{font-size:12px;color:#9a8b84;margin-top:6px}
.badge{background:#9645E1;color:#fff;border-radius:14px;padding:8px 12px;text-align:center;font-size:13px;line-height:1.35;min-width:76px}
.badge b{display:block;font-size:17px}
.intro{background:#fff;border-radius:14px;padding:16px 18px;margin:20px 0 6px;box-shadow:0 2px 10px rgba(58,42,32,.06);font-size:14px;color:#4a3b34;border-left:4px solid #9645E1}
.intro b{color:#9645E1}
.sec{margin-top:30px}
.sec-h{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.chip{background:#9645E1;color:#fff;border-radius:9px;padding:6px 12px;font-weight:700;font-size:14px;letter-spacing:.5px}
.chip-tag{color:#9a8b84;font-size:12px}
.card{background:#fff;border-radius:14px;padding:18px;margin-bottom:14px;box-shadow:0 2px 12px rgba(58,42,32,.07)}
.card h3{margin:0 0 6px;font-size:17px;line-height:1.45;color:#9645E1}
.meta{font-size:12px;color:#a99a92;margin-bottom:10px}
.card p{margin:0 0 10px;font-size:15px;line-height:1.8;color:#4a3b34}
.kv{font-size:14px;line-height:1.75;margin-top:10px;padding-left:10px;border-left:3px solid #E8DFF6}
.kv b{color:#9645E1;font-weight:700}
.box{background:#F6F1FB;border-radius:10px;padding:12px 14px;margin-top:12px;font-size:14px;line-height:1.75;color:#4a3b34}
.box b{color:#7B3FC4;font-weight:700}
.brief{margin:6px 0 0;padding:0;list-style:none}
.brief li{font-size:14px;color:#4a3b34;padding:7px 0 7px 14px;position:relative;border-top:1px dashed #EEE7F5}
.brief li:before{content:"·";position:absolute;left:2px;color:#9645E1;font-weight:700}
.act{background:#2E2A28;color:#fff;border-radius:14px;padding:16px 18px;margin-top:30px;font-size:15px}
.act b{color:#F2C94C;font-size:12px;letter-spacing:1px;display:block;margin-bottom:5px}
.foot{color:#c3b6ae;font-size:11px;text-align:center;margin-top:26px}
a{color:inherit;text-decoration:none}
"""

def esc(s):
    return html.escape(str(s or ""))

def render(data, d):
    weekday = "一二三四五六日"[NOW.weekday()]
    names = [("pet", "一、宠物行业热点", "产品 · 渠道 · 法规"),
             ("global", "二、全球热点", "经济 · 供应链 · 政策"),
             ("ai", "三、AI 热点", "电商 · 内容 · 工具")]
    secs = []
    total = 0
    for key, name, tag in names:
        blk = data.get("sections", {}).get(key, {}) or {}
        focus = blk.get("focus", []) or []
        brief = blk.get("brief", []) or []
        total += len(focus) + len(brief)
        cards = []
        for x in focus:
            kv = ""
            if x.get("impact"):
                kv += '<div class="kv impact"><b>行业影响｜</b>%s</div>' % esc(x["impact"])
            if x.get("trend"):
                kv += '<div class="kv trend"><b>趋势判断｜</b>%s</div>' % esc(x["trend"])
            if x.get("advice"):
                kv += '<div class="box"><b>创始人视角｜</b>%s</div>' % esc(x["advice"])
            cards.append(
                '<div class="card"><h3>%s</h3>'
                '<div class="meta">%s ｜ <a href="%s">原文</a></div>'
                '<p>%s</p>%s</div>' % (
                    esc(x.get("title")), esc(x.get("source") or "来源"), esc(x.get("url")),
                    esc(x.get("summary")), kv))
        bl = "".join('<li><a href="%s">%s</a></li>' % (esc(x.get("url")), esc(x.get("title"))) for x in brief)
        secs.append('<div class="sec"><div class="sec-h"><span class="chip">%s</span>'
                    '<span class="chip-tag">%s ｜ %d 条</span></div>%s%s</div>' % (
                        name, tag, len(focus) + len(brief), "".join(cards),
                        ('<ul class="brief">%s</ul>' % bl) if bl else ""))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>宠物行业热点日报 · %s</title><style>%s</style></head><body><div class="wrap">
<div class="head"><div><div class="brand">宠物行业热点<em>日报</em></div>
<div class="cov">覆盖：昨日 21:00 — 今日 21:00 ｜ 共 %d 条</div></div>
<div class="badge"><b>%s</b>周%s</div></div>
<div class="intro">聚焦宠物食品 / 营养补充剂 / 渠道电商 / 法规标准 / 全球与 AI 动态，<b>以品牌顾问视角</b>给出判断与建议。今日判断：<b>%s</b></div>
%s
<div class="act"><b>今日可执行的一件事</b>%s</div>
<div class="foot">宠物行业热点日报 · 每日 21:00 更新 · 绒毛巨兽 FURRY HERO</div>
</div></body></html>""" % (esc(d), CSS, total, d[5:], weekday, esc(data.get("headline")), "".join(secs), esc(data.get("action")))

def push(data, url):
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token or os.environ.get("DRY_RUN") == "1":
        print("跳过推送"); return
    def blk(key):
        b = data["sections"].get(key, {}) or {}
        rows = []
        for x in (b.get("focus", []) or [])[:1]:
            rows.append("<b>%s</b><br>%s" % (esc(x.get("title")), esc(x.get("advice") or x.get("summary"))))
        rows += ["· %s" % esc(x.get("title")) for x in (b.get("brief", []) or [])[:3]]
        return "<br>".join(rows) or "今日无更新"
    content = ("<b>今日判断</b><br>%s<hr><b>① 宠物行业热点</b><br>%s<hr><b>② 全球热点</b><br>%s<hr>"
               "<b>③ AI 热点</b><br>%s<hr><b>今日可执行</b><br>%s%s") % (
        esc(data.get("headline")), blk("pet"), blk("global"), blk("ai"), esc(data.get("action")),
        ('<br><a href="%s">看完整日报 →</a>' % url) if url else "")
    body = {"token": token, "title": "宠物行业热点 " + NOW.strftime("%m-%d"),
            "content": content, "template": "html"}
    topic = os.environ.get("PUSHPLUS_TOPIC")
    if topic:
        body["topic"] = topic
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
