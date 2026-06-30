#!/usr/bin/env python3
"""
Sharp — Daily Content Engine (v2: per-vertical pools)
RSS (multi-source) -> per-vertical Claude synthesis -> daily JSON in the app's schema.

For EACH niche it produces ~10 significant, conversation-worthy, cross-corroborated
stories (so a tailored user always has a deep pool), each tagged region = US | Global.
The app then composes a session by evenly splitting across the user's chosen niches.

Guards: facts only from source snippets (anti-hallucination); prefer >=2 sources;
copy rule (informed, never "smarter/IQ").

Dependency-free (stdlib). Run:
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 generate.py              # full run -> output/latest.json
    python3 generate.py --dry-run    # fetch + per-vertical counts only (no key)
"""

import json, os, re, sys, html, uuid, datetime, ssl, urllib.request, urllib.error
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "sources.json")
OUTDIR = os.path.join(HERE, "output")
MODEL = os.environ.get("SHARP_MODEL", "claude-sonnet-4-6")        # cheaper: claude-haiku-4-5-20251001
PER_VERTICAL = int(os.environ.get("SHARP_PER_VERTICAL", "10"))   # target stories per niche
PER_FEED = 22
UA = "SharpContentBot/1.0 (+https://sharpdaily.app)"

VLABEL = {
    "finance": "Finance & markets", "tech": "Technology & science",
    "politics": "Politics & world affairs", "sports": "Sports", "general": "General news",
}
VALID = set(VLABEL)

def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    for p in ("/etc/ssl/cert.pem", "/opt/homebrew/etc/openssl@3/cert.pem"):
        if os.path.exists(p):
            return ssl.create_default_context(cafile=p)
    return ssl.create_default_context()

SSL_CTX = _ssl_context()

# ---------- RSS ----------

def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": UA,
        "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout, context=SSL_CTX) as r:
        return r.read()

def strip_html(s):
    if not s: return ""
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", html.unescape(s)).strip()

def parse_feed(xml_bytes, source, vertical):
    items = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return items
    nodes = root.findall(".//item")
    is_atom = not nodes
    if is_atom:
        nodes = [e for e in root.iter() if e.tag.endswith("entry")]
    for n in nodes[:PER_FEED]:
        def find(tag):
            for c in n:
                if c.tag.endswith(tag): return c
            return None
        title = (getattr(find("title"), "text", "") or "")
        desc_el = (find("summary") or find("content")) if is_atom else find("description")
        desc = (getattr(desc_el, "text", "") or "")
        title, desc = strip_html(title), strip_html(desc)
        if title:
            items.append({"source": source, "title": title, "summary": desc[:400]})
    return items

def fetch_by_vertical():
    cfg = json.load(open(SOURCES))
    groups, report = {}, {}
    for f in cfg["feeds"]:
        v = f["vertical"]
        try:
            got = parse_feed(http_get(f["url"]), f["source"], v)
            groups.setdefault(v, []).extend(got)
            report[f"{v:8} {f['source']}"] = len(got)
        except Exception as e:
            report[f"{v:8} {f['source']}"] = f"ERR {type(e).__name__}"
    return groups, report

# ---------- Claude ----------

PROMPT = """You are the editor of "Sharp", a daily current-affairs briefing app. The reader follows {label}.

From the {label} headlines + summaries below (each tagged with its SOURCE), select the {k} most SIGNIFICANT and CONVERSATION-WORTHY stories from the last day — the kind an informed adult would naturally bring up with a friend or colleague over coffee. Prioritize broad impact, relevance, and talkability; skip niche, procedural, or trivial items.

Rules per story:
- Prefer stories CORROBORATED by 2+ different sources. Only include a single-source story if it is clearly major.
- Use ONLY facts present in the snippets. Never invent names, numbers, dates, or quotes. Skip anything you can't support.
- Neutral, factual tone. NEVER imply the app makes the reader smarter or raises IQ — it's about staying informed.
- "region": "US" if the story is primarily about the United States, otherwise "Global".
- "topicTag": 1-3 words UPPERCASE. "headline": <= 9 words. "summary": 2-3 neutral sentences from the snippets. "readSeconds": 12-25.
- "quiz": a clear "prompt", exactly 4 "options", a 0-based "correctIndex", and a one-sentence "explanation". Answerable directly from your summary.

Aim for {k} strong stories. Quality over quantity — do NOT pad with weak filler.

Return STRICT JSON only (no markdown):
{{"stories":[{{"region":"US","topicTag":"...","headline":"...","summary":"...","readSeconds":18,"sources":["BBC","NPR"],"quiz":{{"prompt":"...","options":["a","b","c","d"],"correctIndex":0,"explanation":"..."}}}}]}}

{label} ITEMS:
{items}
"""

def call_claude(prompt, key):
    body = json.dumps({"model": MODEL, "max_tokens": 6000,
                       "messages": [{"role": "user", "content": prompt}]}).encode()
    req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=body, method="POST",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=120, context=SSL_CTX) as r:
        data = json.loads(r.read())
    text = "".join(b.get("text", "") for b in data.get("content", [])).strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text).rsplit("```", 1)[0].strip()
    return json.loads(text)

def gen_vertical(vertical, items, key):
    lines = "\n".join(f"- [{it['source']}] {it['title']} :: {it['summary'][:200]}" for it in items)
    prompt = PROMPT.format(label=VLABEL[vertical], k=PER_VERTICAL, items=lines)
    result = call_claude(prompt, key)
    cards = []
    for s in result.get("stories", []):
        q = s.get("quiz", {}); opts = q.get("options", [])
        if not s.get("headline") or len(opts) != 4:
            continue
        ci = int(q.get("correctIndex", 0)); ci = ci if 0 <= ci < 4 else 0
        region = s.get("region", "Global"); region = region if region in ("US", "Global") else "Global"
        cards.append({
            "id": str(uuid.uuid4()), "vertical": vertical, "region": region,
            "topicTag": str(s.get("topicTag", "")).upper()[:24],
            "headline": s.get("headline", "").strip(), "summary": s.get("summary", "").strip(),
            "readSeconds": int(s.get("readSeconds", 18)), "sources": s.get("sources", []),
            "quiz": {"prompt": q.get("prompt", "").strip(),
                     "options": [str(o).strip() for o in opts],
                     "correctIndex": ci, "explanation": q.get("explanation", "").strip()},
        })
    return cards

# ---------- main ----------

def main():
    dry = "--dry-run" in sys.argv
    print("Fetching feeds...")
    groups, report = fetch_by_vertical()
    for k, v in sorted(report.items()):
        print(f"  {k}: {v}")
    for v in groups:
        print(f"  -> {v}: {len(groups[v])} items pooled")
    os.makedirs(OUTDIR, exist_ok=True)

    key = os.environ.get("ANTHROPIC_API_KEY")
    if dry or not key:
        json.dump({"fetchedAt": datetime.datetime.now().isoformat(), "groups": {k: len(v) for k, v in groups.items()}},
                  open(os.path.join(OUTDIR, "candidates.json"), "w"), indent=2)
        print("\n[dry] wrote candidates.json" + ("" if key else "  (set ANTHROPIC_API_KEY for full run)"))
        return

    all_cards, by_vertical = [], {}
    for v in ["finance", "tech", "politics", "sports", "general"]:
        if not groups.get(v):
            continue
        print(f"\nSynthesizing {v} with {MODEL}...")
        try:
            cards = gen_vertical(v, groups[v], key)
        except urllib.error.HTTPError as e:
            print(f"  API error {e.code}: {e.read().decode()[:200]}"); continue
        by_vertical[v] = len(cards)
        all_cards.extend(cards)
        print(f"  {len(cards)} cards")

    out = {"generatedAt": datetime.date.today().isoformat(), "model": MODEL,
           "cardCount": len(all_cards), "byVertical": by_vertical, "cards": all_cards}
    date = out["generatedAt"]
    json.dump(out, open(os.path.join(OUTDIR, f"sharp_content_{date}.json"), "w"), indent=2, ensure_ascii=False)
    json.dump(out, open(os.path.join(OUTDIR, "latest.json"), "w"), indent=2, ensure_ascii=False)
    print(f"\n✅ {len(all_cards)} cards total -> output/latest.json")
    print(f"   by vertical: {by_vertical}")

if __name__ == "__main__":
    main()
