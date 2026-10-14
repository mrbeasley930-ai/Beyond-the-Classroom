#!/usr/bin/env python3
"""Pulls history feeds and writes data/history-news.json for the History page
("The History Line": the Arrivals board, new every morning).

Runs in a GitHub Action every morning (see .github/workflows/history-news.yml).
No third-party packages: urllib + xml + json only.

Three things arrive:
  stories   articles from history magazines, the National Archives' blogs, The Conversation (history only)
            and the Guardian's history books desk          (kind "story", ten days deep)
  podcasts  new episodes of the history podcasts on the department's own list (its October 2026
            poster: nineteen of the twenty-two still running; capped at MAX_PODCASTS on the board)
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
    ("HistoryExtra",        "https://www.historyextra.com/feed/",                    "magazine", "BBC History Magazine's website", 5, None, "story"),
    ("The Conversation",    "https://theconversation.com/uk/arts/articles.atom",      "academic", "academics writing for a general reader: history pieces only", 4, "hist", "story"),
    ("The Guardian",        "https://www.theguardian.com/books/history/rss",         "review",   "the Guardian's history books desk: history books only", 4, "hist", "story"),
    ("The Rest is History", "https://feeds.megaphone.fm/GLT4787413333",              "podcast",  "Tom Holland and Dominic Sandbrook (on the department's list; some strong language)", 3, None, "podcast"),
    ("Cold War Conversations", "https://feeds.megaphone.fm/NSR5326520675",           "podcast",  "oral history of the Cold War from people who were there (on the department's list)", 2, None, "podcast"),
    ("HistoryExtra podcast", "https://feeds.megaphone.fm/GLT5697813216",             "podcast",  "interviews with historians about their new books (on the department's list)", 3, None, "podcast"),
    ("Not Just the Tudors", "https://access.acast.com/rss/b0ed85cc-f4ed-49e9-b860-0ba48481ae25", "podcast", "Suzannah Lipscomb on the early modern world (on the department's list)", 2, None, "podcast"),
    ("Gone Medieval",       "https://access.acast.com/rss/11c1773b-6d50-4dbb-b543-483046bdc241", "podcast", "the Middle Ages (on the department's list)", 1, None, "podcast"),
    # the rest of the department's list (its October 2026 poster), added 14 Oct 2026; one or two each so they share the board
    ("Empire",              "https://feeds.megaphone.fm/empirepodcast",                 "podcast",  "William Dalrymple and Anita Anand on empires, the British one included (on the department's list)", 2, None, "podcast"),
    ("Since Attlee & Churchill", "https://feeds.acast.com/public/shows/68c319db86eb67a6f6b2a910", "podcast", "post-war British politics with a historian of each big party (on the department's list)", 2, None, "podcast"),
    ("More Jam Tomorrow",   "https://rss.libsyn.com/shows/570270/destinations/4934350.xml", "podcast", "Ros Taylor on how life in Britain changed after 1945 (on the department's list)", 1, None, "podcast"),
    ("The Rest is Classified", "https://feeds.megaphone.fm/therestisclassified",        "podcast",  "spy history with Gordon Corera and David McCloskey (on the department's list)", 1, None, "podcast"),
    ("Legacy",              "https://feeds.acast.com/public/shows/68e4c0be965488b63a6987c5", "podcast", "Afua Hirsch and Peter Frankopan on whether famous lives deserve their reputations (on the department's list)", 1, None, "podcast"),
    ("Journey Through Time", "https://feeds.megaphone.fm/journeythroughtime",           "podcast",  "David Olusoga and Sarah Churchwell on forgotten people and unintended consequences (on the department's list)", 1, None, "podcast"),
    ("Presidents, Prime Ministers, Monarchs & Dictators", "https://feeds.captivate.fm/the-presidents-prime-ministers/", "podcast", "Iain Dale on every PM, president, monarch and dictator (on the department's list)", 1, None, "podcast"),
    ("Talk '90s to Me",     "https://feeds.megaphone.fm/NSR2448691535",                 "podcast",  "Miranda Sawyer on the 1990s (on the department's list; some strong language)", 1, None, "podcast"),
    ("Dan Snow's History Hit", "https://access.acast.com/rss/c939f8d1-c4bc-478e-8bb9-e5343f9a7ab5", "podcast", "Dan Snow, any period, usually with the historian who wrote the book (on the department's list)", 2, None, "podcast"),
    ("American History Hit", "https://access.acast.com/rss/62c7fb2a7acf3900129dffa7",  "podcast",  "Don Wildman on the United States (on the department's list)", 1, None, "podcast"),
    ("History Daily",       "https://audioboom.com/channels/5162833.rss",               "podcast",  "Noiser: one event told on its anniversary, twenty minutes (on the department's list)", 1, None, "podcast"),
    ("Short History Of...", "https://feeds.megaphone.fm/NSR8236478683",                 "podcast",  "Noiser: one subject an episode, told as a story (on the department's list)", 1, None, "podcast"),
    ("History's Heroes",    "https://podcasts.files.bbci.co.uk/m001mcbp.rss",           "podcast",  "BBC Radio 4: story-led series on extraordinary people (on the department's list)", 1, None, "podcast"),
    ("The Last Best Hope?", "https://feeds.acast.com/public/shows/62cda17f1c07740014d65e4f", "podcast", "Adam Smith and Oxford's Rothermere American Institute on American political history (on the department's list)", 1, None, "podcast"),
]
# the board takes turns between sources, but with nineteen podcast feeds the podcasts would still outnumber the
# articles two to one, so the podcasts are capped after the turn-taking: the newest from each feed get in first
MAX_PODCASTS = 16
# show pages for feeds whose episodes carry no link of their own and whose channel has no <link> (Goalhanger's megaphone feeds)
HOMES = {
    "Empire": "https://empirepoduk.com/",
    "The Rest is Classified": "https://therestisclassified.com/",
    "Journey Through Time": "https://podcasts.apple.com/gb/podcast/journey-through-time/id1802085505",
    "The Rest is History": "https://therestishistory.com/",
}
OTD_SKIP = re.compile(r"murder|abduct|kidnap|rape|sexual|serial killer|child|girl|boy\b|suicide|massacre of (school|children)", re.I)
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
# the years each tag covers, used for On this day so a 1992 bridge in Kolkata is not tagged to the East India Company
YEARS = {
    "1H Russia 1855–1917": (1850, 1917), "1H Soviet Union 1917–64": (1917, 1964),
    "2S Britain 1951–79": (1945, 1979), "2S Britain 1979–2007": (1979, 2007),
    "NEA: Tudors": (1485, 1603), "NEA: Stuarts": (1603, 1714), "NEA: East India Company": (1600, 1858),
    "NEA: French Revolution": (1688, 1871), "NEA: Franchise": (1800, 1928), "NEA: Ireland": (1798, 1922),
}

# for general feeds: the headline has to look like history
HIST = re.compile(r"histor|centur|\bwar\b|wars\b|empire|imperial|revolution|archive|museum|ancient|medieval|victorian|tudor|stuart|georgian|edwardian|soviet|\btsar|\bkings?\b|queens?\b|monarch|\b1[0-9]{3}s?\b|\b20[0-2]0s\b|fascis|nazi|colonial|slavery|holocaust|cold war|\bera\b|dynasty|\bdig\b|archaeolog|heritage|anniversary", re.I)

# notices and items that are not history
SKIP = re.compile(r"daily quiz|\bquiz\b|^letter:|best .* books|books out in|job opportunit|vacanc|webinar:|sponsored|\bdeals?\b|discount|black friday|subscribe|competition:|win a |\bsale\b|gift guide|best .* to buy|crossword|quiz of the|trailer|^introducing|bonus:|ad-free|members? only", re.I)


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
        if OTD_SKIP.search(text):  # crimes against individuals and children are not for this board
            continue
        # the event text only (page extracts drag in false matches), and only tags whose period the year falls in
        th = [t for t in tag(text) if YEARS.get(t, (-9999, 9999))[0] <= (year or 0) <= YEARS.get(t, (-9999, 9999))[1]]
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
        home = (podcast_home(raw) or HOMES.get(label, "")) if kind == "podcast" else ""
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
    kept, np = [], 0
    for h in mixed:
        if h["kind"] == "podcast":
            if np >= MAX_PODCASTS:
                continue
            np += 1
        kept.append(h)
    mixed = kept
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
