#!/usr/bin/env python3
"""Builds data/classics-news.json for the Classical Civilisation page ("The Cave": the Mouth of the Cave).

One list, `stories`, of three kinds:
  story     articles from public RSS feeds (Antigone, Sententiae Antiquae, Archaeology magazine, the Greek
            Archaeology Newsroom, the Guardian's classics and archaeology desks, The Conversation, Phys.org,
            Smithsonian) — the general feeds are kept only when the headline names something Greek or Roman
  line      a Sententiae Antiquae post that quotes Greek (a passage with a translation)
  podcast   new episodes of Natalie Haynes Stands Up for the Classics and In Our Time (read from the BBC
            programme pages, since the BBC podcast RSS feeds have been stale since 2024), The Rest Is History
            (ancient episodes only), The Ancients and The Partial Historians

Runs in a GitHub Action every morning (see .github/workflows/classics-news.yml).
No third-party packages: urllib + xml + re only.

Every item is tagged to the parts of OCR A Level Classical Civilisation (H408) the department teaches,
by keyword. The tags are a rough sort to help a pupil find something that touches the part they are on;
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "classics-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-cave/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# BBC programmes read from their /episodes/player pages: label, pid, line, how many of the latest episodes to look at, filter, days
#   days = how far back an episode may be (all five Haynes series stay on Sounds, so the latest four are kept whatever their date)
PROGRAMMES = [
    ("Natalie Haynes Stands Up for the Classics", "b077x8pc", "Radio 4 · a comedian who read Classics, one ancient writer or figure per half hour, with an academic guest", 4, None, 3650),
    ("In Our Time", "b006qykl", "Radio 4 · Melvyn Bragg and three academics for 45 minutes: classical episodes only", 30, "ancient", 120),
]

# label, feed url, kind, line (what the source is), keep, filter, days
#   filter: None = keep everything; "ancient" = keep only items whose headline names something Greek or Roman
#   days: how far back to look (Antigone publishes about weekly, so it gets a longer window)
FEEDS = [
    ("Antigone",               "https://antigonejournal.com/feed/",                              "story",   "an open online journal of classics, edited by classicists, written for anyone", 4, None, 60),
    ("Sententiae Antiquae",    "https://sententiaeantiquae.com/feed/",                           "story",   "two Homer scholars post a passage of Greek or Latin with a translation most days, and longer pieces on Homer", 6, None, 14),
    ("Archaeology magazine",   "https://www.archaeology.org/feed",                               "story",   "the Archaeological Institute of America's magazine: Greek and Roman stories only", 5, "ancient", 14),
    ("Archaeology Newsroom",   "https://www.archaeology.wiki/feed/",                             "story",   "Greek archaeology news site (Archaeology & Arts, Athens), in English: Greek and Roman stories only", 4, "ancient", 14),
    ("Guardian · classics",    "https://www.theguardian.com/books/classics/rss",                 "story",   "the Guardian's classics books tag: ancient-world pieces only", 4, "ancient", 45),
    ("Guardian · archaeology", "https://www.theguardian.com/science/archaeology/rss",            "story",   "the Guardian's archaeology desk: Greek and Roman stories only", 4, "ancient", 21),
    ("The Conversation",       "https://theconversation.com/uk/arts/articles.atom",              "story",   "academics writing for a general reader: ancient-world pieces only", 4, "ancient", 21),
    ("Phys.org · archaeology", "https://phys.org/rss-feed/science-news/archaeology-fossils/",    "story",   "science news wire: Greek and Roman archaeology only", 3, "ancient", 14),
    ("Smithsonian · history",  "https://www.smithsonianmag.com/rss/history/",                    "story",   "Smithsonian magazine's history desk: ancient-world pieces only", 3, "ancient", 21),
    ("The Rest Is History",    "https://feeds.megaphone.fm/GLT4787413333",                       "podcast", "Tom Holland and Dominic Sandbrook: ancient episodes only (entertaining, opinionated, some strong language)", 3, "ancient", 90),
    ("The Ancients",           "https://access.acast.com/rss/f2925f7a-eb08-471a-9958-387cb5ee6353", "podcast", "History Hit: one scholar, one ancient subject per episode: Greek and Roman episodes only", 3, "ancient", 45),
    ("The Partial Historians", "https://partialhistorians.com/feed/",                            "podcast", "two academics telling Roman history in order, source by source", 2, None, 45),
]

# OCR H408 as the department teaches it, by keyword (lower-case match). Order matters: first match is the main tag.
THEMES = {
    "WH Odyssey":               r"odyss|odysseus|ulysses|penelope|telemach|ithaca|ithaka|cyclops|polyphem|\bcirce\b|calypso|siren|nausica|phaeacia|demodoc|eumaeus|suitors",
    "WH Aeneid":                r"aeneid|aeneas|virgil|vergil|\bdido\b|turnus|carthage|anchises|lavinia|camilla|latinus|augustus|augustan|\bjuno\b",
    "WH Homeric and Augustan world": r"\biliad\b|achilles|hector|\btroy\b|trojan|mycenae|mycenaean|bronze age|\bhomer|homeric|\bepic\b|kleos|xenia|rhapsod|\bbard\b|hero\b|heroic|heroes",
    "LR Sappho":                r"sappho|lesbos|lesbian poet|lyric poet|lyric poetry|\bfragment 31|fr\. 31",
    "LR Plato":                 r"\bplato\b|platonic|symposium|socrates|socratic|diotima|alcibiades|\beros\b|the republic|republic\b",
    "LR Ovid":                  r"\bovid\b|amores|ars amatoria|art of love|elegy|elegiac|corinna|metamorphoses",
    "LR Seneca":                r"seneca|stoic|stoicism|lucilius|epictetus|marcus aurelius",
    "LR Greek and Roman society": r"marriage|wedding|\bwomen\b|\bwife\b|\bwives\b|husband|household|oikos|sexualit|\bgender\b|hetaira|courtesan|adulter|pederast|\blove\b|desire|erotic|roman family|dowry|concubine|brothel|same-sex|homosexual",
    "IB Herodotus":             r"herodotus|persian wars|marathon|thermopylae|salamis|plataea|xerxes|darius|croesus|scythian|leonidas|three hundred",
    "IB Aeschylus Persians":    r"aeschylus|\bpersians\b|persae|greek tragedy|tragedian|dionysia|tragedy",
    "IB Greeks and others":     r"barbarian|persia\b|persian|achaemenid|amazon|scythia|ethnic|foreigner|xenophob|otherness|the other\b|centaur|amazonomach|gigantomach|egyptian.*greek|greek.*egypt|identity",
    "GA Sculpture":             r"sculpt|statue|kouros|kouroi|\bkore\b|korai|\bbronze\b|\bmarble\b|riace|doryphoros|polykleitos|polyclitus|praxiteles|lysippos|lysippus|phidias|pheidias|discobolus|discus thrower|myron|aphrodite of knidos|kritios|elgin|parthenon marbles|parthenon sculptures",
    "GA Vase painting":         r"\bvase|pottery|amphora|krater|kylix|black-figure|red-figure|black figure|red figure|exekias|euphronios|beazley|ceramic|potter\b|painter\b",
    "GA Architecture":          r"\btemple\b|parthenon|acropolis|\bdoric\b|\bionic\b|corinthian|\bcolumn|frieze|pediment|metope|architect|erechtheion|erechtheum|caryatid|olympia|delphi|bassae|propylaea",
    "Classical skills":         r"translat|manuscript|papyr|epigraph|inscription|philolog|textual|lexicon|dictionar|oxyrhynchus|scroll|herculaneum papyri|ancient greek language|learn latin|learn greek|grammar",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

# for general feeds: the headline has to name something Greek or Roman
ANCIENT = re.compile(r"\bgreek|\bgreece|\broman\b|\bromans\b|\brome\b|\blatin\b|\bhomer|odyss|\biliad|virgil|vergil|aeneid|\bovid\b|sappho|\bplato\b|socrates|aristotle|seneca|cicero|herodotus|thucydides|aeschylus|sophocles|euripides|aristophanes|athens|athenian|sparta|spartan|\btroy\b|trojan|pompeii|herculaneum|vesuvius|parthenon|acropolis|delphi|olympia|mycenae|minoan|etruscan|augustus|caesar|\bnero\b|hadrian|classics\b|classicist|classical antiquity|antiquity|ancient world|ancient greek|ancient roman|archaeologists? (find|discover|unearth)|mosaic|amphora|papyr|hellenistic|ptolem|cleopatra|carthage|hannibal|alexander the great|byzantine|gladiator|legion|roman villa|roman road|roman bath|roman fort|hadrian's wall|vindolanda|persian empire|achaemenid|xerxes|darius|zeus|apollo|athena|aphrodite|dionysus|hercules|heracles|achilles|hector|penelope|odysseus|aeneas|\bmyth", re.I)

# notices and items that are not news
SKIP = re.compile(r"job opportunit|vacanc|postdoc|fellowship|phd position|studentship|call for papers|sponsored|subscribe|giveaway|win tickets|crossword|quiz\b|newsletter|live blog|as it happened|horoscope|\bdeal\b|discount|black friday|prime day|best .* to buy|buying guide|programme announcement|conference programme", re.I)
GREEK = re.compile(r"[Ͱ-Ͽἀ-῿]")


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


NS = {"atom": "http://www.w3.org/2005/Atom", "dc": "http://purl.org/dc/elements/1.1/",
      "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd"}


def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not link.startswith("http"):  # podcast items often have no link: use the guid if it is a page
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
    stories, sources = [], []

    # ---- BBC programmes
    for label, pid, line, n, filt, days in PROGRAMMES:
        try:
            eps = bbc_episodes(pid, n)
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": "podcast", "line": line, "ok": False, "n": 0})
            continue
        k = 0
        for title, url, desc, date, sounds in eps:
            if date and date < now - dt.timedelta(days=days):
                continue
            if filt == "ancient" and not ANCIENT.search(title):
                continue
            stories.append({
                "id": hashlib.sha1(url.encode("utf-8")).hexdigest()[:10],
                "kind": "podcast", "src": label, "title": title, "url": sounds, "page": url,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"),
                "th": tag(title, desc),
            })
            k += 1
        sources.append({"src": label, "kind": "podcast", "line": line, "ok": True, "n": k})
        print(f"{label}: {k} episodes kept of {len(eps)} looked at")

    # ---- feeds
    for label, url, kind, line, keep, filt, days in FEEDS:
        try:
            raw = fetch(url)
            got = items_from(raw)
            home = podcast_home(raw) if kind == "podcast" else ""
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": kind, "line": line, "ok": False, "n": 0})
            continue
        got = [(t, (l if l.startswith("http") else home), d, dd) for t, l, d, dd in got if t]
        got = [g for g in got if g[1].startswith("http")]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date in got:
            if date and date < now - dt.timedelta(days=days):
                continue
            if n >= keep:
                break
            if SKIP.search(title):
                continue
            if filt == "ancient" and not ANCIENT.search(title):
                continue
            k = kind
            if label == "Sententiae Antiquae" and GREEK.search(desc):
                k = "line"
            stories.append({
                "id": hashlib.sha1((link if kind == "story" else link + title).encode("utf-8")).hexdigest()[:10],
                "kind": k, "src": label, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": tag(title, desc),
            })
            n += 1
        sources.append({"src": label, "kind": kind, "line": line, "ok": True, "n": n})
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
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": mixed,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(mixed)} items")
    if len(mixed) < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
