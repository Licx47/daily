#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""全球行业热点推送（云端版，标准库实现）
三块：一、宠物行业热点 / 二、全球热点 / 三、AI 热点
每块 10–13 条，编号排列；每条：事实 + 行业影响 + 反思 + 建议
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
    "pet": ["宠物食品", "猫 冻干", "宠物营养补充剂", "宠物品牌 融资", "宠物 电商", "宠物 政策 标准", "宠物 展会"],
    "global": ["全球经济", "美联储 利率", "芯片 供应链", "监管 新规", "国际贸易 关税", "消费 零售 数据"],
    "ai": ["AI 电商", "AI 宠物", "大模型 发布", "AI 创业", "AI 视频 生成", "AI Agent 落地"],
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
                d["desc"] = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", txt))[:220]
            elif t in ("pubdate", "published", "updated"):
                d["pub"] = txt
        if d.get("title") and d.get("url"):
            items.append(d)
    return items

def collect():
    pools = {"pet": [], "global": [], "ai": []}
    for section, queries in QUERIES.items():
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
        out[section] = keep[:45]
        print(section, "候选", len(out[section]))
    return out

SCHEMA = (
 '{"headline":"今日判断，一句话，25字内",'
 '"summary":"今日总览，50-70字",'
 '"sections":{'
 '"pet":[{"title":"","source":"","fact":"事实，50-70字，含数字","impact":"行业影响，一句","insight":"引发的反思，一句","advice":"建议动作，一句","url":""}],'
 '"global":[...同上...],"ai":[...同上...]},'
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
        for i, it in enumerate(items[:40]):
            lines.append('%d. %s | %s | %s | %s' % (i, it["title"], it.get("desc", "")[:100], it.get("time", ""), it["url"]))
        parts.append("\n".join(lines))
    prompt = (
        "你是绒毛巨兽（宠物营养补充剂品牌）的品牌顾问，每天给创始人写一份行业简报。\n"
        "口吻：冷静、直接、有立场。不喊口号、不堆形容词、不标题党。\n"
        "把候选新闻整理成 JSON，严格按此结构：\n" + SCHEMA + "\n"
        "规则：\n"
        "1. 每块 10–13 条，按重要性从高到低排列（第一条必须是最重要的）；\n"
        "2. 每条四段：fact 写事实（谁、做了什么、关键数字）；impact 写这件事改变了什么；"
        "insight 写“引发的反思”（更上一层的判断，可以说行业规律、供需变化、竞争格局）；"
        "advice 写“我们该做什么”（一句具体动作）；\n"
        "3. 宠物优先猫/冻干/营养补充剂/渠道/政策/展会；全球优先影响商业、供应链、政策；"
        "AI 优先电商、内容、工具，且能落到我们身上的；\n"
        "4. url 只能从候选里选，不得编造；source 写媒体名；\n"
        "5. 数量不够时宁可重复不同视角，也不要编造新闻。\n\n" + "\n\n".join(parts)
    )
    body = json.dumps({"model": "deepseek-chat",
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3, "max_tokens": 8000,
        "response_format": {"type": "json_object"}}).encode()
    req = urllib.request.Request("https://api.deepseek.com/chat/completions", data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer " + key})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            data = json.loads(r.read().decode())
        return json.loads(data["choices"][0]["message"]["content"])
    except Exception as e:
        print("LLM 失败：", e)
        return None

def fallback(pools):
    def pack(items):
        return [{"title": it["title"], "source": it.get("source", ""), "fact": it.get("desc", "")[:90],
                 "impact": "", "insight": "", "advice": "", "url": it["url"]} for it in items[:10]]
    return {"headline": "今日简报（兜底版）", "summary": "未接 AI，仅按源抓取。",
            "sections": {k: pack(v) for k, v in pools.items()},
            "action": "挑一条与猫/冻干相关的，做成明天的一条选题。"}

CSS = """
*{box-sizing:border-box}
body{margin:0;background:#FBF6EC;color:#2E2A28;font:16px/1.75 -apple-system,'PingFang SC','Microsoft YaHei',sans-serif}
.wrap{max-width:760px;margin:0 auto;padding:26px 18px 60px}
.head{display:flex;justify-content:space-between;align-items:flex-start;border-bottom:3px solid #9645E1;padding-bottom:14px}
.brand{font-size:26px;font-weight:800;letter-spacing:-.5px;line-height:1.25}
.brand em{font-style:normal;color:#9645E1}
.cov{font-size:12px;color:#9a8b84;margin-top:6px}
.badge{background:#9645E1;color:#fff;border-radius:14px;padding:8px 12px;text-align:center;font-size:13px;line-height:1.35;min-width:78px}
.badge b{display:block;font-size:17px}
.intro{background:#fff;border-radius:14px;padding:16px 18px;margin:20px 0 6px;box-shadow:0 2px 10px rgba(58,42,32,.06);font-size:14.5px;color:#4a3b34;border-left:4px solid #9645E1;line-height:1.8}
.intro b{color:#9645E1}
.sec{margin-top:32px}
.sec-h{display:flex;align-items:center;gap:10px;margin-bottom:14px;border-bottom:2px solid #EDE4F8;padding-bottom:8px}
.chip{background:#9645E1;color:#fff;border-radius:9px;padding:6px 14px;font-weight:700;font-size:15px;letter-spacing:.5px}
.chip-tag{color:#9a8b84;font-size:12px;margin-left:auto}
.card{background:#fff;border-radius:14px;padding:18px;margin-bottom:14px;box-shadow:0 2px 12px rgba(58,42,32,.07)}
.card h3{margin:0 0 6px;font-size:17px;line-height:1.5;color:#7B3FC4}
.card h3 span.n{color:#C9B8E4;margin-right:6px;font-weight:800}
.meta{font-size:12px;color:#a99a92;margin-bottom:10px}
.card p{margin:0 0 8px;font-size:15px;line-height:1.8;color:#4a3b34}
.kv{font-size:14px;line-height:1.8;margin-top:8px;padding-left:10px;border-left:3px solid #E8DFF6}
.kv b{color:#9645E1}
.box{background:#F6F1FB;border-radius:10px;padding:12px 14px;margin-top:10px;font-size:14px;line-height:1.8;color:#4a3b34}
.box b{color:#7B3FC4}
.act{background:#2E2A28;color:#fff;border-radius:14px;padding:16px 18px;margin-top:30px;font-size:15px}
.act b{color:#F2C94C;font-size:12px;letter-spacing:1px;display:block;margin-bottom:5px}
.foot{color:#c3b6ae;font-size:11px;text-align:center;margin-top:26px}
a{color:inherit;text-decoration:none}
"""

def esc(s):
    return html.escape(str(s or ""))

def render(data, d):
    weekday = "一二三四五六日"[NOW.weekday()]
    names = [("pet", "一、宠物行业热点", "产品 · 渠道 · 政策 · 供应链"),
             ("global", "二、全球热点", "经济 · 地缘 · 产业"),
             ("ai", "三、AI 热点", "电商 · 内容 · 工具 · 创业")]
    secs, total = [], 0
    for key, name, tag in names:
        items = data.get("sections", {}).get(key, []) or []
        total += len(items)
        cards = []
        for i, x in enumerate(items, 1):
            kv = ""
            if x.get("impact"):
                kv += '<div class="kv"><b>行业影响｜</b>%s</div>' % esc(x["impact"])
            if x.get("insight"):
                kv += '<div class="kv"><b>反思｜</b>%s</div>' % esc(x["insight"])
            if x.get("advice"):
                kv += '<div class="box"><b>建议｜</b>%s</div>' % esc(x["advice"])
            cards.append(
                '<div class="card"><h3><span class="n">%d.</span>%s</h3>'
                '<div class="meta">%s ｜ <a href="%s">原文</a></div>'
                '<p>%s</p>%s</div>' % (
                    i, esc(x.get("title")), esc(x.get("source") or "来源"), esc(x.get("url")),
                    esc(x.get("fact")), kv))
        secs.append('<div class="sec"><div class="sec-h"><span class="chip">%s</span>'
                    '<span class="chip-tag">%s ｜ %d 条</span></div>%s</div>' % (
                        name, tag, len(items), "".join(cards)))
    return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>全球行业热点推送 · %s</title><style>%s</style></head><body><div class="wrap">
<div class="head"><div><div class="brand">全球行业热点<em>推送</em></div>
<div class="cov">覆盖：昨日 21:00 — 今日 21:00 ｜ 共 %d 条</div></div>
<div class="badge"><b>%s</b>周%s</div></div>
<div class="intro"><b>今日判断｜</b>%s<br><span style="color:#8b7a72">%s</span></div>
%s
<div class="act"><b>今日可执行的一件事</b>%s</div>
<div class="foot">全球行业热点推送 · 每日 21:00 更新 · 绒毛巨兽 FURRY HERO</div>
</div></body></html>""" % (esc(d), CSS, total, d[5:], weekday, esc(data.get("headline")), esc(data.get("summary")), "".join(secs), esc(data.get("action")))

def push(data, url):
    token = os.environ.get("PUSHPLUS_TOKEN")
    if not token or os.environ.get("DRY_RUN") == "1":
        print("跳过推送"); return
    def top(key, n=2):
        rows = []
        for x in (data["sections"].get(key, []) or [])[:n]:
            rows.append("<b>%s</b><br>%s" % (esc(x.get("title")), esc(x.get("advice") or x.get("impact"))))
        return "<br>".join(rows) or "今日无更新"
    content = ("<b>今日判断</b><br>%s<hr><b>一、宠物行业热点</b><br>%s<hr><b>二、全球热点</b><br>%s<hr>"
               "<b>三、AI 热点</b><br>%s<hr><b>今日可执行</b><br>%s%s") % (
        esc(data.get("headline")), top("pet"), top("global"), top("ai"), esc(data.get("action")),
        ('<br><a href="%s">看完整日报 →</a>' % url) if url else "")
    body = {"token": token, "title": "全球行业热点 " + NOW.strftime("%m-%d"),
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
