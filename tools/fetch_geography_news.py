#!/usr/bin/env python3
"""Builds data/geography-news.json for the Geography page ("The Overpass": the hazard board).

Runs in a GitHub Action every morning (see .github/workflows/geography-news.yml).
No third-party packages: urllib, json and xml only.

Two lists come out:
  events  - hazards happening now, with a position, for the mission map and the hazard board:
            earthquakes of magnitude 5 and over from the USGS (past week, plus the month's
            "significant" list), open natural events from NASA EONET (volcanoes, storms,
            wildfires, floods, ice, drought, landslides), and orange and red alerts from GDACS
            (with green alerts for tropical cyclones, floods and droughts).
  stories - this week's geography news from nine sources.
Every item is tagged by keyword to the Edexcel A level Geography (9GE0) topics. The tags are a
rough sort to help a pupil find something on the topic they are studying; they are not a
judgement about the item, and some will be wrong.
"""
import datetime as dt
import email.utils
import gzip
import hashlib
import json
import pathlib
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "geography-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-geography-overpass/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

T1 = "Topic 1 Tectonic processes and hazards"
T2A = "Topic 2A Glaciated landscapes and change"
T2B = "Topic 2B Coastal landscapes and change"
T3 = "Topic 3 Globalisation"
T4A = "Topic 4A Regenerating places"
T4B = "Topic 4B Diverse places"
T5 = "Topic 5 The water cycle and water insecurity"
T6 = "Topic 6 The carbon cycle and energy security"
T7 = "Topic 7 Superpowers"
T8A = "Topic 8A Health, human rights and intervention"
T8B = "Topic 8B Migration, identity and sovereignty"

THEMES = {
    T1:  r"earthquake|\bquakes?\b|tsunami|volcan|erupt|\blava\b|magma|tectonic|\bfault\b|seismic|landslide|aftershock",
    T2A: r"glacier|glacial|ice sheet|ice cap|ice shelf|permafrost|periglacial|antarctic|greenland|\bthaw|meltwater|iceberg",
    T2B: r"\bcoast|erosion|sea.level|\bcliffs?\b|\bbeach|storm surge|mangrove|\bdeltas?\b|estuar|saltmarsh|shoreline|sea wall|\breefs?\b",
    T3:  r"globali[sz]|\btrade\b|tariff|supply chain|shipping|multinational|\bexports?\b|\bimports?\b|tourism|tourist|outsourc|\binvestment\b|\bports?\b|offshor",
    T4A: r"regenerat|\bhousing\b|high street|town centre|\burban\b|\bcity\b|\bcities\b|\brural\b|deprivation|levelling up|gentrif|\bplanning\b|new towns?|brownfield|green belt",
    T4B: r"\bcommunit|demograph|\bcensus\b|ethnic|\bdiverse\b|diversity|segregat|belonging|\bheritage\b",
    T5:  r"drought|\bflood|rainfall|\brivers?\b|water (supply|scarcity|security|shortage|stress)|aquifer|groundwater|reservoir|monsoon|hydrolog|\bdams?\b|el ni[ñn]o|la ni[ñn]a|sewage|heatwave|cyclone|hurricane|typhoon|\bstorms?\b",
    T6:  r"carbon|emission|amazon|\bforests?\b|\bco2\b|net.zero|sea ice|arctic|fossil|\bcoal\b|\boil\b|\bgas\b|renewable|\bsolar\b|wind (farm|power|turbine)|\benergy\b|deforest|rainforest|\bpeat|ocean acid|climate|wildfire|\bcop3\d|global warming|methane",
    T7:  r"superpower|geopolit|\bchina\b|chinese|beijing|united states|washington|white house|\btrump\b|russia|kremlin|\bnato\b|\bg7\b|\bg20\b|\bbrics\b|sanction|south china sea|\bimf\b|world bank|\bun security council",
    T8A: r"\bhealth|disease|malaria|cholera|pandemic|epidemic|vaccin|life expectancy|human rights|\baid\b|humanitarian|intervention|famine|hunger|malnutrition|poverty|\bsdgs?\b|development goals|mortality",
    T8B: r"migra|refugee|asylum|\bborders?\b|sovereign|displac|diaspora|\bidentity\b|nationalis|citizenship|deport|small boats",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}


def tag(title, desc=""):
    """Topics whose keywords appear in the headline come first; a topic found only in the
    summary is used when the headline gives none. At most three."""
    head = [k for k, rx in _rx.items() if rx.search(title)]
    if head:
        return head[:3]
    return [k for k, rx in _rx.items() if rx.search(desc)][:2]


# ---------------------------------------------------------------- stories
# label, url, level, line, keep
#   level: "way in" = written for anyone; "magazine" = specialist journalism; "research" = written by researchers
FEEDS = [
    ("Carbon Brief",            "https://www.carbonbrief.org/feed",                                    "magazine", "climate science and energy policy", 4),
    ("BBC Science & Environment", "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",     "way in",   "BBC News", 6),
    ("Guardian Environment",    "https://www.theguardian.com/environment/rss",                         "way in",   "the Guardian's environment desk", 5),
    ("Guardian Global Development", "https://www.theguardian.com/global-development/rss",              "way in",   "the Guardian's global development desk", 5),
    ("Geographical",            "https://geographical.co.uk/feed",                                     "magazine", "the Royal Geographical Society's magazine", 4),
    ("NASA Earth Observatory",  "https://earthobservatory.nasa.gov/feeds/image-of-the-day.rss",        "magazine", "a satellite image of the day, explained", 4),
    ("ESA Observing the Earth", "https://www.esa.int/rssfeed/Applications/Observing_the_Earth",         "magazine", "European Space Agency Earth observation", 3),
    ("The Conversation",        "https://theconversation.com/uk/environment/articles.atom",            "research", "written by university researchers", 4),
    ("UN News (migration)",     "https://news.un.org/feed/subscribe/en/news/topic/migrants-and-refugees/feed/rss.xml", "way in", "United Nations news on migrants and refugees", 3),
]

# notices, live blogs and items that are not news
# the NASA science feed carries space stories as well as Earth images
SPACE = re.compile(r"\bmars\b|curiosity|perseverance|artist.s concept|spacecraft|\blunar\b|\bmoon\b|galax|telescope|\bwebb\b|hubble|asteroid|comet|exoplanet|\bsun\b|solar wind", re.I)
SKIP = re.compile(r"\blive\b.*(updates|blog)|as it happened|quiz of the week|crossword|podcast:|newsletter|subscribe|sponsored|webinar|in pictures|photo of the day|week in wildlife|\bcorrection\b", re.I)


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    for a, b in (("&nbsp;", " "), ("&#8217;", "’"), ("&#8216;", "‘"), ("&#8220;", "“"), ("&#8221;", "”"), ("&#8211;", "–"), ("&#8212;", "—"), ("&amp;", "&"), ("&#039;", "'"), ("&#39;", "'"), ("&quot;", '"')):
        s = s.replace(a, b)
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


def fetch(url, accept="application/rss+xml, application/atom+xml, application/xml, text/xml, */*"):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": accept})
    with urllib.request.urlopen(req, timeout=30) as r:
        b = r.read()
    if b[:2] == b"\x1f\x8b":                 # some servers send gzip whatever we ask for
        b = gzip.decompress(b)
    return b


NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "geo": "http://www.w3.org/2003/01/geo/wgs84_pos#", "gdacs": "http://www.gdacs.org"}


def items_from(xml_bytes):
    # repair two faults seen in real feeds: attributes run together, and bare ampersands
    xml_bytes = re.sub(rb'"(xmlns:)', rb'" \1', xml_bytes)
    xml_bytes = re.sub(rb"&(?!#?[A-Za-z0-9]+;)", b"&amp;", xml_bytes)
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        out.append((clean(it.findtext("title")), (it.findtext("link") or "").strip(),
                    clean(it.findtext("description") or ""),
                    parse_date(it.findtext("pubDate") or it.findtext("dc:date", namespaces=NS)), it))
    for it in root.iter("{http://www.w3.org/2005/Atom}entry"):
        link = ""
        for l in it.findall("atom:link", NS):
            if l.get("rel", "alternate") == "alternate":
                link = l.get("href", "")
                break
        desc = clean(it.findtext("atom:summary", namespaces=NS) or it.findtext("atom:content", namespaces=NS) or "")
        date = parse_date(it.findtext("atom:published", namespaces=NS) or it.findtext("atom:updated", namespaces=NS))
        out.append((clean(it.findtext("atom:title", namespaces=NS)), link, desc, date, it))
    return out


def hid(s):
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


def get_stories(now, sources):
    cutoff = now - dt.timedelta(days=12)
    stories = []
    for label, url, level, line, keep in FEEDS:
        try:
            got = items_from(fetch(url))
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": "story", "level": level, "line": line, "ok": False, "n": 0})
            continue
        got = [g for g in got if g[0] and g[1].startswith("http")]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date, _ in got:
            if n >= keep:
                break
            if (date and date < cutoff) or SKIP.search(title) or SPACE.search(title):
                continue
            th = tag(title, desc)
            # the two general news desks: keep only stories that touch the course
            if label.startswith(("BBC", "Guardian")) and not th:
                continue
            stories.append({"id": hid(link), "src": label, "level": level, "title": title, "url": link,
                            "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                            "date": (date or now).isoformat(timespec="minutes"), "th": th})
            n += 1
        sources.append({"src": label, "kind": "story", "level": level, "line": line, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")
    seen, uniq = set(), []
    words = lambda t: {w for w in re.findall(r"[a-z]{5,}", t.lower())}
    for h in sorted(stories, key=lambda h: h["date"], reverse=True):
        k = h["url"].split("?")[0]
        t = re.sub(r"[^a-z0-9]", "", h["title"].lower())[:60]
        if k in seen or t in seen:
            continue
        w = words(h["title"])
        if any(u["src"] == h["src"] and len(w & words(u["title"])) >= 2 for u in uniq):
            continue                       # the same story again from the same desk
        seen.add(k); seen.add(t)
        uniq.append(h)
    # newest first, taking turns between sources so one busy feed cannot fill the top of the list
    queues = {}
    for h in uniq:
        queues.setdefault(h["src"], []).append(h)
    order = sorted(queues, key=lambda k: queues[k][0]["date"], reverse=True)
    mixed = []
    while any(queues.values()):
        for k in order:
            if queues[k]:
                mixed.append(queues[k].pop(0))
    return mixed


# ---------------------------------------------------------------- hazards
KIND_TH = {"quake": [T1], "volcano": [T1], "landslide": [T1], "storm": [T5], "flood": [T5],
           "drought": [T5], "wildfire": [T6], "ice": [T2A, T6], "other": []}


def iso(ms_or_str):
    if isinstance(ms_or_str, (int, float)):
        return dt.datetime.fromtimestamp(ms_or_str / 1000, dt.timezone.utc).isoformat(timespec="minutes")
    d = parse_date(ms_or_str)
    return d.isoformat(timespec="minutes") if d else ""


def get_usgs(now, sources):
    evs = {}
    for label, url in (("USGS (magnitude 4.5+, past week)", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson"),
                       ("USGS (significant, past month)", "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson")):
        try:
            j = json.loads(fetch(url, "application/geo+json, application/json"))
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": "hazard", "level": "data", "line": "US Geological Survey earthquake catalogue", "ok": False, "n": 0})
            continue
        n = 0
        for f in j.get("features", []):
            p = f["properties"]; lon, lat, depth = (f["geometry"]["coordinates"] + [0, 0, 0])[:3]
            mag = p.get("mag") or 0
            if mag < 5.0 and not p.get("alert") and "significant" not in label:
                continue
            if f["id"] in evs:
                continue
            evs[f["id"]] = {"id": "q" + f["id"], "kind": "quake", "src": "USGS", "title": f"Magnitude {mag:.1f} earthquake",
                            "place": p.get("place") or "", "lat": round(lat, 2), "lon": round(lon, 2),
                            "date": iso(p.get("time")), "mag": round(mag, 1), "depth": round(depth or 0),
                            "alert": p.get("alert") or "", "tsunami": bool(p.get("tsunami")), "url": p.get("url") or "",
                            "th": KIND_TH["quake"]}
            n += 1
        sources.append({"src": label, "kind": "hazard", "level": "data", "line": "US Geological Survey earthquake catalogue", "ok": True, "n": n})
        print(f"{label}: {n}")
    quakes, per = [], {}
    for e in sorted(evs.values(), key=lambda e: e["mag"], reverse=True):
        region = re.split(r",\s*", e["place"])[-1].replace(" region", "").strip().lower() or "?"
        if per.get(region, 0) >= 4:
            continue
        per[region] = per.get(region, 0) + 1
        e["region"] = region
        quakes.append(e)
    return quakes[:24]


EONET_KIND = {"volcanoes": "volcano", "severeStorms": "storm", "wildfires": "wildfire", "floods": "flood",
              "seaLakeIce": "ice", "drought": "drought", "landslides": "landslide", "earthquakes": "quake",
              "snow": "other", "dustHaze": "other", "tempExtremes": "other", "waterColor": "other", "manmade": "other"}
EONET_CAP = {"wildfire": 8, "ice": 3, "other": 3, "flood": 8, "storm": 12, "volcano": 14, "drought": 4, "landslide": 4, "quake": 0}


def get_eonet(now, sources):
    label = "NASA EONET"
    try:
        j = json.loads(fetch("https://eonet.gsfc.nasa.gov/api/v3/events?status=open&days=21", "application/json"))
    except Exception as e:
        print(f"!! {label}: {e}", file=sys.stderr)
        sources.append({"src": label, "kind": "hazard", "level": "data", "line": "NASA Earth Observatory Natural Event Tracker", "ok": False, "n": 0})
        return []
    out, per = [], {}
    for ev in sorted(j.get("events", []), key=lambda e: (e.get("geometry") or [{}])[-1].get("date", ""), reverse=True):
        cats = [c["id"] for c in ev.get("categories", [])]
        kind = EONET_KIND.get(cats[0] if cats else "", "other")
        if per.get(kind, 0) >= EONET_CAP.get(kind, 3):
            continue
        geo = (ev.get("geometry") or [])
        if not geo:
            continue
        g = geo[-1]; c = g.get("coordinates")
        if g.get("type") == "Polygon":
            c = c[0][0]
        try:
            lon, lat = float(c[0]), float(c[1])
        except Exception:
            continue
        src = (ev.get("sources") or [{}])[0]
        url = src.get("url") or ev.get("link") or ""
        title = clean(ev.get("title", ""))
        if re.match(r"prescribed (fire|burn)|\brx\b", title, re.I):
            continue                       # planned burns are not hazards
        out.append({"id": "eo" + hid(ev["id"]), "kind": kind, "src": "NASA EONET" + (f" · {src.get('id')}" if src.get("id") else ""),
                    "title": title, "place": "", "lat": round(lat, 2), "lon": round(lon, 2),
                    "date": iso(g.get("date", "")), "first": iso(geo[0].get("date", "")), "url": url,
                    "th": KIND_TH.get(kind, [])})
        per[kind] = per.get(kind, 0) + 1
    sources.append({"src": label, "kind": "hazard", "level": "data", "line": "NASA Earth Observatory Natural Event Tracker", "ok": True, "n": len(out)})
    print(f"{label}: {len(out)}")
    return out


GDACS_KIND = {"TC": "storm", "FL": "flood", "VO": "volcano", "DR": "drought", "WF": "wildfire", "EQ": "quake"}


def get_gdacs(now, sources):
    label = "GDACS"
    try:
        got = items_from(fetch("https://www.gdacs.org/xml/rss.xml"))
    except Exception as e:
        print(f"!! {label}: {e}", file=sys.stderr)
        sources.append({"src": label, "kind": "hazard", "level": "data", "line": "Global Disaster Alert and Coordination System (UN and European Commission)", "ok": False, "n": 0})
        return []
    out = []
    cutoff = now - dt.timedelta(days=21)
    for title, link, desc, date, it in got:
        et = (it.findtext("gdacs:eventtype", namespaces=NS) or "").strip()
        lvl = (it.findtext("gdacs:alertlevel", namespaces=NS) or "").strip().lower()
        if et == "EQ" or et not in GDACS_KIND:
            continue                       # earthquakes come from the USGS
        if lvl not in ("orange", "red") and et not in ("TC", "FL", "DR"):
            continue
        if date and date < cutoff and lvl == "green":
            continue
        try:
            lat = float(it.findtext("geo:Point/geo:lat", namespaces=NS) or it.findtext("geo:lat", namespaces=NS))
            lon = float(it.findtext("geo:Point/geo:long", namespaces=NS) or it.findtext("geo:long", namespaces=NS))
        except Exception:
            continue
        country = clean(it.findtext("gdacs:country", namespaces=NS) or "")
        cs = [c.strip() for c in country.split(",") if c.strip()]
        if len(cs) > 4:
            country = ", ".join(cs[:4]) + f" and {len(cs) - 4} more"
        kind = GDACS_KIND[et]
        title = gdacs_title(et, title, country)
        out.append({"id": "gd" + hid(link or title), "kind": kind, "src": "GDACS", "title": title, "place": country,
                    "lat": round(lat, 2), "lon": round(lon, 2), "date": (date or now).isoformat(timespec="minutes"),
                    "alert": lvl, "url": link, "desc": (desc[:240] + "…") if len(desc) > 240 else desc, "th": KIND_TH[kind]})
    out = sorted(out, key=lambda e: ({"red": 0, "orange": 1}.get(e["alert"], 2), e["date"]))[:16]
    sources.append({"src": label, "kind": "hazard", "level": "data", "line": "Global Disaster Alert and Coordination System (UN and European Commission)", "ok": True, "n": len(out)})
    print(f"{label}: {len(out)}")
    return out


def gdacs_title(et, title, country):
    """GDACS headlines are notices ("Red notification for tropical cyclone SIMON-26. Population
    affected by..."); turn them into plain names. The alert colour is kept separately."""
    where = country or "an unnamed area"
    if et == "TC":
        m = re.search(r"cyclone\s+([A-Z][A-Z\-]*?)-\d\d", title)
        return f"Tropical cyclone {m.group(1).title()}" if m else "Tropical cyclone"
    if et == "VO":
        m = re.search(r"for (.+?) in (.+?)\.?$", title)
        return f"{m.group(1)} volcano, {m.group(2)}: eruption" if m else title
    return {"FL": "Flooding in ", "DR": "Drought in ", "WF": "Wildfire in "}.get(et, "") + where


def storm_name(e):
    m = re.search(r"(?:storm|cyclone|typhoon|hurricane|depression)\s+([A-Z][A-Za-z\-]*[A-Za-z])", e["title"], re.I)
    return m.group(1).lower() if m else None


def main():
    now = dt.datetime.now(dt.timezone.utc)
    sources = []
    quakes = get_usgs(now, sources)
    eonet = get_eonet(now, sources)
    gdacs = get_gdacs(now, sources)
    # a storm that EONET and GDACS both carry appears once (the GDACS one, which has an alert level)
    # use EONET's name for it ("Typhoon Koguma") and GDACS's alert, country and report page
    gd = {storm_name(e): e for e in gdacs if e["kind"] == "storm" and storm_name(e)}
    keep = []
    for e in eonet:
        g = gd.get(storm_name(e)) if e["kind"] == "storm" else None
        if g:
            g["title"] = e["title"]
            g["date"] = max(g["date"], e["date"])
            g["src"] = "GDACS and NASA EONET"
        else:
            keep.append(e)
    eonet = keep
    events = sorted(quakes + eonet + gdacs, key=lambda e: e["date"], reverse=True)
    stories = get_stories(now, sources)
    data = {"made": now.isoformat(timespec="minutes"), "themes": list(THEMES.keys()),
            "sources": sources, "events": events, "stories": stories}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT}: {len(events)} events, {len(stories)} stories")
    if len(stories) < 8 and len(events) < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
