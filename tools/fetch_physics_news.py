#!/usr/bin/env python3
"""Pulls physics news feeds and writes data/physics-news.json for the Physics lab page.

Runs in a GitHub Action every morning (see .github/workflows/physics-news.yml).
No third-party packages: urllib + xml only.

Each story is tagged to the AQA A-level Physics (7408) sections by keyword. The tags
are a rough sort to help a pupil find a story that touches the topic they are on; they
are not a judgement about the story. Untagged stories show as "Beyond the spec".
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "physics-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-physics-ticker/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, level, line (what the source is), keep, physics_only
#   level: "way in" = written for anyone; "magazine" = science journalism; "research" = written close to the paper
#   physics_only: keep only stories that hit at least one spec keyword (for general science feeds)
FEEDS = [
    ("BBC Science",        "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml", "way in",   "BBC News science and environment desk", 8, True),
    ("The Conversation",   "https://theconversation.com/uk/topics/physics-154/articles.atom", "way in", "articles by university researchers, written for anyone", 5, False),
    ("Quanta Magazine",    "https://www.quantamagazine.org/physics/feed/",                  "magazine", "Simons Foundation · long reads on new physics", 4, False),
    ("Physics World",      "https://physicsworld.com/feed/",                                "magazine", "Institute of Physics magazine", 8, False),
    ("Science News",       "https://www.sciencenews.org/topic/physics/feed",                "magazine", "Society for Science · physics desk", 5, False),
    ("CERN",               "https://home.cern/news/feed",                                   "magazine", "the particle physics lab near Geneva", 5, False),
    ("ESA",                "https://www.esa.int/rssfeed/Our_Activities/Space_Science",      "magazine", "European Space Agency · space science", 5, False),
    ("NASA",               "https://www.nasa.gov/news-release/feed/",                       "magazine", "NASA news releases", 5, True),
    ("Physics Magazine",   "https://feeds.aps.org/rss/recent/physics.xml",                 "research", "American Physical Society · short pieces on new papers", 6, False),
    ("Phys.org (physics)", "https://phys.org/rss-feed/physics-news/",                       "research", "university press releases, physics", 6, False),
    ("Phys.org (space)",   "https://phys.org/rss-feed/space-news/",                         "research", "university press releases, space and astronomy", 5, False),
]

# AQA 7408 sections, by keyword (lower-case match)
THEMES = {
    "Measurements":      r"atomic clock|\bsi unit|redefin|metrolog|uncertaint|precision measurement|most precise|kilogram|\bthe second\b",
    "Particles & quantum": r"\bquarks?\b|lepton|neutrino|\bmuons?\b|positron|antimatter|antiparticle|hadron|meson|baryon|boson|higgs|\bphotons?\b|photoelectric|collider|\blhc\b|\bcern\b|standard model|wave-particle|energy levels?|\belectrons?\b|quantum",
    "Waves & light":     r"\bwaves?\b|laser|optic|\blight\b|interferen|diffract|polari[sz]|refract|fibre|fiber|spectr|\bsound\b|acoustic|ultrasound|\blens|x-ray|microwave|infrared|ultraviolet",
    "Mechanics & materials": r"\bforces?\b|momentum|velocity|accelerat|friction|projectile|materials?\b|stress|strain|elastic|stiff|young's modulus|collision|\bdrag\b|fluid|rocket|thrust|crystal|graphene|metamaterial",
    "Electricity":       r"circuit|\bcurrent\b|voltage|resistan|resistor|semiconductor|superconduct|batter(y|ies)|transistor|electricity|electronic|solar cell|photovoltaic",
    "Further mechanics & thermal": r"oscillat|resonan|vibrat|pendulum|circular motion|rotat|\bheat|thermal|temperature|absolute zero|ultracold|cryogen|\bgas(es)?\b|entropy|thermodynamic|kelvin",
    "Fields":            r"gravit|\borbits?\b|orbital|satellite|magnet|electric field|capacitor|\bcharged?\b|electromagnet|induction|solar storm|solar wind|aurora|geomagnetic",
    "Nuclear":           r"nuclear|fusion|fission|radioactiv|isotope|reactor|half-life|\bdecay|radiation|tokamak|\biter\b|uranium|plutonium|neutrons?\b|radiocarbon",
    "Astrophysics":      r"\bstars?\b|stellar|galax|telescope|\bjwst\b|james webb|hubble|exoplanet|planet|supernova|black holes?|dark matter|dark energy|cosmolog|big bang|universe|redshift|nebula|quasar|pulsar|neutron stars?|comet|asteroid|\bmoon\b|\bmars\b|jupiter|saturn|\bsun\b|solar system|astronom",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# notices that are not news (staff circulars, schedules)
SKIP = re.compile(r"operational circular|administrative circular|\bcircular no\b|job opportunit|vacanc|press accreditation|media advisory", re.I)


def tag(text):
    return [k for k, rx in _rx.items() if rx.search(text)]


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
    cutoff = now - dt.timedelta(days=7)
    stories, sources = [], []
    for label, url, level, line, keep, physics_only in FEEDS:
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
            th = tag(f"{title}. {desc}")
            if physics_only and not th:
                continue
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
