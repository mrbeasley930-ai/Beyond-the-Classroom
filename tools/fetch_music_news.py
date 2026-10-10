#!/usr/bin/env python3
"""Builds data/music-news.json for the Music page ("The Score": the programme at the back).

Two lists:
  broadcasts  this week's episodes of Radio 3 and Radio 4 music programmes, read from the BBC
              programme pages (the /episodes/player list, then each episode page for its date),
              each with a link to listen on BBC Sounds
  stories     music news and reviews from public RSS feeds

Runs in a GitHub Action every morning (see .github/workflows/music-news.yml).
No third-party packages: urllib + xml + re only.

Every item is tagged to the areas of study of AQA A-level Music (7272) by keyword.
The tags are a rough sort to help a pupil find something that touches the area they are on;
they are not a judgement about the item. An item with no tag is "Beyond the course".
"""
import datetime as dt
import email.utils
import hashlib
import html
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "music-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-music-programme/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"
DAYS = 10          # stories
BDAYS = 21         # broadcasts stay on BBC Sounds for a month, and the programme pages can lag a week

# BBC programmes: label, pid, station, line, how many of the latest episodes to look at
PROGRAMMES = [
    ("Composer of the Week",  "b006tnxf", "Radio 3", "five programmes a week on one composer", 5),
    ("Record Review",         "b06w2121", "Radio 3", "Saturday mornings: Building a Library compares recordings of one piece", 2),
    ("Music Matters",         "b006tnvx", "Radio 3", "the weekly magazine on the musical world", 2),
    ("The Early Music Show",  "b006tn49", "Radio 3", "Baroque and earlier", 2),
    ("Jazz Record Requests",  "b006tnn9", "Radio 3", "jazz records and the stories behind them", 2),
    ("Add to Playlist",       "m00106lb", "Radio 4", "two musicians link five tracks by what is in the music", 2),
    ("Soul Music",            "b008mj7p", "Radio 4", "one piece of music and the lives it has run through", 2),
]

# label, feed url, line (what the source is), keep, filter
#   filter: None = keep everything; "music" = keep only headlines that name music (MUSIC); "musical" = keep stage musicals only
FEEDS = [
    ("Guardian · classical",  "https://www.theguardian.com/music/classical-music-and-opera/rss", "classical music and opera reviews and news", 7, None),
    ("Guardian · jazz",       "https://www.theguardian.com/music/jazz/rss",                       "jazz reviews", 3, None),
    ("Guardian · musicals",   "https://www.theguardian.com/stage/musicals/rss",                   "musical theatre reviews", 3, None),
    ("Guardian · music",      "https://www.theguardian.com/music/rss",                            "the main music desk: pop and everything else", 6, "music"),
    ("OperaWire",             "https://operawire.com/feed/",                                      "opera news and reviews, worldwide", 4, None),
    ("WhatsOnStage",          "https://www.whatsonstage.com/feed/",                               "theatre news: musicals only", 3, "musical"),
    ("The Arts Desk",         "https://www.theartsdesk.com/rss.xml",                              "arts reviews: music only", 4, "music"),
    ("Pitchfork",             "https://pitchfork.com/feed/feed-news/rss",                         "pop, hip-hop and electronic music news", 4, None),
    ("NME",                   "https://www.nme.com/news/music/feed",                              "pop and rock news", 3, None),
    ("Folk Radio UK",         "https://www.folkradio.co.uk/feed/",                                "folk, traditional and world music", 4, None),
    ("MusicRadar",            "https://www.musicradar.com/rss",                                   "production, instruments and technology", 3, "music"),
]

# AQA 7272 areas of study, by keyword (lower-case match). Order matters: first match is the main tag.
THEMES = {
    "AoS1 Baroque solo concerto": r"baroque|purcell|vivaldi|\bbach\b|handel|concerto|harpsichord|continuo|monteverdi|corelli|telemann|scarlatti|rameau|couperin|early music|period instrument|lute|viol\b|recorder",
    "AoS1 Mozart opera":          r"mozart|figaro|don giovanni|cos[iì] fan|magic flute|zauberfl|opera|operatic|aria|libretto|glyndebourne|royal opera|\beno\b|english national opera|welsh national opera|opera north|scottish opera|soprano|tenor|baritone|mezzo",
    "AoS1 Chopin, Brahms, Grieg": r"chopin|brahms|grieg|nocturne|ballade|intermezzo|lyric pieces|schumann|liszt|mendelssohn|\bpiano\b|pianist|rachmaninov|schubert|romantic",
    "AoS1 Western classical 1650–1910": r"beethoven|haydn|tchaikovsky|dvo[rř][aá]k|mahler|bruckner|wagner|verdi|puccini|bizet|carmen|elgar|rossini|donizetti|bellini|berlioz|saint-sa|faur[eé]|debussy|ravel|sibelius|symphon|sonata|quartet|chamber music|orchestra|conductor|philharmonic|proms?\b|recital|choir|choral|cathedral|organ\b|classical|string|cellist|violinist|oratorio|requiem|mass\b",
    "AoS2 Pop music":             r"stevie wonder|joni mitchell|\bmuse\b|beyonc|daft punk|labrinth|pop star|pop music|\bpop\b|billboard|hip-hop|hip hop|\brap\b|rapper|grime|r&b|\bsoul\b|indie|rock band|rocker|punk|metal\b|singer-songwriter|songwriter|glastonbury|brit awards|grammy|mercury prize|streaming|spotify|taylor swift|dua lipa|billie eilish|kendrick|drake\b|sampling|synth|electronic|techno|house music|dance music|\bdj\b|chart-topping|number one",
    "AoS3 Music for media":       r"herrmann|hans zimmer|giacchino|thomas newman|uematsu|film score|film music|soundtrack|\bscore\b|composer for|video game|game music|games? score|television|tv theme|\bbbc proms.*film|cinema|movie|oscar|bafta|streaming series|netflix|disney|pixar|star wars|interstellar",
    "AoS4 Music for theatre":     r"kurt weill|rodgers|sondheim|sch[oö]nberg|jason robert brown|musical theatre|musical theater|\bmusicals?\b|west end|broadway|threepenny|les mis|hamilton|wicked|lloyd webber|cabaret|cast recording|cast album|olivier award|tony award",
    "AoS5 Jazz":                  r"\bjazz\b|armstrong|ellington|charlie parker|miles davis|metheny|simcock|bebop|big band|swing\b|improvis|ronnie scott|blue note|saxophon|trumpeter|coltrane|monk\b|mingus|herbie hancock",
    "AoS6 Contemporary traditional": r"piazzolla|tango|diabat|\bkora\b|anoushka|shankar|sitar|mariza|fado|bellowhead|folk\b|traditional music|world music|flamenco|ceilidh|ceili|bluegrass|celtic|gamelan|afrobeat|raga|bhangra|qawwali|cajun|klezmer|balkan|nordic folk|fiddle|accordion|uilleann|bagpipe",
    "AoS7 Art music since 1910":  r"shostakovich|messiaen|steve reich|macmillan|minimalis|stravinsky|schoenberg|berg\b|webern|bart[oó]k|britten|tippett|ligeti|boulez|stockhausen|cage\b|glass\b|adams\b|birtwistle|ad[eè]s|contemporary classical|new music|premiere|world premiere|new commission|commission|electronic music|electroacoustic|avant",
    "Performing":                 r"audition|competition|young musician|conservatoire|royal academy|royal college|guildhall|rncm|trinity laban|youth orchestra|\bnyo\b|national youth|masterclass|prize|award|scholarship|grade 8|abrsm|\bexams?\b",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# for general feeds: the headline has to name music
MUSIC = re.compile(r"music|song|album|singer|composer|concert|orchestra|opera|symphony|band\b|\bgig\b|jazz|pianist|violin|cello|choir|choral|soprano|tenor|guitar|conductor|recording|proms?\b|festival|musical\b|rapper|\bdj\b|producer|synth|record label|soundtrack|score\b|chart|single\b|tour\b|\blive\b|remix|playlist|streaming|lyrics|sonata|concerto|quartet|recital|libretto|aria|hip-hop|\brap\b|grime|folk\b|techno|house music|drum|bass|piano|flute|trumpet|saxophon|harp\b|organ\b", re.I)
MUSICAL = re.compile(r"\bmusicals?\b|west end musical|cast recording|cast album|sondheim|lloyd webber|hamilton|wicked|les mis|cabaret|operetta|gilbert and sullivan", re.I)

# notices and items that are not news
SKIP = re.compile(r"job opportunit|vacanc|sponsored|subscribe|competition: win|giveaway|win tickets|crossword|quiz|podcast:|newsletter|live blog|as it happened|horoscope|\bdeal\b|discount|black friday|prime day|best .* to buy|buying guide", re.I)


def tag(title, desc=""):
    """Tags whose keywords appear in the headline come first; a tag found only in the
    summary is used when the headline gives none. At most three."""
    head = [k for k, rx in _rx.items() if rx.search(title)]
    if head:
        return head[:3]
    return [k for k, rx in _rx.items() if rx.search(desc)][:2]


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*(The post .* appeared first on .*|Continue reading.*|Read more.*)$", "", s)
    return s


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


def fetch(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, text/html, */*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en-GB,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/"}


def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in list(root.iter("item")) + list(root.iter("{http://purl.org/rss/1.0/}item")):
        if it.tag != "item":
            q = lambda t: it.findtext("{http://purl.org/rss/1.0/}" + t)
            out.append((clean(q("title")), (q("link") or "").strip(), clean(q("description") or ""),
                        parse_date(it.findtext("dc:date", namespaces=NS))))
            continue
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        desc = clean(it.findtext("description") or "")
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


def bbc_episodes(pid, n):
    """The latest episodes of a BBC programme, from its /episodes/player page, then each
    episode page for the date and synopsis. Returns (title, url, desc, date, sounds_url)."""
    page = fetch(f"https://www.bbc.co.uk/programmes/{pid}/episodes/player", accept="text/html").decode("utf-8", "replace")
    out = []
    for m in re.finditer(r'class="programme__titles"><a href="(https://www\.bbc\.co\.uk/programmes/(\w+))"[^>]*>(.*?)</a>', page, re.S):
        url, epid, raw = m.group(1), m.group(2), m.group(3)
        title = clean(raw)
        if " — " in title:
            a, b = title.split(" — ", 1)
            title = f"{b}: {a}"
        out.append([title, url, epid])
        if len(out) >= n:
            break
    eps = []
    for title, url, epid in out:
        try:
            ep = fetch(url, accept="text/html").decode("utf-8", "replace")
        except Exception as e:
            print(f"   !! {epid}: {e}", file=sys.stderr)
            continue
        d = re.search(r'"datePublished":"([^"]+)"', ep) or re.search(r'"startDate":"([^"]+)"', ep)
        date = parse_date(d.group(1)) if d else None
        s = re.search(r'property="og:description" content="([^"]*)"', ep)
        desc = clean(html.unescape(s.group(1))) if s else ""
        eps.append((title, url, desc, date, f"https://www.bbc.co.uk/sounds/play/{epid}"))
    return eps


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=DAYS)
    stories, sources = [], []

    # ---- broadcasts
    for label, pid, station, line, n in PROGRAMMES:
        try:
            eps = bbc_episodes(pid, n)
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": "broadcast", "line": f"{station} · {line}", "ok": False, "n": 0})
            continue
        k = 0
        for title, url, desc, date, sounds in eps:
            if date and date < now - dt.timedelta(days=BDAYS):
                continue
            stories.append({
                "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:10],
                "kind": "broadcast", "src": f"{label} · {station}", "title": title, "url": sounds, "page": url,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"),
                "th": tag(title, desc) or (["AoS1 Western classical 1650–1910"] if label == "Composer of the Week" else []),
            })
            k += 1
        sources.append({"src": label, "kind": "broadcast", "line": f"{station} · {line}", "ok": True, "n": k})
        print(f"{label}: {k} episodes this week of {len(eps)} looked at")

    # ---- stories
    for label, url, line, keep, filt in FEEDS:
        try:
            got = items_from(fetch(url))
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": "story", "line": line, "ok": False, "n": 0})
            continue
        got = [g for g in got if g[0] and g[1].startswith("http")]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date in got:
            if date and date < cutoff:
                continue
            if n >= keep:
                break
            if SKIP.search(title):
                continue
            if filt == "music" and not MUSIC.search(title):
                continue
            if filt == "musical" and not MUSICAL.search(title + " " + desc):
                continue
            stories.append({
                "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:10],
                "kind": "story", "src": label, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": tag(title, desc),
            })
            n += 1
        sources.append({"src": label, "kind": "story", "line": line, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")

    seen, uniq = set(), []
    for h in sorted(stories, key=lambda h: h["date"], reverse=True):
        k = h["url"].split("?")[0]
        t = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
        if k in seen or t in seen:
            continue
        seen.add(k); seen.add(t)
        uniq.append(h)
    # newest first, but take turns between sources so one busy feed cannot fill the top of the list;
    # broadcasts first so this week's Composer of the Week is at the top of the programme
    queues = {}
    for h in uniq:
        queues.setdefault(h["src"], []).append(h)
    bcast = sorted([h for h in uniq if h["kind"] == "broadcast"], key=lambda h: h["date"], reverse=True)
    squeues = {k: v for k, v in queues.items() if v and v[0]["kind"] == "story"}
    order = sorted(squeues, key=lambda k: squeues[k][0]["date"], reverse=True)
    mixed = []
    while any(squeues.values()):
        for k in order:
            if squeues[k]:
                mixed.append(squeues[k].pop(0))
    uniq = bcast + mixed
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": uniq,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(uniq)} items")
    if len(uniq) < 10:
        sys.exit(1)


if __name__ == "__main__":
    main()
