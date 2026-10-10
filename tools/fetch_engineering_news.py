#!/usr/bin/env python3
"""Pulls engineering and motorsport-engineering news feeds and writes data/engineering-news.json
for the Engineering page ("The Garage": the pit wall screens).

Runs in a GitHub Action every morning (see .github/workflows/engineering-news.yml).
No third-party packages: urllib + xml only.

Each story is tagged to the units of the OCR Cambridge Advanced National in Engineering
(H027/H127, units F130-F137) by keyword. The tags are a rough sort to help a pupil find a
story that touches the unit they are on; they are not a judgement about the story.
Stories from the motorsport sources also carry the "Motorsport" tag, and only technical
stories are kept from them (results and gossip are dropped by keyword).
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "engineering-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-engineering-pitwall/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, level, line (what the source is), keep, filter
#   level: "way in" = written for anyone; "magazine" = engineering journalism; "research" = written close to the paper
#   filter: None = keep everything; "eng" = keep only stories whose headline names a piece of engineering (ENG);
#           "tech" = motorsport news source: keep only technical stories (TECH in the headline, NOT_TECH absent);
#           "motor" = motorsport technology source: keep everything, tag Motorsport
FEEDS = [
    ("Racecar Engineering", "https://www.racecar-engineering.com/feed/",                 "magazine", "motorsport technology magazine (some articles for subscribers)", 5, "motor"),
    ("The Race",            "https://www.the-race.com/feed/",                            "way in",   "motorsport news site: technical stories only", 4, "tech"),
    ("Motorsport.com",      "https://www.motorsport.com/rss/f1/news/",                   "way in",   "Formula 1 news: technical stories only", 4, "tech"),
    ("New Civil Engineer",  "https://www.newcivilengineer.com/feed/",                    "magazine", "civil engineering: bridges, tunnels, rail, water", 6, None),
    ("IEEE Spectrum",       "https://spectrum.ieee.org/feeds/feed.rss",                  "magazine", "magazine of the Institute of Electrical and Electronics Engineers", 5, "eng"),
    ("BBC Technology",      "https://feeds.bbci.co.uk/news/technology/rss.xml",          "way in",   "BBC News technology desk: engineering stories only", 5, "eng"),
    ("ESA",                 "https://www.esa.int/rssfeed/Enabling_Support/Space_Engineering_Technology", "magazine", "European Space Agency · space engineering and technology", 4, "eng"),
    ("NASA",                "https://www.nasa.gov/news-release/feed/",                   "magazine", "NASA news releases: engineering stories only", 3, "eng"),
    ("Carbon Brief",        "https://www.carbonbrief.org/feed",                          "magazine", "energy and climate: engineering stories only", 3, "eng"),
    ("Tech Xplore (engineering)", "https://techxplore.com/rss-feed/engineering-news/",  "research", "university press releases, engineering", 6, None),
    ("Tech Xplore (vehicles)", "https://techxplore.com/rss-feed/automotive-news/",       "research", "university press releases, vehicles and batteries", 4, None),
    ("ScienceDaily (materials)", "https://www.sciencedaily.com/rss/matter_energy/materials_science.xml", "research", "university press releases, materials science", 4, "eng"),
]

# OCR Cambridge Advanced National in Engineering units, by keyword (lower-case match)
THEMES = {
    "F130 Principles":      r"\bforces?\b|torque|momentum|velocity|accelerat|friction|\bload(s|ed|ing)?\b|efficien|thermodynamic|\bheat\b|fluid|aerodynam|downforce|\bdrag\b|pressure|stress|strain|vibrat|resonan|kinetic|wind tunnel|\bwinds?\b",
    "F131 Materials":       r"material|steel|alumin|titanium|carbon fibre|carbon fiber|composite|concrete|timber|\bwood|bamboo|polymer|plastic|ceramic|alloy|graphene|corrosion|fatigue|crack|\bglass\b|rubber|\btyres?\b|\btires?\b|recycl|metamaterial|metasurface|\bgel\b",
    "F132 In practice":     r"safety|sustainab|net zero|emission|regulation|technical rules|standards?\b|inspect|maintenance|collapse|failure|\bfail(s|ed)?\b|construction|infrastructure|bridge|tunnel|railway|\brail\b|\bdam\b|highway|airport|reactor|cost cap|ethic|hydrogen",
    "F133 CAD":             r"\bcad\b|computer-aided design|digital twin|simulat|\bcfd\b|finite element|\bfea\b|design software",
    "F134 Programmable electronics": r"microcontroller|arduino|raspberry pi|embedded|\bcode\b|coding|algorithm|\bai\b|artificial intelligence|machine learning|robot|autonomous|self-driving|sensors?\b|control system|drones?\b|\bchips?\b|processor|semiconductor|software",
    "F135 Mechanical design": r"\bengines?\b|gearbox|transmission|suspension|\bbrakes?\b|bearing|mechanism|\bgears?\b|piston|turbine|\bpumps?\b|actuator|chassis|\bwings?\b|\bfloor\b|diffuser|upgrade|prototype|product design|heat shield|thruster",
    "F136 CAM":             r"manufactur|3d.print|3-d print|printed|additive|\bcnc\b|machining|machined|factory|production line|assembly line|\bweld|\bcasting\b|forging|mass production|automation",
    "F137 Electrical":      r"batter(y|ies)|electric (car|vehicle|motor)|\bevs?\b|charging|charger|hybrid|power unit|energy recovery|\bers\b|\bmgu|\bmotors?\b|circuit|voltage|\bcurrent\b|electricity|solar|wind (farm|turbine|power)|\bgrid\b|transformer|\bcables?\b|electronic",
    "Motorsport":           r"$^",   # set by the source, not by keyword
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# for general feeds: the headline has to name a piece of engineering
ENG = re.compile(r"engineer|bridge|tunnel|railway|\btrains?\b|batter(y|ies)|robot|drones?\b|satellite|rocket|\bengines?\b|turbine|reactor|\bgrid\b|wind farm|solar|material|3d.print|3-d print|printed|factory|manufactur|\bchips?\b|semiconductor|electric (car|vehicle)|\bevs?\b|\bcars?\b|vehicle|aircraft|\bplanes?\b|\bships?\b|\bdam\b|building|construction|infrastructure|\bmotors?\b|sensors?\b|heat shield|thruster|launch|spacecraft|power station|nuclear|hydrogen|energy storage|heat pump|concrete|steel|alloy|composite|exoskeleton|prosthe|wearable|circuit|laser|lidar|autonomous|self-driving|collapse|\bgel\b|metasurface", re.I)

# a motorsport news story is kept only if its headline is about the machinery, not the racing
TECH = re.compile(r"upgrade|\baero|downforce|\bfloor|\bwings?\b|diffuser|sidepod|power ?unit|\bengines?\b|batter(y|ies)|hybrid|\benergy\b|\bers\b|\bmgu|\btyres?\b|\bbrakes?\b|cooling|suspension|gearbox|chassis|\bfuel\b|technical|regulations|wind tunnel|\bcfd\b|simulator|ride height|porpois|\bdrs\b|active aero|overtake mode|\bspec\b|engineer|car concept|\bparts?\b|software|\bdesign", re.I)
NOT_TECH = re.compile(r"results?\b|starting grid|standings|live:|as it happened|contract|signs? for|sacked|replaced by|ratings|winners and losers|preview:|stewards|penalt|yellow flag|verdict|owns up|furious|\bpole\b|pessimistic|optimistic", re.I)

# notices and items that are not news
SKIP = re.compile(r"job opportunit|vacanc|press accreditation|media advisory|webinar:|sponsored|podcast:|issue out now|subscribe|\bieee\b|framework|secures .*£|contracts?\b.*£|£.*contracts?\b|honorary fellow|changing your career|presentations at|consultation\b", re.I)


def tag(title, desc=""):
    """Tags whose keywords appear in the headline come first; a tag found only in the
    summary is used when the headline gives none. At most three."""
    head = [k for k, rx in _rx.items() if k != "Motorsport" and rx.search(title)]
    if head:
        return head[:3]
    return [k for k, rx in _rx.items() if k != "Motorsport" and rx.search(desc)][:2]


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = s.replace("&nbsp;", " ").replace("&#8217;", "’").replace("&#8216;", "‘").replace("&#8220;", "“").replace("&#8221;", "”").replace("&#8211;", "–").replace("&#8212;", "—").replace("&amp;", "&").replace("&#039;", "'").replace("&quot;", '"')
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


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
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


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=10)
    stories, sources = [], []
    for label, url, level, line, keep, filt in FEEDS:
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
            if date and date < cutoff:
                continue
            if n >= keep:
                break
            if SKIP.search(title):
                continue
            th = tag(title, desc)
            if filt == "eng" and not ENG.search(title):
                continue
            if filt == "tech" and (not TECH.search(title) or NOT_TECH.search(title)):
                continue
            if filt in ("tech", "motor"):
                th = ["Motorsport"] + th[:2]
            stories.append({
                "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:10],
                "src": label, "level": level, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": th,
            })
            n += 1
        sources.append({"src": label, "level": level, "line": line, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")
    seen, uniq = set(), []
    for h in sorted(stories, key=lambda h: h["date"], reverse=True):
        k = h["url"].split("?")[0]
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
    uniq = mixed
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": uniq,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(uniq)} stories")
    if len(uniq) < 10:
        sys.exit(1)


if __name__ == "__main__":
    main()
