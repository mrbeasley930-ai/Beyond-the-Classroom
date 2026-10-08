#!/usr/bin/env python3
"""Pulls the French news feeds and writes data/french-news.json for the Kiosque page.

Runs in a GitHub Action every morning (see .github/workflows/french-news.yml).
No third-party packages: urllib + xml only.

Each headline is tagged to the Edexcel 9FR0 themes by keyword. The tags are a
rough sort to help a pupil find a story for the theme they are working on; they
are not a judgement about the story.
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

OUT = pathlib.Path(__file__).resolve().parent.parent / "data" / "french-news.json"
UA = "Mozilla/5.0 (compatible; Beyond-the-Classroom-kiosque/1.0; +https://mrbeasley930-ai.github.io/Beyond-the-Classroom/)"

# label, feed url, kind, line (press orientation for the T2 "médias" point), keep
FEEDS = [
    ("Le Monde",          "https://www.lemonde.fr/rss/une.xml",                      "presse", "centre-gauche · quotidien du soir", 10),
    ("Le Figaro",         "https://www.lefigaro.fr/rss/figaro_actualites.xml",       "presse", "droite · le plus ancien quotidien national (1826)", 8),
    ("Libération",        "https://www.liberation.fr/arc/outboundfeeds/rss-all/collection/accueil-une/?outputType=xml", "presse", "gauche · fondé par Sartre et Serge July (1973)", 8),
    ("France 24",         "https://www.france24.com/fr/france/rss",                  "tv",     "service public international · rubrique France", 10),
    ("RFI",               "https://www.rfi.fr/fr/france/rss",                        "radio",  "radio publique internationale · rubrique France", 10),
    ("franceinfo",        "https://www.franceinfo.fr/titres.rss",                    "radio",  "service public · chaîne d'information continue", 10),
    ("France Inter",      "https://www.radiofrance.fr/franceinter/rss",              "radio",  "première radio de France · actualités", 8),
    ("1jour1actu",        "https://www.1jour1actu.com/feed",                         "facile", "l'actualité expliquée aux 8–13 ans · français clair", 6),
    ("Journal en français facile", "https://francaisfacile.rfi.fr/fr/podcasts/journal-en-fran%C3%A7ais-facile/podcast", "ecouter", "RFI · 10 minutes par jour, avec la transcription", 5),
]

# Edexcel 9FR0 themes and sub-themes, by keyword (lower-case, accent-insensitive match)
THEMES = {
    "T1 Famille":        r"famille|familial|mariage|mari[ée]s|divorce|pacs|couple|parent|enfant|natalit|naissance|garde d'enfant|congé parental|adoption|pma|fécondit",
    "T1 Éducation":      r"école|ecole|lycée|lycee|collège|college|bac\b|baccalaur|université|universite|étudiant|etudiant|élève|eleve|enseignant|professeur|éducation nationale|education nationale|parcoursup|rentrée scolaire",
    "T1 Travail":        r"grève|greve|syndicat|chômage|chomage|emploi|salaire|smic|retraite|travail|cgt|télétravail|teletravail|égalité salariale|licenciement|patronat|medef",
    "T2 Musique":        r"musique|chanson|chanteu|concert|rap\b|rappeu|album|festival de musique|victoires de la musique|fête de la musique|aya nakamura|stromae|opéra|opera\b",
    "T2 Médias":         r"média|media|journalis|presse|télévision|television|réseaux sociaux|reseaux sociaux|tiktok|instagram|youtube|x \(ex-twitter\)|twitter|influenceu|désinformation|desinformation|fake news|infox|liberté d'expression|liberte d'expression|audiovisuel|cnews|bfm|arcom|bolloré",
    "T2 Fêtes":          r"festival|fête|fete\b|tradition|carnaval|noël|noel\b|14 juillet|14-juillet|cannes|avignon|patrimoine|journées du patrimoine|beaujolais|tour de france",
    "T2 Politique":      r"assemblée|assemblee|sénat|senat|gouvernement|premier ministre|première ministre|ministre|élection|election|présidentiel|presidentiel|législative|legislative|macron|élysée|elysee|matignon|député|depute|motion de censure|49\.3|parlement|référendum|referendum|municipales|constitution",
    "T3 Immigration":    r"immigr|migrant|réfugié|refugie|demandeur d'asile|asile|sans-papiers|titre de séjour|frontière|frontiere|ofpra|expuls|naturalis|intégration|integration|banlieue|quartiers populaires|diversité|discrimination|racis",
    "T3 Laïcité":        r"laïc|laic|voile|abaya|signes religieux|religion|islam|musulman|catholi|juif|antisémit|antisemit|loi de 1905|séparatisme|separatisme|blasph",
    "T3 Extrême droite": r"rassemblement national|\brn\b|le pen|bardella|extrême droite|extreme droite|reconquête|zemmour|front national|identitaire",
    "T4 Occupation et Résistance": r"résistan|resistan|occupation|vichy|pétain|petain|collaborat|déport|deport|shoah|rafle|jean moulin|de gaulle|18 juin|libération de paris|liberation de paris|seconde guerre|1944|1940|panthéon|pantheon|mémoire|memoire|commémor|commemor",
    "No et moi":         r"sans-abri|sans abri|sdf\b|mal-logement|mal logement|sans domicile|hébergement d'urgence|hebergement d'urgence|abbé pierre|abbe pierre|samu social|samusocial|précarité|precarite|pauvreté|pauvrete|restos du c",
    "La Haine":          r"police|policier|violences policières|violences policieres|bavure|émeute|emeute|banlieue|cité|cites\b|quartier|jeunes des quartiers|kassovitz|igpn|nahel",
}
_rx = {k: re.compile(v, re.I) for k, v in THEMES.items()}

def strip_accents(s):
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

def tag(text):
    t = text.lower()
    hits = [k for k, rx in _rx.items() if rx.search(t) or rx.search(strip_accents(t))]
    return hits

def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
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
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None

def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/rss+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

NS = {"atom": "http://www.w3.org/2005/Atom", "itunes": "http://www.itunes.com/dtds/podcast-1.0.dtd", "content": "http://purl.org/rss/1.0/modules/content/"}

def items_from(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    # RSS 2.0
    for it in root.iter("item"):
        title = clean(it.findtext("title"))
        link = (it.findtext("link") or "").strip()
        if not link:
            enc = it.find("enclosure")
            link = enc.get("url") if enc is not None else ""
        desc = clean(it.findtext("description") or it.findtext("itunes:summary", namespaces=NS) or "")
        date = parse_date(it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date"))
        dur = (it.findtext("itunes:duration", namespaces=NS) or "").strip()
        out.append((title, link, desc, date, dur))
    # Atom
    for it in root.findall("atom:entry", NS):
        title = clean(it.findtext("atom:title", namespaces=NS))
        link_el = it.find("atom:link", NS)
        link = link_el.get("href") if link_el is not None else ""
        desc = clean(it.findtext("atom:summary", namespaces=NS) or "")
        date = parse_date(it.findtext("atom:updated", namespaces=NS) or it.findtext("atom:published", namespaces=NS))
        out.append((title, link, desc, date, ""))
    return out

def main():
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=4)
    headlines, sources = [], []
    for label, url, kind, line, keep in FEEDS:
        try:
            raw = fetch(url)
            got = items_from(raw)
        except Exception as e:
            print(f"!! {label}: {e}", file=sys.stderr)
            sources.append({"src": label, "kind": kind, "line": line, "ok": False, "n": 0})
            continue
        got = [g for g in got if g[0] and g[1]]
        got.sort(key=lambda g: g[3] or dt.datetime.min.replace(tzinfo=dt.timezone.utc), reverse=True)
        n = 0
        for title, link, desc, date, dur in got:
            if date and date < cutoff:
                continue
            if n >= keep:
                break
            text = f"{title}. {desc}"
            hid = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]
            headlines.append({
                "id": hid, "src": label, "kind": kind, "title": title, "url": link,
                "desc": desc[:260], "date": (date or now).isoformat(timespec="minutes"),
                "th": tag(text), "dur": dur,
            })
            n += 1
        sources.append({"src": label, "kind": kind, "line": line, "ok": True, "n": n})
        print(f"{label}: {n} kept of {len(got)}")
    # de-duplicate by url
    seen, uniq = set(), []
    for h in headlines:
        if h["url"] in seen:
            continue
        seen.add(h["url"]); uniq.append(h)
    uniq.sort(key=lambda h: h["date"], reverse=True)
    data = {
        "made": now.isoformat(timespec="minutes"),
        "madeLocal": now.astimezone(dt.timezone(dt.timedelta(hours=1))).strftime("%A %d %B %Y, %H:%M"),
        "themes": list(THEMES.keys()),
        "sources": sources,
        "headlines": uniq,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"wrote {OUT} with {len(uniq)} headlines")
    if len(uniq) < 10:
        sys.exit(1)

if __name__ == "__main__":
    main()
