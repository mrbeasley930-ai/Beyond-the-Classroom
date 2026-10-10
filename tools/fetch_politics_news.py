#!/usr/bin/env python3
"""Pulls politics feeds and the latest Commons divisions, and writes data/politics-news.json
for the Politics page ("The Division Lobby": the Order Paper, new every morning).

Runs in a GitHub Action every morning (see .github/workflows/politics-news.yml).
No third-party packages: urllib + xml + json only.

Three things arrive:
  stories    UK and US politics articles from the BBC, the Guardian, The Conversation, the Commons Library,
             the Constitution Unit, the Institute for Government, UK in a Changing Europe, the LSE and the
             Electoral Reform Society; NPR, the Guardian's US desk and SCOTUSblog   (kind "story", seven days deep)
  podcasts   new episodes of politics podcasts, most of them on the department's own enrichment list
                                                                                    (kind "podcast", two weeks deep)
  divisions  the most recent recorded votes in the House of Commons, from Parliament's Commons Votes API,
             with the Aye/No totals and how each party split. Pupils vote before they see the result.
Everything is tagged by keyword to AQA A-level Politics (7152) as the department teaches it: Paper 1 (UK),
Paper 2 (USA and comparative) and Paper 3 (political ideas). UK sources only take Paper 1 tags and US sources
only Paper 2 tags; Paper 3 can land on either. The tags are a rough sort to help a pupil find something near
their topic; they are not a judgement about the story.
"""
import datetime as dt
import email.utils
import hashlib
import json
import pathlib
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "politics-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-politics-orderpaper/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, level, line (what the source is), keep, side (uk / us / any), kind
FEEDS = [
    ("BBC News: Politics",        "https://feeds.bbci.co.uk/news/politics/rss.xml",                 "news",     "the BBC's politics desk", 5, "uk", "story"),
    ("The Guardian: Politics",    "https://www.theguardian.com/politics/rss",                       "news",     "the Guardian's politics desk (a left-of-centre paper; read it alongside the Spectator podcast)", 3, "uk", "story"),
    ("The Conversation",          "https://theconversation.com/uk/politics/articles.atom",          "academic", "academics writing for a general reader: UK politics", 4, "uk", "story"),
    ("Commons Library",           "https://commonslibrary.parliament.uk/feed/",                     "briefing", "the House of Commons Library: the research briefings MPs read", 3, "uk", "story"),
    ("Constitution Unit",         "https://constitution-unit.com/feed/",                            "academic", "UCL's Constitution Unit blog: Parliament, elections, the constitution", 2, "uk", "story"),
    ("Institute for Government",  "https://www.instituteforgovernment.org.uk/rss.xml",             "think tank", "the IfG: how government works, with the figures", 3, "uk", "story"),
    ("UK in a Changing Europe",   "https://ukandeu.ac.uk/feed/",                                    "academic", "impartial research on UK–EU relations and British politics", 3, "uk", "story"),
    ("LSE British Politics",      "https://blogs.lse.ac.uk/politicsandpolicy/feed/",                "academic", "the LSE's British Politics and Policy blog", 2, "uk", "story"),
    ("Electoral Reform Society",  "https://www.electoral-reform.org.uk/feed/",                      "campaign", "a campaign group for proportional representation: it argues a case", 2, "uk", "story"),
    ("NPR: Politics",             "https://feeds.npr.org/1014/rss.xml",                             "news",     "NPR's politics desk, Washington", 4, "us", "story"),
    ("The Guardian: US politics", "https://www.theguardian.com/us-news/us-politics/rss",            "news",     "the Guardian's US politics desk", 3, "us", "story"),
    ("SCOTUSblog",                "https://www.scotusblog.com/feed/",                               "specialist", "independent reporting on the US Supreme Court", 3, "us", "story"),
    ("The Conversation (US)",     "https://theconversation.com/us/politics/articles.atom",          "academic", "academics writing for a general reader: US politics", 3, "us", "story"),
    ("Parliament Matters",        "https://feeds.acast.com/public/shows/6537a8ac217b660012c59633",  "podcast",  "the Hansard Society's weekly podcast on what Parliament did and why", 2, "uk", "podcast"),
    ("The News Agents",           "https://feeds.captivate.fm/the-news-agents/",                    "podcast",  "Emily Maitlis, Jon Sopel and Lewis Goodall (on the department's list)", 3, "uk", "podcast"),
    ("The News Agents USA",       "https://feeds.captivate.fm/the-news-agents-usa-podcast/",        "podcast",  "the US edition (on the department's list)", 2, "us", "podcast"),
    ("The Rest Is Politics",      "https://feeds.megaphone.fm/GLT9190936013",                       "podcast",  "Alastair Campbell and Rory Stewart: two former insiders, one from each side; treat it as opinion", 2, "uk", "podcast"),
    ("Coffee House Shots",        "https://access.acast.com/rss/68359028e1abc4be6b032cd1/default",  "podcast",  "the Spectator's daily politics podcast (a right-of-centre magazine; read it alongside the Guardian)", 2, "uk", "podcast"),
    ("Inside Briefing",           "https://feeds.megaphone.fm/PMO8430796457",                       "podcast",  "the Institute for Government on how government works", 2, "uk", "podcast"),
    ("Westminster Insider",       "https://feeds.megaphone.fm/ASD2104948920",                       "podcast",  "Politico's long-form Westminster podcast (on the department's list)", 1, "uk", "podcast"),
    ("Politics Weekly UK",        "https://www.theguardian.com/politics/series/politicsweekly/podcast.xml", "podcast", "the Guardian's weekly UK politics podcast (on the department's list)", 2, "uk", "podcast"),
    ("Political Fix",             "https://feeds.acast.com/public/shows/8e80ba05-2f15-4479-a6cc-b2f6635a1fe0", "podcast", "the Financial Times' weekly UK politics podcast (on the department's list)", 1, "uk", "podcast"),
    ("Today in Parliament",       "https://podcasts.files.bbci.co.uk/b006qtqd.rss",                 "podcast",  "BBC Radio 4's nightly half hour of what was said in both Houses", 1, "uk", "podcast"),
    ("The NPR Politics Podcast",  "https://feeds.npr.org/510310/podcast.xml",                       "podcast",  "NPR's Washington reporters, most weekdays (on the department's list)", 2, "us", "podcast"),
    ("Politics Weekly America",   "https://www.theguardian.com/politics/series/politics-weekly-america/podcast.xml", "podcast", "the Guardian's weekly US politics podcast (on the department's list)", 1, "us", "podcast"),
    ("The Last Best Hope?",       "https://feeds.acast.com/public/shows/62cda17f1c07740014d65e4f",  "podcast",  "Adam Smith of Oxford's Rothermere American Institute on US politics and history (on the department's list)", 1, "us", "podcast"),
]

DIVISIONS_SEARCH = "https://commonsvotes-api.parliament.uk/data/divisions.json/search?take=40"
DIVISION_ONE = "https://commonsvotes-api.parliament.uk/data/division/{id}.json"
DIVISION_PAGE = "https://votes.parliament.uk/Votes/Commons/Division/{id}"
BILL_SEARCH = "https://bills.parliament.uk/?SearchTerm={q}"
# procedural votes a pupil cannot sensibly vote on without the context
DIV_SKIP = re.compile(r"closure motion|sit in private|programme motion|programme \(no|business of the house|allocation of time|^that the (question|clause|schedule)|^draft .*(regulations|order)\b", re.I)
# a numbered amendment or new clause means nothing without the debate; keep those only when Parliament describes them
DIV_NEEDS_DESC = re.compile(r"amendment \d|new clause|new schedule|clause \d|schedule \d|\bamdt\b|\bnc\s?\d", re.I)

# AQA 7152 as the department teaches it, by keyword (lower-case match)
THEMES = {
    "P1 Parliament and government": r"parliament|\bcommons\b|\blords\b|\bmps?\b|\bpeers?\b|select committee|\bpmqs?\b|prime minister|\bcabinet\b|\bminister|downing street|\bno\.? ?10\b|\bwhips?\b|\bspeaker\b|\bbills?\b|legislation|hansard|backbench|reshuffle|civil servi|prerogative|\bbudget\b|chancellor|treasury|\bgovernment\b",
    "P1 Constitution, courts and devolution": r"constitution|supreme court|judicial review|human rights act|\bechr\b|\bjudges?\b|\bjudiciary\b|rule of law|devolution|devolved|holyrood|senedd|stormont|scottish parliament|welsh government|northern ireland|\bmayors?\b|local government|\bcouncils?\b|lords reform|bill of rights|\bmonarch|\bking\b|royal assent",
    "P1 Democracy, elections and parties": r"election|by-election|\bballot|\bvoters?\b|turnout|\bpolls?\b|polling|first past the post|\bfptp\b|proportional|referendum|manifesto|\blabour\b|\bconservatives?\b|\btory\b|\btories\b|lib dems?|liberal democrat|reform uk|green party|\bsnp\b|plaid|party leader|leadership (contest|election|race)|voting age|franchise|electoral commission|starmer|badenoch|farage|davey|swinney|party conference|\bfactions?\b",
    "P1 Pressure groups, media and the EU": r"pressure group|lobby(ing|ists?)\b|trade union|\bstrikes?\b|campaign(ers| group)|think.?tank|\bdonors?\b|donation|party funding|brexit|european union|\beu\b|trade deal|\bmedia\b|\bpress\b|\bbbc\b|ofcom|social media|\btiktok\b|\bx\b|newspapers?",
    "P2 Congress and the President": r"\bcongress|\bsenate\b|senators?\b|house of representatives|speaker of the house|white house|\bpresiden|executive order|\bveto|filibuster|shutdown|impeach|capitol|oval office|\bcabinet\b|pentagon|federal (government|agenc)",
    "P2 US courts and rights": r"supreme court|scotus|justices?\b|\broe\b|amendment|first amendment|second amendment|\bguns?\b|abortion|civil rights|affirmative action|voting rights|federal (court|judge)|\bcert\b|oral argument|chief justice|\bdobbs\b|due process",
    "P2 US elections and parties": r"midterm|\bprimar(y|ies)\b|\bcaucus|electoral college|democrat|republican|\bgop\b|\bmaga\b|swing state|\bgovernor|ballot measure|super pac|campaign finance|\bpac\b|redistricting|gerrymander|\bturnout\b|\bpolls?\b|candidates?\b",
    "P3 Political ideas": r"\bliberal(ism)?\b|conservatism|socialis|feminis|ideolog|\bmarx|rawls|\bmill\b|\blocke\b|\bburke\b|hobbes|nozick|ayn rand|hayek|neoliberal|populis|nationalis|libertarian|\bthe state\b|freedom|liberty|equality|social justice|\bwelfare\b|free market|patriarch|class struggle|collectivis|individualis|\bleft.wing|\bright.wing|\bcentrist|\bthird way",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}
SIDE_OK = {"uk": lambda k: not k.startswith("P2"), "us": lambda k: not k.startswith("P1"), "any": lambda k: True}

# notices and items that are not for this board
SKIP = re.compile(r"\blive\b|liveblog|as it happened|daily quiz|\bquiz\b|^letters?:|crossword|job opportunit|vacanc|webinar|sponsored|\bdeals?\b|discount|subscribe|competition:|win a |gift guide|trailer|^introducing|bonus:|ad-free|members? only|obituar|\brape\b|sexual (assault|abuse|misconduct)|child (abuse|sex)|grooming|paedoph|murder|horoscope|recipe", re.I)


def tag(title, desc, side):
    """Tags found in the headline come first; tags found only in the summary are used when the
    headline gives none. At most three. UK sources take Paper 1 tags, US sources Paper 2."""
    ok = SIDE_OK.get(side, SIDE_OK["any"])
    head = [k for k, rx in _rx.items() if ok(k) and rx.search(title)]
    if head:
        return head[:3]
    return [k for k, rx in _rx.items() if ok(k) and rx.search(desc)][:2]


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = (s.replace("&nbsp;", " ").replace("&#8217;", "’").replace("&#8216;", "‘").replace("&#8220;", "“")
          .replace("&#8221;", "”").replace("&#8211;", "–").replace("&#8212;", "—").replace("&amp;", "&")
          .replace("&#039;", "'").replace("&#39;", "'").replace("&quot;", '"').replace("&lt;", "<").replace("&gt;", ">"))
    s = s.replace("&#160;", " ").replace(" ", " ").replace("&#x27;", "'")
    s = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*The post .*? appeared first on [^.]*\.?", "", s)
    s = re.sub(r"\s*(Continue reading.*|Read more.*|Read full article.*)$", "", s)
    # podcast show notes carry adverts and sign-up blurbs after the first paragraph or two
    s = re.split(r"\s(?:Learn more about your ad choices|See acast\.com/privacy|Hosted on Acast|Join .{0,40} for ad-free|Sign up|Subscribe to|Become a member|Producer:|Produced by|Get in touch|Email us|Instagram:|Twitter:|TikTok:|Assistant Producer|Video Editor|Social Producer|Senior Producer|Hosted by|Presented by)", s)[0]
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


def divisions():
    """The latest recorded votes in the Commons, newest first, with the party split on each side."""
    try:
        lst = json.loads(fetch(DIVISIONS_SEARCH, "application/json"))
    except Exception as e:
        print(f"!! Commons Votes API: {e}", file=sys.stderr)
        return [], False
    out = []
    for d in lst:
        title = clean(d.get("Title") or "")
        if not title or DIV_SKIP.search(title):
            continue
        did = d.get("DivisionId")
        desc = clean(d.get("FriendlyDescription") or "")
        if DIV_NEEDS_DESC.search(title) and not desc:
            continue
        parties = {"aye": [], "no": []}
        try:
            one = json.loads(fetch(DIVISION_ONE.format(id=did), "application/json"))
            desc = desc or clean(one.get("FriendlyDescription") or "")
            for side, key in (("aye", "Ayes"), ("no", "Noes")):
                c = {}
                for m in one.get(key) or []:
                    p = m.get("PartyAbbreviation") or m.get("Party") or "?"
                    c[p] = c.get(p, 0) + 1
                parties[side] = sorted(([p, n] for p, n in c.items()), key=lambda x: -x[1])
        except Exception as e:
            print(f"!! division {did}: {e}", file=sys.stderr)
        bill = ""
        m = re.match(r"^(.*?\bBill\b(?: \[Lords\])?)", title)
        if m:
            bill = BILL_SEARCH.format(q=urllib.parse.quote(m.group(1).replace(" [Lords]", "")))
        out.append({
            "id": "div-" + str(did), "no": d.get("Number"), "date": (d.get("Date") or "")[:10],
            "title": title, "desc": desc[:400], "ayes": d.get("AyeCount"), "noes": d.get("NoCount"),
            "deferred": bool(d.get("IsDeferred")), "parties": parties, "url": DIVISION_PAGE.format(id=did), "bill": bill,
            "th": tag(title, desc, "uk") or ["P1 Parliament and government"],
        })
        if len(out) >= 6:
            break
    return out, True


def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = {"story": now - dt.timedelta(days=7), "podcast": now - dt.timedelta(days=14)}
    stories, sources = [], []
    for label, url, level, line, keep, side, kind in FEEDS:
        try:
            raw = fetch(url)
            got = items_from(raw)
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "level": level, "line": line, "kind": kind, "side": side, "ok": False, "n": 0})
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
            th = tag(title, desc, side)
            stories.append({
                "id": hashlib.sha1((link + title).encode("utf-8")).hexdigest()[:10],
                "src": label, "level": level, "kind": kind, "side": side, "title": title, "url": link,
                "desc": (desc[:277] + "…") if len(desc) > 280 else desc,
                "date": (date or now).isoformat(timespec="minutes"), "th": th,
            })
            n += 1
        sources.append({"src": label, "level": level, "line": line, "kind": kind, "side": side, "ok": True, "n": n})
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
    divs, div_ok = divisions()
    sources.append({"src": "Commons Votes (Parliament)", "level": "official", "line": "Parliament's own record of every recorded vote in the Commons", "kind": "divisions", "side": "uk", "ok": div_ok, "n": len(divs)})
    data = {
        "made": now.isoformat(timespec="minutes"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "stories": mixed,
        "divisions": divs,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(mixed)} items on the Order Paper and {len(divs)} divisions")
    if len(mixed) < 8:
        sys.exit(1)


if __name__ == "__main__":
    main()
