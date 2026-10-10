#!/usr/bin/env python3
"""Pulls history feeds and writes data/history-news.json for the History page
("The History Line": the Arrivals board, new every morning).

Runs in a GitHub Action every morning (see .github/workflows/history-news.yml).
No third-party packages: urllib + xml + json only.

Three things arrive:
  stories   articles from history magazines, the National Archives' blogs, The Conversation (history only)
            and the Guardian's history books desk          (kind "story", ten days deep)
  podcasts  new episodes of history podcasts, several of them on the department's own list
                                                            (kind "podcast", three weeks deep)
  onthisday Wikipedia's selected events for today's date    (separate list)
Everything is tagged by keyword to AQA A-level History (7042) as the department teaches it:
1H Tsarist and Communist Russia 1855-1964, 2S The Making of Modern Britain 1951-2007, and the
seven NEA questions. The tags are a rough sort to help a pupil find something near their unit;
they are not a judgement about the story.
"""
import datetime as dt
import email.utils
import hashlib
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "history-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-history-arrivals/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, level, line (what the source is), keep, filter, kind
#   filter: None = keep everything; "hist" = keep only headlines that look like history
FEEDS = [
    ("The National Archives", "https://www.nationalarchives.gov.uk/feeds/blogs.xml", "archive", "the National Archives' own blogs: documents from its collections, explained by its staff", 4, None, "story"),
    ("History Today",       "https://www.historytoday.com/feed/rss.xml",            "magazine", "monthly history magazine written by historians", 5, None, "story"),
    ("HistoryExtra",        "https://www.historyextra.com/feed/",                    "magazine", "BBC History Magazine's website", 5, None, "story"),
    ("The Conversation",    "https://theconversation.com/uk/arts/articles.atom",      "academic", "academics writing for a general reader: history pieces only", 4, "hist", "story"),
    ("The Guardian",        "https://www.theguardian.com/books/history/rss",         "review",   "the Guardian's history books desk: reviews and extracts", 4, None, "story"),
    ("History Hit",         "https://www.historyhit.com/feed/",                      "magazine", "History Hit articles", 3, None, "story"),
    ("The Rest is History", "https://feeds.megaphone.fm/GLT4787413333",              "podcast",  "Tom Holland and Dominic Sandbrook (on the department's list; some strong language)", 3, None, "podcast"),
    ("Cold War Conversations", "https://feeds.megaphone.fm/NSR5326520675",           "podcast",  "oral history of the Cold War from people who were there (on the department's list)", 2, None, "podcast"),
    ("Empire",              "https://feeds.megaphone.fm/empirepodcast",              "podcast",  "William Dalrymple and Anita Anand on empires (on the department's list)", 2, None, "podcast"),
    ("HistoryExtra podcast", "https://feeds.megaphone.fm/GLT5697813216",             "podcast",  "interviews with historians about their new books (on the department's list)", 3, None, "podcast"),
    ("Not Just the Tudors", "https://access.acast.com/rss/b0ed85cc-f4ed-49e9-b860-0ba48481ae25", "podcast", "Suzannah Lipscomb on the early modern world (on the department's list)", 2, None, "podcast"),
    ("Gone Medieval",       "https://access.acast.com/rss/11c1773b-6d50-4dbb-b543-483046bdc241", "podcast", "the Middle Ages (on the department's list)", 1, None, "podcast"),
]
WIKI = "https://en.wikipedia.org/api/rest_v1/feed/onthisday/selected/{mm}/{dd}"

# AQA 7042 as the department teaches it, by keyword (lower-case match)
THEMES = {
    "1H Russia 1855–1917": r"\btsars?\b|tsarist|\bczars?\b|romanov|alexander ii|alexander iii|nicholas ii|rasputin|\bserfs?\b|serfdom|crimean war|russo-japanese|\b1905\b|\bduma\b|stolypin|\bwitte\b|okhrana|narodni|pogrom|imperial russia|trans-siberian|february revolution|provisional government|kerensky|petrograd|st petersburg|russian empire|russification",
    "1H Soviet Union 1917–64": r"\blenin|bolshevik|trotsky|\bstalin|soviet|\bussr\b|gulag|kulak|collectivi|five.year plan|\bpurges?\b|great terror|\bcheka\b|nkvd|\bkgb\b|khrushchev|october revolution|red army|russian civil war|holodomor|\bberia\b|zhukov|kremlin|sputnik|gagarin|leningrad|stalingrad|communis|\b1917\b|russian revolution",
    "2S Britain 1951–79": r"churchill|anthony eden|macmillan|douglas-home|harold wilson|\bheath\b|callaghan|\bsuez\b|windrush|1950s|1960s|1970s|fifties|sixties|seventies|swinging|beatles|teenagers?\b|\bmods\b|rockers|winter of discontent|three-day week|welfare state|decoloni|rhodesia|common market|\beec\b|enoch powell|rivers of blood|profumo|coronation|festival of britain|\bpunk\b|smethwick|beeching|the troubles|northern ireland",
    "2S Britain 1979–2007": r"thatcher|falklands|miners'? strike|orgreave|poll tax|privatis|john major|black wednesday|maastricht|tony blair|new labour|gordon brown|good friday|iraq war|section 28|brixton|toxteth|handsworth|greenham|1980s|1990s|eighties|nineties|princess diana|devolution|millennium|\b7/7\b|9/11",
    "NEA: Tudors": r"\btudors?\b|henry vii\b|henry viii|elizabeth i\b|elizabethan|mary i\b|mary tudor|edward vi|anne boleyn|thomas cromwell|reformation|dissolution of the monasteries|armada|wolsey|cranmer|bosworth|\b1485\b",
    "NEA: Stuarts": r"\bstuarts?\b|james i\b|charles i\b|charles ii|james ii|english civil war|civil wars|oliver cromwell|glorious revolution|william of orange|william iii|bill of rights|restoration|\b1688\b|jacobite|queen anne|gunpowder plot",
    "NEA: East India Company": r"east india|\beic\b|plassey|robert clive|bengal|mughal|the raj\b|indian rebellion|1857|warren hastings|calcutta|kolkata|madras|bombay|colonial india",
    "NEA: French Revolution": r"french revolution|bastille|robespierre|reign of terror|napoleon|bonaparte|louis xvi|marie antoinette|jacobin|\b1789\b|guillotine|paris commune|revolution of 1848|third republic|ancien r[eé]gime",
    "NEA: Franchise": r"reform act|franchise|chartis|suffrag|peterloo|rotten borough|great reform|\b1832\b|\b1867\b|\b1884\b|representation of the people|right to vote|votes for women",
    "NEA: Ireland": r"\bireland\b|\birish\b|fenian|home rule|parnell|easter rising|act of union|o'connell|great famine|potato famine|\bulster\b|sinn f[eé]in|land war|\b1798\b",
    "Historiography": r"historians?\b|historiograph|\barchives?\b|myths?\b|memory|interpretation|revisionis|manuscript|primary source|the evidence|rewrit",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# for general feeds: the headline has to look like history
HIST = re.compile(r"histor|centur|\bwar\b|wars\b|empire|imperial|revolution|archive|museum|ancient|medieval|victorian|tudor|stuart|georgian|edwardian|soviet|\btsar|\bkings?\b|queens?\b|monarch|\b1[0-9]{3}s?\b|\b20[0-2]0s\b|fascis|nazi|colonial|slavery|holocaust|cold war|\bera\b|dynasty|\bdig\b|archaeolog|heritage|anniversary", re.I)

# notices and items that are not history
SKIP = re.compile(r"job opportunit|vacanc|webinar:|sponsored|\bdeals?\b|discount|black friday|subscribe|competition:|win a |\bsale\b|gift guide|best .* to buy|crossword|quiz of the|trailer|^introducing|bonus:|ad-free|members? only", re.I)


def tag(title, desc=""):
    """Tags found in the headline come first; tags found only in the summary are used when the
    headline gives none. At most three."""
    head = [k for k, rx in _rx.items() if rx.search(title)]
    if head:
        return head[:3]
    return [k for k, rx in _rx.items() if rx.search(desc)][:2]


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&#8217;", "’").replace("&#8216;", "‘").replace("&#8220;", "“")
          .replace("&#8221;", "”").replace("&#8211;", "–").replace("&#8212;", "—").replace("&amp;", "&")
          .replace("&#039;", "'").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    s = s.replace("&#160;", " ").replace(" ", " ").replace("&#x27;", "'")
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*The post .*? appeared first on [^.]*\.?", "", s)
    s = re.sub(r"\s*(Continue reading.*|Read more.*|Read full article.*)$", "", s)
    # podcast show notes carry adverts and sign-up blurbs after the first paragraph or two
    s = re.split(r"\s(?:Learn more about your ad choices|See acast\.com/privacy|Hosted on Acast|Join .{0,40} for ad-free|Sign up|Subscribe to|Become a member|Producer:|Produced by)", s)[0]
    return s.strip()


def parse_date(s):
    if not s:
        return None
    try:
        d = email.utils.parsedate_to_datetime(s)
        if d.tzinfo is None:
            d = d.replace(tzinfo=dt.timezone.utc)
        return d.astimezone(dt.timezone.utc)
    except Exception:
        pass
    try:
        return dt.datetime.fromisoformat(s.strip().replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None


def fetch(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not link.startswith("http"):  # podcast items often have no link: use the enclosure page or guid
            g = (it.findtext("guid") or "").strip()
            link = g if g.startswith("http") else ""
        desc = clean(it.findtext("description") or it.findtext("itunes:summary", namespaces=NS) or "")
        date = parse_date(it.findtext("pubDate") or it.findtext("dc:date", namespaces=NS))
        out.append((title, link, desc, date))
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
        title = clean(it.findtext("atom:title", namespaces=NS))
        link = ""
        for l in it.findall("atom:link", NS):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        desc = clean(it.findtext("atom:summary", namespaces=NS) or it.findtext("atom:content", namespaces=NS) or "")
        date = parse_date(it.findtext("atom:published", namespaces=NS) or it.findtext("atom:updated", namespaces=NS))
        out.append((title, link, desc, date))
    return out


def podcast_home(xml_bytes):
    """The show's own web page, for episodes that carry no link of their own."""
    try:
        ch = ET.fromstring(xml_bytes).find("channel")
        return (ch.findtext("link") or "").strip() if ch is not None else ""
    except Exception:
        return ""


def on_this_day(now):
    london = now + dt.timedelta(hours=1)  # close enough to the UK date at 07:10 UTC all year
    url = WIKI.format(mm=f"{london.month:02d}", dd=f"{london.day:02d}")
    try:
        j = json.loads(fetch(url, "application/json"))
    except Exception as e:
        print(f"!! Wikipedia on this day: {e}", file=sys.stderr)
        return [], False
    out = []
    for ev in j.get("selected", []):
        text = clean(ev.get("text", ""))
        year = ev.get("year")
        pages = ev.get("pages") or []
        link = ""
        if pages:
            link = (((pages[0].get("content_urls") or {}).get("desktop") or {}).get("page")) or ""
        if not text or not link:
            continue
        th = tag(text, " ".join(clean(p.get("extract", "")) for p in pages[:2]))
        out.append({"id": "otd-" + hashlib.sha1((str(year) + text).encode("utf-8")).hexdigest()[:8],
                    "year": year, "text": text, "url": link, "th": th})
    # on the course first, then the rest by year
    out.sort(key=lambda e: (0 if e["th"] else 1, -(e["year"] or 0)))
    return out[:14], True


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = {"story": now - dt.timedelta(days=10), "podcast": now - dt.timedelta(days=21)}
    stories, sources = [], []
    for label, url, level, line, keep, filt, kind in FEEDS:
        try:
            raw = fetch(url)
            got = items_from(raw)
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "level": level, "line": line, "kind": kind, "ok": False, "n": 0})
            continue
        home = podcast_home(raw) if kind == "podcast" else ""
        got = [(t, (l if l.startswith("http") else home), d, dd) for t, l, d, dd in got if t]
        got = [g for g in got if g[1].startswith("http")]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date in got:
            if date and date < cutoff[kind]:
                continue
            if n >= keep:
                break
            if SKIP.search(title):
                continue
            if filt == "hist" and not HIST.search(title):
                continue
            th = tag(title, desc)
            stories.append({
                "id": hashlib.sha1((link + title).encode("utf-8")).hexdigest()[:10],
                "src": label, "level": level, "kind": kind, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": th,
            })
            n += 1
        sources.append({"src": label, "level": level, "line": line, "kind": kind, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")
    seen, uniq = set(), []
    for h in sorted(stories, key=lambda h: h["date"], reverse=True):
        k = h["url"].split("?")[0] + ("" if h["kind"] == "story" else h["title"])
        t = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
        if k in seen or t in seen:
            continue
        seen.add(k); seen.add(t)
        uniq.append(h)
    # newest first, but take turns between sources so one busy feed cannot fill the top of the list
    queues = {}
    for h in uniq:
        queues.setdefault(h["src"], []).append(h)
    order = sorted(queues, key=lambda k: queues[k][0]["date"], reverse=True)
    mixed = []
    while any(queues.values()):
        for k in order:
            if queues[k]:
                mixed.append(queues[k].pop(0))
    otd, otd_ok = on_this_day(now)
    sources.append({"src": "Wikipedia: On this day", "level": "reference", "line": "Wikipedia's selected events for today's date", "kind": "onthisday", "ok": otd_ok, "n": len(otd)})
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": mixed,
        "onthisday": otd,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(mixed)} arrivals and {len(otd)} on this day")
    if len(mixed) < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
