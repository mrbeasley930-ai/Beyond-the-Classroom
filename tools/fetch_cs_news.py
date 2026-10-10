#!/usr/bin/env python3
"""Pulls computer science news feeds and writes data/cs-news.json for the Computer Science page
("The Repo": 7 · upstream, fetched every morning).

Runs in a GitHub Action every morning (see .github/workflows/cs-news.yml).
No third-party packages: urllib + xml only.

Each story is tagged to the sections of OCR A Level Computer Science (H446) by keyword.
The tags are a rough sort to help a pupil find a story that touches the topic they are on;
they are not a judgement about the story. Computerphile videos come in as kind "video";
everything else is kind "story".
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "cs-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-cs-upstream/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, level, line (what the source is), keep, filter, kind
#   level: "way in" = written for anyone; "magazine" = technology journalism; "research" = written close to the paper
#   filter: None = keep everything; "cs" = keep only stories whose headline names something from computer science (CS)
FEEDS = [
    ("BBC Technology",       "https://feeds.bbci.co.uk/news/technology/rss.xml",              "way in",   "BBC News technology desk: computing stories only", 7, "cs", "story"),
    ("Computerphile",        "https://www.youtube.com/feeds/videos.xml?channel_id=UC9-y-6csu5WGm29I7JiwpnA", "way in", "University of Nottingham academics on YouTube, one idea per video", 4, None, "video"),
    ("NCSC",                 "https://www.ncsc.gov.uk/api/1/services/v1/news-rss-feed.xml",    "magazine", "the UK's National Cyber Security Centre: warnings and advice", 4, None, "story"),
    ("The Conversation",     "https://theconversation.com/uk/technology/articles.atom",       "way in",   "academics writing for a general reader: computing stories only", 4, "cs", "story"),
    ("Quanta",               "https://www.quantamagazine.org/computer-science/feed/",         "magazine", "Quanta Magazine, computer science: theory, algorithms, AI research", 4, None, "story"),
    ("Raspberry Pi Foundation", "https://www.raspberrypi.org/blog/feed/",                     "way in",   "the education charity behind Ada Computer Science, Bebras and Astro Pi", 3, None, "story"),
    ("Ars Technica",         "https://arstechnica.com/information-technology/feed/",          "magazine", "technology magazine: computing stories only", 5, "cs", "story"),
    ("IEEE Spectrum",        "https://spectrum.ieee.org/feeds/topic/computing.rss",           "magazine", "magazine of the Institute of Electrical and Electronics Engineers, computing", 4, None, "story"),
    ("MIT News",             "https://news.mit.edu/rss/topic/computers",                      "research", "Massachusetts Institute of Technology research news, computing", 4, None, "story"),
    ("ScienceDaily",         "https://www.sciencedaily.com/rss/computers_math.xml",           "research", "university press releases, computers and maths", 4, "cs", "story"),
]

# OCR A Level Computer Science (H446) sections, by keyword (lower-case match)
THEMES = {
    "1.1 Processors and hardware": r"processor|\bcpus?\b|\bgpus?\b|\bchips?\b|semiconductor|silicon|quantum comput|qubit|\bram\b|memory|storage|\bssds?\b|hard drive|\barm\b|risc|nvidia|intel\b|\bamd\b|supercomputer|data cent(re|er)|hardware|\bpcs?\b|laptop|smartphone|iphone|transistor|moore's law|cray",
    "1.2 Software and development": r"software|operating system|windows|linux|android|\bios\b|macos|update|\bbugs?\b|patch|open.source|compiler|programming|\bcode\b|coding|python|\brust\b|javascript|\bjava\b|\bapps?\b|developer|github|debian|version control|outage|crash|glitch",
    "1.3 Networks and the web": r"network|internet|broadband|\b5g\b|\b6g\b|wi-?fi|undersea cable|\bcables?\b|protocol|\bdns\b|browser|website|\bweb\b|search engine|google search|cloud|server|\bmcp\b|certificate|tls|https|satellite internet|starlink",
    "1.3 Security and encryption": r"encrypt|cyber|hack|ransomware|breach|password|phishing|malware|spyware|vulnerab|exploit|attack|\bscam|fraud|leak|passkey|authenticat|counterfeit|incident|threat|malicious|actors?\b|targeting|state.backed",
    "1.3 Databases and data": r"database|\bdata\b|\bsql\b|dataset|records|personal information|big data",
    "1.4 / 2.3 Algorithms": r"algorithm|data structure|\bstacks?\b|\bqueues?\b|\bundo\b|race condition|concurren|recursi|\bsort|search(ing)? problem|compression|boolean|logic gate|graph|shortest path|complexity|\bproof\b|theorem|prime|maths? |mathematic|cryptograph|puzzle",
    "1.5 Law and ethics": r"\blaw\b|regulat|ofcom|online safety|privacy|gdpr|data protection|copyright|lawsuit|court|\bbans?\b|banned|surveillance|facial recognition|\bbias|ethic|deepfake|misinformation|disinformation|\bjobs?\b|workers|energy use|environment|e-waste|children|teen|social media|age (check|verification)|government|minister|accessib",
    "1.5 AI and automation": r"\bai\b|artificial intelligence|machine learning|neural|\bllms?\b|large language|chatgpt|chatbot|openai|anthropic|gemini|copilot|robot|autonomous|self-driving|agents?\b|automat",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# for general feeds: the headline has to name something from computer science
CS = re.compile(r"comput|software|algorithm|\bcode\b|coding|program|\bai\b|artificial intelligence|machine learning|neural|chatbot|\bllms?\b|robot|cyber|hack|encrypt|password|data\b|database|internet|network|\bweb\b|online|digital|\bchips?\b|processor|quantum comput|semiconductor|\bapps?\b|cloud|server|smartphone|technology|\btech\b|deepfake|social media|automat|autonomous|self-driving|\bpc\b|laptop|malware|ransomware|vulnerab|linux|windows|open.source|search engine", re.I)

# notices and items that are not news
SKIP = re.compile(r"job opportunit|vacanc|webinar:|sponsored|\bdeals?\b|discount|black friday|subscribe|podcast:|issue out now|week in review|weekly roundup|live updates|#shorts|\bsale\b|gift guide|best .* to buy|review:|^tech now$", re.I)


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
    s = (s.replace("&nbsp;", " ").replace("&#8217;", "’").replace("&#8216;", "‘").replace("&#8220;", "“")
          .replace("&#8221;", "”").replace("&#8211;", "–").replace("&#8212;", "—").replace("&amp;", "&")
          .replace("&#039;", "'").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    s = s.replace("&#160;", " ").replace("\u00a0", " ").replace("&#x27;", "'")
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*The post .*? appeared first on [^.]*\.?", "", s)
    s = re.sub(r"\s*(Continue reading.*|Read more.*|Read full article.*)$", "", s)
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


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "media": "http://search.yahoo.com/mrss/"}


def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
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
        if not desc:  # YouTube puts the description in media:group/media:description
            g = it.find("media:group", NS)
            if g is not None:
                desc = clean(g.findtext("media:description", namespaces=NS) or "")
        date = parse_date(it.findtext("atom:published", namespaces=NS) or it.findtext("atom:updated", namespaces=NS))
        out.append((title, link, desc, date))
    return out


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=10)
    vid_cutoff = now - dt.timedelta(days=45)   # Computerphile posts every week or two
    stories, sources = [], []
    for label, url, level, line, keep, filt, kind in FEEDS:
        try:
            got = items_from(fetch(url))
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "level": level, "line": line, "ok": False, "n": 0})
            continue
        got = [g for g in got if g[0] and g[1].startswith("http")]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date in got:
            if date and date < (vid_cutoff if kind == "video" else cutoff):
                continue
            if n >= keep:
                break
            if SKIP.search(title):
                continue
            if filt == "cs" and not CS.search(title):
                continue
            th = tag(title, desc)
            stories.append({
                "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:10],
                "src": label, "level": level, "kind": kind, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": th,
            })
            n += 1
        sources.append({"src": label, "level": level, "line": line, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")
    seen, uniq = set(), []
    for h in sorted(stories, key=lambda h: h["date"], reverse=True):
        k = h["url"].split("?")[0] if "youtube.com" not in h["url"] else h["url"]
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
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": mixed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(mixed)} stories")
    if len(mixed) < 10:
        sys.exit(1)


if __name__ == "__main__":
    main()
