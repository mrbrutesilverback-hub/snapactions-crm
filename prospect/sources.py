"""Veille tournois : recuperation depuis des sources configurables, normalisation,
dedoublonnage. Aucun envoi de message ici — la veille ne fait que remplir la base."""
import csv
import datetime as dt
import html
import io
import json
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

from . import db, geo, ics, util, photos

USER_AGENT = "prospect-clubs/1.0 (CRM local; contact via le site du club)"
TIMEOUT = 25

KINDS = [
    ("ics",  "Calendrier iCal (.ics)"),
    ("rss",  "Flux RSS / Atom"),
    ("json", "API JSON"),
    ("html", "Page HTML (motif regex)"),
    ("csv",  "CSV / TSV en ligne"),
    ("openagenda", "Agenda public OpenAgenda (slug ou numero)"),
    ("retrocalage", "Rassemblements auto (Retrocalage)"),
    ("ffm", "Moto, quad et engins (carte FFM)"),
]


# --------------------------------------------------------------------------

def normalize_url(url):
    """webcal:// -> https:// (les abonnements Apple/iCloud utilisent ce schema,
    que urllib ne connait pas)."""
    url = (url or "").strip()
    if url.lower().startswith("webcal://"):
        return "https://" + url[9:]
    if url.lower().startswith("webcals://"):
        return "https://" + url[10:]
    return url


def fetch(url):
    url = normalize_url(url)
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("adresse non geree : %s" % url[:60])
    req = urllib.request.Request(url, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/calendar, application/json, text/html, application/xml, */*",
        "Accept-Language": "fr-FR,fr;q=0.9",
    })
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        raw = resp.read()
    charset = "utf-8"
    try:
        ctype = resp.headers.get("Content-Type", "")
        m = re.search(r"charset=([\w\-]+)", ctype or "", re.I)
        if m:
            charset = m.group(1)
    except Exception:
        pass
    for enc in (charset, "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


# Les affiches en ligne sont truffees de pictogrammes : « \U0001F3C6 ELITE CAR CUP \u26bd\U0001F525 ».
# Ils n'apportent rien au CRM et ressortiraient tels quels dans l'objet du mail.
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F\u200D\u20E3]+")


def sans_emoji(texte):
    return re.sub(r"\s{2,}", " ", EMOJI_RE.sub(" ", str(texte or ""))).strip(" -\u2013\u2014\u00b7")


def strip_tags(s):
    if not s:
        return ""
    s = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html.unescape(s)
    return re.sub(r"[ \t]{2,}", " ", s).strip()


def _dig(obj, path):
    """Chemin pointe dans du JSON : 'data.items.0.title'."""
    cur = obj
    for part in str(path).split("."):
        if cur is None:
            return None
        if isinstance(cur, list):
            if part.isdigit() and int(part) < len(cur):
                cur = cur[int(part)]
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(part)
        else:
            return None
    return cur


# --------------------------------------------------------------------------
# Adaptateurs
# --------------------------------------------------------------------------

def from_ics(text, source_name):
    out = []
    for ev in ics.parse_ics(text):
        start = ev.get("dtstart")
        name = ev.get("summary")
        if not name:
            continue
        end = ev.get("dtend")
        # En iCal, un DTEND en VALUE=DATE est exclusif : le dernier jour est la veille.
        if isinstance(end, dt.date) and not isinstance(end, dt.datetime):
            end = end - dt.timedelta(days=1)
        loc = ev.get("location") or ""
        postal = util.extract_postal(loc) or util.extract_postal(ev.get("description") or "")
        out.append({
            "name": name.strip(),
            "start_date": _as_date(start),
            "end_date": _as_date(end),
            "city": _city_from_location(loc),
            "postal_code": postal,
            "venue": loc.split(",")[0].strip() if loc else None,
            "url": ev.get("url"),
            "description": (ev.get("description") or "")[:2000] or None,
            "external_id": ev.get("uid"),
            "source": source_name,
        })
    return out


def from_rss(text, source_name):
    out = []
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError:
        return out
    items = root.findall(".//item") or root.findall(
        ".//{http://www.w3.org/2005/Atom}entry")
    for item in items:
        def get(tag):
            el = item.find(tag)
            if el is None:
                el = item.find("{http://www.w3.org/2005/Atom}" + tag)
            if el is None:
                return None
            return (el.text or "").strip() or (el.get("href") or "").strip() or None

        title = get("title")
        if not title:
            continue
        desc = strip_tags(get("description") or get("summary") or get("content") or "")
        link = get("link")
        blob = "%s %s" % (title, desc)
        out.append({
            "name": title,
            "start_date": _first_date(blob) or _as_date(util.parse_date(get("pubDate"))),
            "city": None,
            "postal_code": util.extract_postal(blob),
            "url": link,
            "description": desc[:2000] or None,
            "external_id": get("guid") or link,
            "source": source_name,
        })
    return out


FIELD_ALIASES = {
    "name": ["name", "title", "nom", "libelle", "intitule"],
    "start_date": ["start_date", "startDate", "date_debut", "dateDebut", "date", "start",
                   "debut"],
    "end_date": ["end_date", "endDate", "date_fin", "dateFin", "end", "fin"],
    "city": ["city", "ville", "commune", "localite"],
    "postal_code": ["postal_code", "postalCode", "cp", "code_postal", "zip"],
    "venue": ["venue", "lieu", "salle", "site", "installation"],
    "organizer": ["organizer", "organisateur", "club", "structure"],
    "level": ["level", "niveau", "categorie", "category", "type"],
    "url": ["url", "link", "lien", "permalink"],
    "external_id": ["id", "uid", "code", "reference"],
    "description": ["description", "details", "resume", "texte"],
}


CATEG_AGE_RE = re.compile(r"^u\d{1,2}", re.I)


def _categories_json(item):
    """Les categories d'age portees par la source, quand elle en publie.

    The Events Calendar range tout dans la meme liste : region, departement,
    tranche d'age, format. On ne garde que ce qui parle d'age ou de format,
    le reste est deja couvert par la ville et le code postal.
    """
    brut = item.get("categories")
    if not isinstance(brut, list):
        return None
    gardes = []
    for c in brut:
        nom = (c.get("name") if isinstance(c, dict) else str(c)) or ""
        plat = util.strip_accents(nom).lower()
        if CATEG_AGE_RE.match(plat) or plat in ("futsal", "seniors", "veterans", "feminines"):
            gardes.append(nom)
    return ", ".join(gardes) if gardes else None


def from_json(text, source_name, config):
    config = config or {}
    try:
        data = json.loads(text)
    except ValueError:
        return []
    root = config.get("root")
    items = _dig(data, root) if root else data
    if isinstance(items, dict):
        for key in ("results", "items", "data", "records", "hits", "tournaments"):
            if isinstance(items.get(key), list):
                items = items[key]
                break
    if not isinstance(items, list):
        return []
    mapping = config.get("fields") or {}
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        rec = {}
        for field, aliases in FIELD_ALIASES.items():
            path = mapping.get(field)
            value = _dig(item, path) if path else None
            if value is None:
                for alias in aliases:
                    if alias in item and item[alias] not in (None, ""):
                        value = item[alias]
                        break
            # Certaines API rendent un objet la ou on attend un mot : « organizer »
            # est une liste chez The Events Calendar. Rien a en tirer ici, et
            # SQLite refuse de l'ecrire — on laisse tomber.
            if isinstance(value, (dict, list)):
                value = None
            if value not in (None, ""):
                rec[field] = strip_tags(value) if isinstance(value, str) else value
        if not rec.get("name"):
            continue
        rec["start_date"] = _as_date(util.parse_date(rec.get("start_date")))
        rec["end_date"] = _as_date(util.parse_date(rec.get("end_date")))
        rec["postal_code"] = util.extract_postal(rec.get("postal_code")
                                                 or rec.get("city") or "") \
            or (rec.get("postal_code") if str(rec.get("postal_code", "")).isdigit() else None)
        rec["name"] = sans_emoji(rec["name"])[:200]
        if not rec["name"]:
            continue
        rec["source"] = source_name
        if not rec.get("categories"):
            cats = _categories_json(item)
            if cats:
                rec["categories"] = cats
        out.append(rec)
    return out


def from_html(text, source_name, config):
    """Extraction par motif regex a groupes nommes.

    config = {"pattern": "...(?P<name>...)...(?P<start_date>...)...", "flags": "is"}
    Les groupes reconnus sont ceux de FIELD_ALIASES.
    """
    config = config or {}
    pattern = config.get("pattern")
    if not pattern:
        return []
    flags = 0
    for ch in (config.get("flags") or "is"):
        flags |= {"i": re.I, "s": re.S, "m": re.M, "x": re.X}.get(ch, 0)
    try:
        rx = re.compile(pattern, flags)
    except re.error as exc:
        raise ValueError("motif invalide : %s" % exc)
    out = []
    for m in rx.finditer(text):
        rec = {k: strip_tags(v) for k, v in m.groupdict().items() if v}
        if not rec.get("name"):
            continue
        rec["start_date"] = _as_date(util.parse_date(rec.get("start_date"))) \
            or _first_date(rec.get("start_date") or "")
        rec["end_date"] = _as_date(util.parse_date(rec.get("end_date")))
        blob = " ".join(str(v) for v in rec.values())
        rec["postal_code"] = rec.get("postal_code") or util.extract_postal(blob)
        base = config.get("base_url")
        if rec.get("url") and base and rec["url"].startswith("/"):
            rec["url"] = base.rstrip("/") + rec["url"]
        rec["source"] = source_name
        out.append(rec)
    return out


def from_csv(text, source_name):
    from . import extract as ex
    headers, rows = ex.sniff_table(text)
    if not headers:
        return []
    idx = {}
    for i, h in enumerate(headers):
        key = util.strip_accents((h or "").strip().lower())
        for field, aliases in FIELD_ALIASES.items():
            if key in [util.strip_accents(a.lower()) for a in aliases]:
                idx[i] = field
                break
    out = []
    for row in rows:
        rec = {}
        for i, field in idx.items():
            if i < len(row) and (row[i] or "").strip():
                rec[field] = row[i].strip()
        if not rec.get("name"):
            continue
        rec["start_date"] = _as_date(util.parse_date(rec.get("start_date")))
        rec["end_date"] = _as_date(util.parse_date(rec.get("end_date")))
        rec["source"] = source_name
        out.append(rec)
    return out


# --------------------------------------------------------------------------

MONTHS_FR = {
    "janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
    "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
    "decembre": 12, "janv": 1, "fev": 2, "avr": 4, "juil": 7, "sept": 9,
    "oct": 10, "nov": 11, "dec": 12,
}
DATE_FR_RE = re.compile(
    r"(\d{1,2})\s*(?:er)?\s+([a-zéûôa-z]+)\.?\s+(\d{4})", re.I)


def _first_date(text):
    """Premiere date trouvee dans un texte, formats FR inclus ('12 avril 2026')."""
    if not text:
        return None
    m = re.search(r"(?<!\d)(\d{4}-\d{2}-\d{2})(?!\d)", text)
    if m:
        return m.group(1)
    m = re.search(r"(?<!\d)(\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4})(?!\d)", text)
    if m:
        d = util.parse_date(m.group(1))
        if d:
            return d.isoformat()
    m = DATE_FR_RE.search(util.strip_accents(text).lower())
    if m:
        month = MONTHS_FR.get(m.group(2)[:9]) or MONTHS_FR.get(m.group(2)[:4])
        if month:
            try:
                return dt.date(int(m.group(3)), month, int(m.group(1))).isoformat()
            except ValueError:
                return None
    return None


def _as_date(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    d = util.parse_date(value)
    return d.isoformat() if d else None


def _city_from_location(loc):
    if not loc:
        return None
    parts = [p.strip() for p in re.split(r"[,\n]", loc) if p.strip()]
    for part in reversed(parts):
        cleaned = re.sub(r"\d{5}", "", part).strip(" ,-")
        if cleaned and not cleaned.isdigit() and len(cleaned) < 40 \
                and cleaned.lower() not in ("france", "fr"):
            return util.title_name(cleaned)
    return None


# --------------------------------------------------------------------------
# Enregistrement
# --------------------------------------------------------------------------

def fingerprint(rec):
    return util.short_hash(util.norm_key(rec.get("name")),
                           rec.get("start_date") or "",
                           util.norm_key(rec.get("city") or rec.get("postal_code") or ""))


# Le nom d'un tournoi porte presque toujours celui du club qui l'organise :
# « Challenge Jose Perez 2026 U12 du SC Cogolin », « Tournoi U13 - FC Croth
# Marcilly ». La source ne remplit pas le champ organisateur ; sans club
# rattache, aucun message ne peut etre redige — c'est ce qui laissait des
# centaines de fiches muettes a l'ecran.
CLUB_MARQUEUR = re.compile(
    r"\b(?:f\.?c|a\.?s|u\.?s|s\.?c|r\.?c|e\.?s|c\.?a|o\.?l|olympique|sporting|stade|"
    r"club|entente|etoile|amicale|association|cercle|academie|academy|racing|avenir|"
    r"jeunesse|elan|esperance|athletique|athletic|union|foot|football|sportive|sportif)\b",
    re.I)

# Ce qui suit « de » sans etre un club : on ne prend pas « de Pre Saison » ni
# « de la Toussaint » pour une raison sociale.
PAS_UN_CLUB = re.compile(
    r"^(?:pre[ -]?saison|rentree|(?:la\s+)?toussaint|noel|paques?|printemps|ete|hiver|"
    r"automne|fin d[' ]annee|l[' ]ascension|(?:la\s+)?pentecote|(?:la\s+)?galette|"
    r"(?:l[' ])?an nouveau|nouvel an|(?:la\s+)?saison|(?:l[' ])?ete)\b", re.I)

SEPARATEUR = re.compile(r"\s+[–—-]\s+")
# Le marqueur seul, sans ce qui suit. L'ancienne expression capturait
# gloutonnement « (.+)$ » : sur « Tournoi de Rentree U9 a 5 de l'AS Morieres »,
# finditer ne rendait qu'UNE occurrence — la premiere — et le candidat etait
# donc tout le reste du titre. CLUB_MARQUEUR y trouvait « AS » quelque part et
# validait l'ensemble. C'est ainsi qu'est ne le club « Tournoi de Toussaint
# 2026 U11 de l'AS Brignoles », et « LE HAVRE | U11 & U13 ».
RATTACHE = re.compile(r"\b(?:de\s+l['’]|de\s+la\s+|du\s+|des\s+|de\s+)", re.I)


# Ce qui designe une CATEGORIE d'age ou de niveau, jamais un club. Une annonce
# en capitales — « CHAMPIONNAT DE FRANCE 1ere D SENIORS EN VENDEE » — finissait
# par fabriquer un club nomme « FRANCE 1ere D SENIORS EN VENDEE », parce que le
# mot qui suit le dernier « de » commence par des majuscules comme tout le
# reste du titre. Quatre faux clubs sont nes ainsi en une soiree.
CATEGORIE = re.compile(
    r"\b(?:seniors?|juniors?|cadets?|cadettes?|minimes?|benjamins?|poussins?|"
    r"veterans?|masters?|espoirs?|pupilles?|avenirs?|microbes?|debutants?|"
    r"u\d{1,2}|\d{1,2}\s?ans|feminines?|masculins?|mixtes?|"
    r"\d(?:er|e|eme|ere)?\s*d(?:an|ivision)?\b|grades?|ceintures?|"
    r"individuel(?:le)?s?|equipes?|ensembles?|duos?|nationaux?|regional|"
    r"departemental|interclubs?)\b", re.I)


def _est_club(candidat, titre_entier=None):
    texte = (candidat or "").strip(" .,;:")
    if not (3 <= len(texte) <= 60):
        return False
    plat = util.strip_accents(texte).lower()
    if PAS_UN_CLUB.match(plat):
        return False
    # « TIG 2027 », « ASP CUP 2026 » : un millesime trahit un nom d'epreuve,
    # pas une raison sociale.
    if re.search(r"\b(?:19|20)\d{2}\b", texte):
        return False
    # Une categorie n'est pas un club : « MONACO SENIORS », « VAR BENJAMINS »,
    # « GRADES VINON 1 AU 3eme Dan ». Meme si le debut ressemble a un sigle.
    if CATEGORIE.search(plat):
        return False
    if CLUB_MARQUEUR.search(plat):
        return True
    # « USZ », « FCPA », « CDFC » : un sigle en capitales est un nom de club.
    # Mais seulement s'il EST le candidat, ou presque : dans un titre ecrit
    # tout en capitales, chaque mot commence par des majuscules et ce signal
    # ne vaut plus rien. On exige donc un candidat court.
    if len(texte.split()) > 3:
        return False
    if titre_entier and titre_entier == titre_entier.upper() \
            and len(texte.split()) > 1:
        return False
    return bool(re.match(r"^[A-Z]{2,6}(?:\b|$)", texte))


def club_du_titre(nom, ville=None):
    """Le club organisateur, lu dans le nom du tournoi. None si on n'en voit pas.

    Deux formes se partagent les annonces : « ... de l'US Venelles » et
    « ... - FC Longjumeau ». La seconde se confond avec la ville collee en fin
    de titre, d'ou la comparaison avec la commune quand on la connait.
    """
    texte = str(nom or "").strip()
    if not texte:
        return None
    morceaux = SEPARATEUR.split(texte)

    # Forme 1 : le club est rattache par « de / du / de l' » dans la tete.
    # On part du dernier « de » vers le premier : « Tournoi *de* Rentree ... *de*
    # l'AS Morieres » — c'est le dernier qui porte le club.
    tete = morceaux[0].strip()
    # Du DERNIER « de » vers le premier : c'est le dernier qui porte le club.
    for m in reversed(list(RATTACHE.finditer(tete))):
        candidat = tete[m.end():].strip(" .,;:")
        if _est_club(candidat, texte):
            return candidat

    # Forme 2 : le club est apres le tiret — sauf si c'est la commune.
    if len(morceaux) > 1:
        queue = morceaux[-1].strip(" .,;:")
        meme_ville = ville and util.strip_accents(queue).lower() == \
            util.strip_accents(str(ville)).lower()
        if not meme_ville and _est_club(queue, texte):
            return queue
    return None


# --------------------------------------------------------------------------
# Perimetre : ce que la veille ramene et qui ne sera jamais fait
# --------------------------------------------------------------------------

# Au-dela de ce trajet, un tournoi isole ne vaut pas le deplacement.
PERIMETRE_MIN_DEFAUT = 120

# Les deux bassins ou le voyage se groupe avec autre chose : la duree n'y
# disqualifie pas la fiche. Memes departements que l'ecran de veille.
BASSINS = ["69", "01", "38", "42", "26", "71", "73", "74",
           "31", "81", "82", "32", "09", "11", "65"]

# Une zone que Julien travaille, trop loin pour la regle generale mais trop
# dense en clubs pour etre jetee. Le lac de Sainte-Croix porte quatre clubs de
# voile et d'aviron a lui seul, et ses communes tombent entre 104 et 123
# minutes : la moitie passait sous la limite des 120, l'autre non. Une fiche
# y etait donc gardee ou purgee selon l'itineraire calcule ce jour-la.
# On raisonne en communes et en codes postaux, pas en departements : mettre
# tout le 04 sauverait aussi Barcelonnette, a trois heures.
ZONE_VERDON = {
    "nom": "Lac de Sainte-Croix",
    "cps": {"83630", "04500", "04360"},
    "communes": {"bauduen", "les salles-sur-verdon", "aiguines",
                 "sainte-croix-du-verdon", "moustiers-sainte-marie",
                 "quinson", "esparron-de-verdon", "baudinard-sur-verdon",
                 "montagnac-montpezat", "regusse", "aups"},
}

ZONES_CHOISIES = [ZONE_VERDON]


def dans_une_zone_choisie(rec):
    """Vrai si la fiche est dans une zone que Julien veut couvrir malgre la
    distance. Renvoie le nom de la zone, pour pouvoir le dire dans le journal."""
    cp = str(rec.get("postal_code") or "").strip()
    ville = util.strip_accents(str(rec.get("city") or "")).strip().lower()
    for zone in ZONES_CHOISIES:
        if cp and cp in zone["cps"]:
            return zone["nom"]
        if ville and ville in zone["communes"]:
            return zone["nom"]
    return None


# Ce que Julien a mis lui-meme n'est jamais purge, quelle que soit la distance :
# son agenda, un flyer qu'il a ramasse, une date annoncee par un club.
SOURCES_PROTEGEES = ("snapactions", "flyer depose", "annonce par le club",
                     "manuel", "calendrier", "colle a la main",
                     # Les dates du Nord et de Belgique : elles ne sont pas
                     # pour Julien, elles sont pour le sourceur, qui cherche
                     # un photographe sur place. Les jeter sur la distance
                     # reviendrait a jeter exactement ce qu'on est alle
                     # chercher. Elles vivent dans l'ecran « L'equipe ».
                     "pour le sourceur")


def _protegee(source):
    s = util.strip_accents(str(source or "")).lower()
    return any(s.startswith(p) for p in SOURCES_PROTEGEES)


def lieu_corrobore(rec):
    """Le lieu de la fiche tient-il debout, et sur quelle preuve ?

    Une fiche saisie a la main depuis une capture d'ecran n'a aucune preuve
    derriere elle. Le 14 septembre, « Vedene (84) » est entre en base comme
    « Vidauban (83) » : 231 km annonces a 65, deux heures et demie de route
    presentees comme cinquante minutes, et un club demarche pour rien.

    On s'appuie desormais sur le referentiel officiel des communes plutot que
    sur notre table maison de 261 lignes : un code postal qui ne correspond
    pas au nom de la ville est detecte, et dit.

    Renvoie (True|False|None, phrase). None = rien a corroborer.
    """
    from . import lieux
    ville = (rec.get("city") or "").strip()
    cp = (rec.get("postal_code") or "").strip()
    if not ville and not cp:
        return None, None

    res = lieux.resoudre(ville=ville or None, code_postal=cp or None,
                         dept=rec.get("dept"))
    if res["conflit"]:
        return False, res["conflit"]
    if res["confiance"] in ("sure", "probable"):
        return True, lieux.verdict_court(res)

    # Le referentiel ne connait pas ce nom : commune etrangere, ou transcription
    # fautive. Dans les deux cas on ne peut pas garantir la distance.
    texte = " ".join(str(rec.get(k) or "")
                     for k in ("name", "venue", "organizer"))
    trouve = lieux.commune_dans_texte(texte)
    if trouve and lieux.cle(trouve["commune"]) == lieux.cle(ville):
        return True, "commune citée dans le texte de la fiche"
    return False, res["conflit"] or "lieu non identifié"


# A partir de combien d'equipes une fiche echappe au filtre de distance.
# Trente-deux equipes, c'est environ trois cent cinquante joueurs et autant de
# familles : de quoi remplir une journee, meme loin.
GROS_PLATEAU = 32

# Les sources ajoutees pour couvrir la France et la Belgique : on ne leur
# demande QUE les grosses machines. Le nom le dit, et c'est lui qui fait foi.
_CATEGORIE_JEUNE = re.compile(r"\bU\s?(\d{1,2})\b", re.I)


def assez_de_categories(rec, mini=2):
    """Un vrai plateau annonce plusieurs categories dans son titre.

    Les sources lointaines — le Nord, la Belgique — ne publient jamais leur
    nombre d'equipes : le filtre « gros plateau » les jetait donc toutes,
    et on ne ramenait rien de ce qu'on etait alle chercher. Ce qui distingue
    un vrai plateau d'un match amical, dans ces titres, c'est le nombre de
    categories annoncees. « Tournoi U7, U9, U11 & U13 » est une journee
    entiere avec des familles toute la matinee ; « Tournoi U13 » tout seul
    peut n'etre que quatre equipes un samedi apres-midi.
    """
    try:
        if int(rec.get("nb_equipes") or 0) >= GROS_PLATEAU:
            return True
    except (TypeError, ValueError):
        pass
    texte = " ".join(str(rec.get(k) or "") for k in ("name", "categories"))
    return len(set(_CATEGORIE_JEUNE.findall(texte))) >= int(mini)


def _gros_seulement(source_name):
    return str(source_name or "").startswith("Gros tournois")


def perimetre_minutes():
    try:
        return int(db.get_setting("perimetre_min", PERIMETRE_MIN_DEFAUT))
    except (TypeError, ValueError):
        return PERIMETRE_MIN_DEFAUT


def hors_perimetre(rec):
    """True si la fiche ne sera jamais faite : trop loin et hors des bassins.

    Prudence deliberee — on ne jette que ce dont on est sur :
      - une duree de trajet inconnue ne suffit pas a jeter ;
      - un departement de bassin sauve la fiche ;
      - une source renseignee par Julien lui-meme est intouchable.
    """
    if db.get_setting("perimetre_actif", "1") != "1":
        return False
    if _protegee(rec.get("source")):
        return False
    if str(rec.get("dept") or "") in BASSINS:
        return False
    if dans_une_zone_choisie(rec):
        return False
    # Un gros plateau ne se jette pas sur la distance. Six heures de route
    # pour six cent cinquante equipes, c'est un week-end qui se prepare ;
    # six heures pour quatre equipes, non. C'est le nombre de familles
    # presentes qui decide, pas le compteur kilometrique.
    try:
        if int(rec.get("nb_equipes") or 0) >= GROS_PLATEAU:
            return False
    except (TypeError, ValueError):
        pass
    minutes = rec.get("trajet_min")
    if minutes in (None, ""):
        return False
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return False
    return minutes > perimetre_minutes()


# Ce que Julien pose lui-meme est toujours pour lui, quelle que soit la route.
POSEES_PAR_JULIEN = ("snapactions", "flyer depose", "annonce par le club",
                     "manuel", "calendrier", "colle a la main",
                     "accord telephone", "accord au telephone")

# Sa region de travail, quand on ne connait pas le temps de route.
DEPTS_MAISON = ("04", "05", "06", "13", "83", "84")


def portee_de(rec):
    """« pour_moi » ou « reseau » : qui ira shooter cette date.

    Julien recrute des photographes dans toute la France. Un tournoi a Lille
    n'est plus un dechet a jeter sur la distance : c'est du travail pour
    quelqu'un qu'on va trouver la-bas. Mais il ne doit pas encombrer les
    ecrans ou Julien decide ce que LUI va faire — d'ou ces deux casiers.
    """
    source = util.strip_accents(str(rec.get("source") or "")).lower()
    if any(source.startswith(p) for p in POSEES_PAR_JULIEN):
        return "pour_moi"

    dept = str(rec.get("dept") or "").strip()
    if dept in BASSINS or dept in DEPTS_MAISON:
        return "pour_moi"
    if dans_une_zone_choisie(rec):
        return "pour_moi"

    minutes = rec.get("trajet_min")
    try:
        minutes = int(minutes) if minutes not in (None, "") else None
    except (TypeError, ValueError):
        minutes = None
    if minutes is not None:
        return "pour_moi" if minutes <= perimetre_minutes() else "reseau"
    # Route inconnue et hors region : c'est du lointain qui s'ignore.
    return "reseau"


def purger_hors_perimetre(dry_run=False):
    """ANCIEN comportement : supprimait les fiches lointaines.

    Ne plus appeler. Depuis que Julien recrute des photographes dans toute la
    France, une fiche lointaine n'est pas un dechet : c'est du travail pour
    quelqu'un d'autre. Elle est marquee « reseau » au lieu d'etre jetee.
    """
    if not dry_run:
        raise RuntimeError(
            "purger_hors_perimetre ne supprime plus rien : les fiches "
            "lointaines sont marquees « reseau », pas jetees.")
    lignes = db.rows_to_dicts(db.q(
        "SELECT id, name, source, dept, trajet_min, distance_km, status "
        "FROM tournaments"))
    vises = [r for r in lignes
             if r["status"] != "processed" and hors_perimetre(r)
             and not db.q1("SELECT id FROM missions WHERE tournament_id = ?", (r["id"],))]
    if not dry_run:
        for r in vises:
            db.delete("tournaments", r["id"])
        if vises:
            db.log("perimetre", "Purge : %d fiche(s) au-dela de %d min et hors bassins"
                   % (len(vises), perimetre_minutes()))
    return vises


# Un agenda de club ne contient pas que des competitions : il contient surtout
# la vie du club. « Entrainement » revient 95 fois chez un club de badminton.
# Les importer comme des tournois noierait les vraies dates — c'est la maladie
# qu'on a deja soignee une fois avec les 370 brouillons.
ROUTINE = re.compile(
    r"\b(entra[iî]nement|entrainements|cours|s[ée]ance|permanence|reunion|"
    r"r[ée]union|assembl[ée]e|\bag\b|bureau|conseil|inscription|reprise|"
    r"vacances|f[eé]ri[eé]|ferm[ée]|anniversaire|galette|repas|ap[ée]ro|"
    r"loto|tombola|stage|formation|arbitrage|convocation)\b", re.I)

# Ce qui vaut le deplacement d'un photographe : du monde, de l'enjeu, des
# familles dans les gradins.
COMPETITION = re.compile(
    r"\b(tournoi|tournois|comp[ée]tition|championnat|challenge|coupe|cup|"
    r"cross\b|course|rencontre|match|plateau|gala|d[ée]monstration|"
    r"open\b|trophee|troph[ée]e|critérium|criterium|meeting|interclub|"
    r"finale|qualification|s[ée]lection|festival|f[eê]te)\b", re.I)


# Des gestionnaires de sites de clubs ecrivent le nom du champ a la place de
# sa valeur : « LOCATION:Localisation ». Pris au mot, ca donne une commune
# nommee « Localisation » et une fiche sans distance.
LIEUX_VIDES = {"localisation", "lieu", "lieu a definir", "a definir", "adresse",
               "non precise", "non precisee", "tbd", "n a", "na", "-", "--", "."}


BALISES = re.compile(r"<[^>]+>")


def sans_html(texte):
    """Le texte d'une description, debarrasse de ses balises.

    Les agendas de clubs livrent du HTML complet, images comprises. Stocke tel
    quel, il pollue les recherches et s'affiche en clair dans les fiches.
    """
    if not texte or "<" not in str(texte):
        return texte
    plat = BALISES.sub(" ", str(texte))
    plat = html.unescape(plat)
    return re.sub(r"\s+", " ", plat).strip()


def depts_plausibles(dept=None):
    """Les departements ou une fiche de cette veille peut raisonnablement etre.

    Sert a departager les homonymes : « La Londe » existe dans le Var et en
    Seine-Maritime, « Vedene » seulement en Vaucluse. Trop etroite, la liste
    rejetait Vedene ; trop large, elle laissait passer la Haute-Saone. On
    prend donc les departements de travail, leurs voisins, et les bassins.
    """
    out = set(BASSINS) | {"06", "83"}
    for d in ("06", "83"):
        out |= set(util.nearby_depts(d))
    # Les voisins immediats des bassins : on y passe en montant.
    for d in ("69", "31", "84", "13"):
        out |= set(util.nearby_depts(d))
    if dept:
        out.add(str(dept))
    return out


def lieu_placeholder(valeur):
    plat = util.strip_accents(str(valeur or "")).lower().strip(" .,-")
    return plat in LIEUX_VIDES


def vaut_le_deplacement(rec):
    """Cette ligne d'agenda est-elle un evenement a photographier ?

    On tranche sur le titre, pas sur la description : un entrainement dont le
    texte mentionne « avant le tournoi de samedi » reste un entrainement.
    """
    titre = str(rec.get("name") or "")
    if COMPETITION.search(titre):
        return True
    if ROUTINE.search(titre):
        return False
    # Ni l'un ni l'autre : on garde, mais la fiche restera a trancher.
    return None


# Retrocalage publie ses evenements en JSON a l'interieur de la page. On ne
# devine donc rien : chaque fiche porte ses dates, sa commune, ses coordonnees
# GPS, et surtout le telephone ou le mail de l'organisateur — de quoi ecrire
# tout de suite, ce qu'aucune autre source de rassemblements ne permet.
_RETRO_FICHE = re.compile(r'\{"id":\d+,"titre":.*?"mini":"[^"]*"\}')


# --------------------------------------------------------------------------
# Federation Francaise de Motocyclisme : la carte des evenements
# --------------------------------------------------------------------------
# Julien demandait des randonnees de quad et d'engins — du public qui achete
# ses photos, comme les rassemblements de voitures anciennes.
#
# Deux pistes essayees avant celle-ci. L'agenda du Codever ne porte que six
# dates, dont trois assemblees d'association, et aucune dans le Sud-Est : il
# ne vaut pas une source. Le calendrier de la FFM, lui, se cache derriere une
# carte interactive — mais cette carte interroge un simple formulaire POST,
# departement par departement, et rend un HTML ou tout est lisible : commune,
# discipline, dates, club organisateur, adresse et coordonnees.
#
# Les disciplines couvertes sont celles de la FFM : Trial, Enduro, Motocross,
# Vitesse, Rallyes routiers, et « Tourisme » — c'est cette derniere qui porte
# les randonnees. Toutes amenent du pilote identifiable sur sa machine, donc
# de la photo qui se vend.

FFM_URL = "https://carte.ffmoto.org/result.php"

# Les departements interroges par defaut : la maison, et de quoi grouper.
FFM_DEPTS = ["06", "83", "04", "13", "84"]

# Un seul passage de l'expression attrape l'adresse, les coordonnees, puis la
# bulle d'information qui porte le reste. L'ordre des champs est celui du
# code de la page, qui construit chaque marqueur a la suite.
_FFM_BLOC = re.compile(
    r'var address = \{"address":"(?P<adresse>[^"]*)"\};\s*'
    r'var coords = \{"latitude":"(?P<lat>[^"]*)","longitude":"(?P<lon>[^"]*)"\};'
    r'.*?iw-title">(?P<ville>[^<]*)<.*?<strong>(?P<discipline>[^<]*)</strong>'
    r".*?Du (?P<d1>\d{2}/\d{2}/\d{4}) au (?P<d2>\d{2}/\d{2}/\d{4})<br>(?P<club>[^<']*)",
    re.S)

# « DES PINS, 06130, Grasse, FRANCE » -> le code postal du CLUB, pas du lieu.
_FFM_CP = re.compile(r",\s*(\d{5})\s*,")


def _ffm_interroger(dept, discipline="0"):
    donnees = urllib.parse.urlencode({
        "nature_recherche": "evenement",
        "discipline_recherche": discipline,
        "ligue_recherche": "0",
        "type_recherche_date": "0",
        "date_recherche": "",
        "departement_recherche": dept,
        "geolocalisation": "",
    }).encode()
    req = urllib.request.Request(FFM_URL, data=donnees,
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as rep:
        return rep.read().decode("utf-8", "replace")


def _ffm_date(jjmmaaaa):
    j, m, a = jjmmaaaa.split("/")
    return "%s-%s-%s" % (a, m, j)


def from_ffm(config, source_name):
    """Les evenements motorises de la FFM, departement par departement."""
    depts = (config or {}).get("departements") or FFM_DEPTS
    out, vus = [], set()
    for dept in depts:
        try:
            page = _ffm_interroger(dept)
        except (urllib.error.URLError, urllib.error.HTTPError, OSError):
            continue
        for m in _FFM_BLOC.finditer(page):
            e = m.groupdict()
            debut = _ffm_date(e["d1"])
            # Le club organisateur revient d'un departement a l'autre quand il
            # roule a cheval sur deux : on ne garde qu'une fiche par date+lieu.
            cle = (debut, (e["ville"] or "").strip().lower())
            if cle in vus:
                continue
            vus.add(cle)
            club = (e["club"] or "").strip().rstrip("\\").strip()
            cp = _FFM_CP.search(e["adresse"] or "")
            # Le lieu annonce est souvent un nom de site — « KARTING DE
            # MANOSQUE », « Route de la Colle, 84400 GIGNAC ». La commune y
            # est pourtant ecrite : on la cherche dans la chaine avant de
            # renoncer. Le nom du site reste le lieu-dit.
            lieu = (e["ville"] or "").strip()
            commune, cp_lieu, dept_lieu = lieu, None, None
            from . import lieux as _lieux
            direct = _lieux.resoudre(ville=lieu)
            if direct.get("commune") and not direct.get("conflit"):
                commune = direct["commune"]
                cp_lieu, dept_lieu = direct.get("cp"), direct.get("dept")
            else:
                # On restreint aux departements interroges : « Gignac »
                # existe dans le 34, le 46 et le 84, et sur la liste large le
                # referentiel refuse — a juste titre — de trancher. Ramene a
                # la zone de travail, il n'y a plus qu'un Gignac.
                trouvee = _lieux.commune_dans_texte(lieu, depts_probables=depts)
                if trouvee and trouvee.get("commune"):
                    commune = trouvee["commune"]
                    cps = trouvee.get("cps") or []
                    # Un seul code postal : c'est le bon. Plusieurs (une ville
                    # a arrondissements) : on ne choisit pas au hasard.
                    cp_lieu = cps[0] if len(cps) == 1 else None
                    dept_lieu = trouvee.get("dept")
            out.append({
                "name": "%s - %s" % (e["discipline"].strip(), lieu),
                "sport": e["discipline"].strip(),
                "start_date": debut,
                "end_date": _ffm_date(e["d2"]),
                "venue": lieu,
                "city": commune,
                # Code postal et departement du LIEU, tels que le referentiel
                # des communes les a etablis — jamais ceux du club.
                "postal_code": cp_lieu,
                "dept": dept_lieu,
                # Ni le code postal ni le departement ne designent le LIEU de
                # l'epreuve : ce sont ceux du club. La carte filtre d'ailleurs
                # sur le club — « Karting de Manosque », dans le 04, remonte
                # sous le 06 parce que le Moto Club de Cagnes l'organise. Les
                # poser ici referait l'erreur de Saint-Remy (27 km affiches
                # pour 224). On laisse le referentiel des communes trancher a
                # partir du nom du lieu, et dire « a confirmer » s'il ne sait
                # pas — ce qui coupe les brouillons, et c'est voulu.
                "organizer": club,
                "url": "https://carte.ffmoto.org/",
                "source": source_name,
                "description": ("%s organise par %s. Siege du club : %s. "
                                "Releve sur la carte de la Federation Francaise "
                                "de Motocyclisme."
                                % (e["discipline"].strip(), club or "un club FFM",
                                   (e["adresse"] or "").strip())),
            })
    return out


def from_retrocalage(text, source_name):
    out = []
    for brut in _RETRO_FICHE.findall(text or ""):
        try:
            e = json.loads(brut)
        except ValueError:
            continue
        titre = (e.get("titre") or "").strip()
        debut = (e.get("debut") or "")[:10]
        if not titre or not debut:
            continue
        dep = e.get("departement") or {}
        tel = e.get("mobile") or e.get("fixe")
        # On recolle le contact dans la description : le depouillement sait
        # deja en tirer un numero et une adresse pour la fiche du club.
        bouts = [e.get("description") or ""]
        for etiquette, valeur in (("Organisateur", e.get("organisateur")),
                                  ("Contact", e.get("responsable")),
                                  ("Telephone", tel), ("Email", e.get("email")),
                                  ("Site", e.get("site"))):
            if valeur:
                bouts.append("%s : %s" % (etiquette, valeur))
        out.append({
            "name": titre[:200],
            "start_date": _as_date(debut),
            "end_date": _as_date((e.get("fin") or debut)[:10]),
            "city": e.get("ville"),
            "venue": e.get("lieu"),
            "dept": dep.get("numero"),
            "organizer": e.get("organisateur"),
            "sport": "Sport auto",
            "url": "https://retrocalage.com/evenement/%s" % e.get("reference", ""),
            "external_id": str(e.get("id") or ""),
            "description": "\n".join(b for b in bouts if b)[:1500],
            "source": source_name,
        })
    return out


def save_tournaments(records, source_name=None, default_sport=None):
    """Ecrit en base, en ignorant les doublons.

    Renvoie (nouveaux, maj, deja_connus, hors_perimetre). Les fiches ecartees
    pour cause de distance sont comptees a part : les melanger aux doublons
    faisait dire au journal « deja connu » pour des fiches jamais vues.
    """
    created, updated, skipped, ecartes = [], 0, 0, 0
    now = util.now_iso()
    for rec in records:
        rec = {k: v for k, v in rec.items() if v not in (None, "")}
        if not rec.get("name"):
            skipped += 1
            continue
        postal = rec.get("postal_code")
        rec["dept"] = rec.get("dept") or util.dept_from_postal(postal)
        rec["sport"] = rec.get("sport") or default_sport
        rec["source"] = rec.get("source") or source_name
        rec["fingerprint"] = fingerprint(rec)
        rec["updated_at"] = now

        # Le trajet decide de tout : on le calcule des l'entree en base, comme
        # le fait deja le depouillement de flyer.
        # Sans code postal, la table de distances rapproche les noms de facon
        # souple — « Saint-Remy-de-Provence » y a trouve un homonyme du coin et
        # s'est affiche a 27 km au lieu de 224. On demande donc d'abord au
        # referentiel le code postal exact, qui porte le departement avec lui.
        if not postal and rec.get("city"):
            from . import lieux
            res = lieux.resoudre(ville=rec.get("city"), dept=rec.get("dept"))
            if res.get("cp") and not res.get("conflit"):
                postal = res["cp"]
                rec["postal_code"] = postal
                rec["dept"] = rec.get("dept") or res.get("dept")

        if rec.get("distance_km") is None and (rec.get("city") or postal):
            d = geo.depuis_cannes(rec.get("city"), postal)
            if d:
                rec["distance_km"] = d.get("km")
                rec["trajet_min"] = d.get("minutes")
                rec["distance_verdict"] = d.get("verdict")
        # Et depuis Hyeres, le second point d'appui : un evenement a vingt
        # minutes d'Hyeres se fait, meme s'il est a deux heures de Cannes.
        if rec.get("hyeres_min") is None and (rec.get("city") or postal):
            rec["hyeres_min"] = geo.minutes_depuis_hyeres(rec.get("city"), postal)

        # Le genre et la note photo, si personne ne les a poses : une fiche
        # venue d'un agenda generaliste doit dire ce qu'elle vaut a vendre.
        if not rec.get("genre"):
            from . import rassemblements
            c = rassemblements.classer(
                " ".join(str(rec.get(k) or "") for k in ("name", "description")))
            if c["genre"]:
                rec["genre"] = c["genre"]
                rec["genre_libelle"] = c["libelle"]
                rec["note_photo"] = c["note"]

        if rec.get("description"):
            rec["description"] = sans_html(rec["description"])[:1500] or None

        # Un lieu qui n'est qu'une etiquette de formulaire vaut un lieu absent.
        for champ in ("city", "venue"):
            if lieu_placeholder(rec.get(champ)):
                rec.pop(champ, None)

        # Faute de lieu declare, on cherche la commune dans le titre : « Cross
        # UNSS au Parc de Fontmerle (Mougins) » la porte, l'agenda non.
        if not rec.get("city") and not rec.get("postal_code"):
            from . import lieux
            # Titre et lieu seulement. Cherchee dans la description, la commune
            # se trouve toujours : un texte de randonnee a fait situer deux
            # sorties du Var a Gray, en Haute-Saone, a 686 km.
            # On donne la region d'ou vient la fiche : les departements de
            # travail, plus celui du club organisateur s'il est connu.
            probables = depts_plausibles(rec.get("dept"))
            trouve = lieux.commune_dans_texte(
                " ".join(str(rec.get(k) or "") for k in ("name", "venue")),
                depts_probables=probables)
            if trouve:
                rec["city"] = trouve["commune"]
                rec["postal_code"] = (trouve.get("cps") or [None])[0]
                rec["dept"] = rec.get("dept") or trouve.get("dept")

        # Le nombre d'equipes se lit dans la description, et nulle part
        # ailleurs : « 32 equipes 7 matchs de 10 minutes ». Sans lui, un
        # tournoi national ne peut pas etre juge gros ou petit, et tout le
        # travail de tri qui suit se fait a l'aveugle.
        if not rec.get("nb_equipes") and rec.get("description"):
            from .flyer import _equipes as _lire_equipes
            n = _lire_equipes(str(rec["description"]))
            if n:
                rec["nb_equipes"] = n

        # La vie interne d'un club — entrainements, AG, anniversaires — n'a
        # rien a faire dans une veille de tournois.
        if vaut_le_deplacement(rec) is False:
            skipped += 1
            continue

        # La corroboration du lieu se pose a l'ENTREE, pas apres coup. Je l'avais
        # d'abord calculee en repassant la base : les fiches deja presentes
        # etaient donc marquees, mais toute nouvelle fiche entrait sans controle
        # — exactement le trou par lequel « Vedene » etait devenu « Vidauban ».
        if rec.get("lieu_verifie") is None:
            verdict, _ = lieu_corrobore(rec)
            if verdict is not None:
                rec["lieu_verifie"] = 1 if verdict else 0

        # Trop loin et hors bassin : la fiche n'entre meme pas en base, sinon
        # le prochain scan la ramenerait indefiniment.
        # Une source nationale ne rapporte que ses GROS plateaux. La premiere
        # passe sur la Belgique a fait entrer soixante et onze tournois U6-U9
        # a mille kilometres : aucun n'annonce son nombre d'equipes, et une
        # distance inconnue ne suffit pas a ecarter une fiche. La regle est
        # donc inverse pour ces sources-la — sans un gros plateau annonce, on
        # ne garde rien. C'est tout ce qu'on leur demande.
        if _gros_seulement(source_name):
            try:
                assez = int(rec.get("nb_equipes") or 0) >= GROS_PLATEAU
            except (TypeError, ValueError):
                assez = False
            if not assez:
                ecartes += 1
                continue

        # On ne jette plus une fiche parce qu'elle est loin. Julien recrute
        # des photographes partout en France : un tournoi a Lille n'est pas
        # pour lui, il est pour quelqu'un qu'on va trouver la-bas. La fiche
        # entre, marquee « reseau », et vit dans l'ecran « L'equipe ».
        # Ce qui est a sa portee garde `pour_moi`, et les ecrans de prospection
        # ne montrent que celui-la.
        rec["portee"] = portee_de(rec)

        # Le club organisateur, lu dans le nom faute d'etre publie par la
        # source. Sans lui, la fiche arrive muette : aucun message a rediger.
        if not rec.get("club_id"):
            nom_club = club_du_titre(rec.get("name"), rec.get("city"))
            if nom_club:
                from . import repo
                cid, _ = repo.upsert_club({
                    "name": nom_club, "sport": rec.get("sport"),
                    "city": rec.get("city"), "postal_code": postal,
                    "dept": rec.get("dept"), "source": rec.get("source"),
                    "source_url": rec.get("url"),
                })
                if cid:
                    rec["club_id"] = cid
                    rec.setdefault("organizer", nom_club)

        existing = db.q1("SELECT * FROM tournaments WHERE fingerprint = ?",
                         (rec["fingerprint"],))
        if not existing and rec.get("external_id"):
            existing = db.q1("SELECT * FROM tournaments WHERE external_id = ? AND source = ?",
                             (rec["external_id"], rec["source"]))
        if existing:
            patch = {k: v for k, v in rec.items()
                     if k not in ("id", "discovered_at", "status") and not existing[k]}
            if patch:
                patch["updated_at"] = now
                db.update("tournaments", existing["id"], patch)
                updated += 1
            else:
                skipped += 1
            continue
        rec["discovered_at"] = now
        rec.setdefault("status", "new")
        rec["id"] = db.insert("tournaments", rec)
        created.append(rec)
    return created, updated, skipped, ecartes


def _pages_suivantes(premier, src, config, maximum=8):
    """Lit les pages suivantes d'une API qui annonce combien elle en a.

    Sans ca, une source de cent tournois n'en rend que les cinquante premiers,
    et personne ne s'en apercoit : la veille dit « 50 nouveaux » et parait
    avoir fait son travail.
    """
    champ = (config or {}).get("pages") or "total_pages"
    try:
        total = int(_dig(json.loads(premier), champ) or 1)
    except (ValueError, TypeError):
        return []
    if total <= 1:
        return []

    out = []
    for page in range(2, min(total, maximum) + 1):
        url = src["url"] + ("&" if "?" in src["url"] else "?") + "page=%d" % page
        try:
            out += from_json(fetch(url), src["name"], config)
        except Exception:
            break          # une page qui casse ne doit pas perdre les precedentes
    return out


# --------------------------------------------------------------------------
# OpenAgenda : agendas publics de villes et d'associations
# --------------------------------------------------------------------------

# Cannes, d'ou l'on mesure tout.
CANNES_LAT, CANNES_LNG = 43.5528, 7.0174

# L'export JSON historique ne demande pas de cle. OpenAgenda le dit « legacy »
# et pousse vers son API authentifiee, mais il repond et suffit ici.
OA_EXPORT = "https://openagenda.com/agendas/%s/events.json"


def oa_uid(slug_ou_uid):
    """Un agenda se designe par son slug ; l'export veut son numero."""
    val = str(slug_ou_uid).strip().strip("/")
    if val.isdigit():
        return val
    page = fetch("https://openagenda.com/fr/" + val.split("/")[-1])
    trouves = sorted(set(re.findall(r"/agendas/(\d{4,})/", page)))
    return trouves[0] if trouves else None


def _oa_texte(valeur):
    """Les champs OpenAgenda sont multilingues : {'fr': '...', 'en': '...'}."""
    if isinstance(valeur, dict):
        return valeur.get("fr") or next(iter(valeur.values()), "") or ""
    return valeur or ""


# ---------------------------------------------------------------------------
# Le calendrier des clubs (portail.sportsregions.fr)
# ---------------------------------------------------------------------------
# Neuf mille deux cent soixante-quinze evenements a venir, trois cent cinq
# sports, publies par les clubs eux-memes. C'est la reponse a « les tournois
# de tennis, de danse, de karate, tous les sports auxquels je ne pense meme
# pas » : aucune federation ne couvre tout, aucun groupe Facebook non plus,
# mais des milliers de clubs tiennent leur agenda sur cette plateforme et le
# portail les rassemble.
#
# Les pages portent des microdonnees schema.org : date, nom, ville, code
# postal, lien. On lit ce qui est balise, jamais ce qu'on devine.
import html as _html

SR_BLOC = re.compile(
    r'itemprop="startDate"\s+content="([^"]+)".*?'
    r'<span class="red-article">([^<]*)</span>.*?'
    r'itemprop="addressLocality">([^<]*)<.*?'
    r'itemprop="postalCode"[^>]*>([^<]*)<.*?'
    r'<a[^>]+href="([^"]+)"[^>]*itemprop="url"[^>]*>'
    r'<span itemprop="name"[^>]*>([^<]*)</span>',
    re.S)


def from_sportsregions(text, source_name, config=None):
    """Le calendrier public des clubs, toutes disciplines."""
    out = []
    for m in SR_BLOC.finditer(text or ""):
        debut, sport, ville, cp, url, nom = (
            _html.unescape(x).strip() for x in m.groups())
        if not nom or not debut:
            continue
        # Le portail affiche le departement demande a cote de la ville, meme
        # quand le code postal dit autre chose — « Marcilly-sur-Eure (06) »
        # avec un 27810. C'est le code postal qui fait foi.
        out.append({
            "name": nom[:200],
            "start_date": debut[:10],
            "city": ville or None,
            "postal_code": cp or None,
            "dept": util.dept_from_postal(cp) if cp else None,
            "sport": sport or None,
            "url": url or None,
            "external_id": (url or "").rsplit("-", 1)[-1] or None,
            "source": source_name,
        })
    return out


def sr_pages(url, maximum=4):
    """Le calendrier d'un departement, page apres page."""
    pages = [url]
    for n in range(2, maximum + 1):
        pages.append("%s%spage=%d" % (url, "&" if "?" in url else "?", n))
    return pages


def from_openagenda(text, source_name):
    """Transforme l'export JSON d'un agenda en fiches."""
    try:
        data = json.loads(text)
    except ValueError:
        return []
    out = []
    for e in (data.get("events") or []):
        titre = _oa_texte(e.get("title")).strip()
        if not titre:
            continue
        lieu = e.get("location") or {}
        debut = e.get("firstTiming") or e.get("firstDate") or ""
        if isinstance(debut, dict):
            debut = debut.get("begin") or debut.get("start") or ""
        fin = e.get("lastTiming") or e.get("lastDate") or ""
        if isinstance(fin, dict):
            fin = fin.get("end") or fin.get("begin") or ""
        out.append({
            "name": titre[:200],
            "start_date": _as_date(str(debut)[:10]),
            "end_date": _as_date(str(fin)[:10]),
            "city": lieu.get("city"),
            "postal_code": lieu.get("postalCode"),
            "venue": lieu.get("name") or lieu.get("address"),
            "url": _oa_texte(e.get("canonicalUrl")) or e.get("originAgenda", {}).get("url"),
            "external_id": str(e.get("uid") or ""),
            "description": _oa_texte(e.get("longDescription"))[:1500]
                           or _oa_texte(e.get("description"))[:1500] or None,
            "source": source_name,
        })
    return out


def oa_url(uid, rayon_km=None, page=1):
    """L'adresse d'export, centree sur Cannes et limitee au rayon utile."""
    rayon = rayon_km or int(db.get_setting("oa_rayon_km", "120") or 120)
    params = [
        ("oaq[lat]", CANNES_LAT), ("oaq[lng]", CANNES_LNG),
        ("oaq[radius]", rayon), ("oaq[passed]", 0),
        ("oaq[order]", "upcoming"), ("limit", 100),
    ]
    if page > 1:
        params.append(("page", page))
    import urllib.parse
    return (OA_EXPORT % uid) + "?" + urllib.parse.urlencode(params)


# ---------------------------------------------------------------------------
# DATAtourisme
# ---------------------------------------------------------------------------
# La base nationale des offices de tourisme : 3 683 evenements rien que pour
# PACA, remis a jour toutes les nuits. Elle sort de la plateforme « diffuseur »
# qui demande un compte — mais le MEME contenu est publie chaque jour en libre
# acces sur data.gouv.fr, sans compte, sans cle, decoupe par region.
#
# C'est par la qu'on passe : la plateforme diffuseur doit fermer en 2027, et un
# fichier ouvert ne se perime pas le jour ou un identifiant expire.
#
# L'adresse du fichier change chaque nuit (elle porte la date dans son chemin),
# on la redemande donc a l'API de data.gouv a chaque passage.
DT_JEU = "5b598be088ee387c0c353714"
DT_FICHIER = "datatourisme-reg-pac.csv"
DT_DEPTS = ("06", "83", "04", "84")

# Combien de fiches douteuses on soumet au modele local par passage.
# A deux secondes piece, cinquante tiennent dans la routine du matin.
RELECTURE_MAX = 50


def dt_adresse(fichier=None):
    """L'adresse du jour pour l'export regional."""
    data = json.loads(fetch("https://www.data.gouv.fr/api/1/datasets/%s/" % DT_JEU))
    voulu = fichier or DT_FICHIER
    for r in data.get("resources", []):
        if (r.get("title") or "") == voulu:
            return r.get("url")
    raise ValueError("export %s introuvable sur data.gouv.fr" % voulu)


def _dt_dates(periodes):
    """« 2026-05-08<->2026-05-10 » -> ('2026-05-08', '2026-05-10')."""
    bout = (periodes or "").split(";")[0].strip()
    if "<->" in bout:
        a, b = bout.split("<->", 1)
        return _as_date(a.strip()[:10]), _as_date(b.strip()[:10])
    return _as_date(bout[:10]), None


def from_datatourisme(texte, source_name, config=None):
    """Les fetes et manifestations des offices de tourisme, par departement."""
    config = config or {}
    depts = tuple(config.get("depts") or DT_DEPTS)
    out = []
    lecteur = csv.DictReader(io.StringIO(texte))
    for row in lecteur:
        cats = row.get("Categories_de_POI") or ""
        if "EntertainmentAndEvent" not in cats:
            continue                      # un hotel ou un restaurant n'est pas une date
        cp_commune = (row.get("Code_postal_et_commune") or "").strip()
        if cp_commune[:2] not in depts:
            continue
        cp, _, commune = cp_commune.partition("#")
        debut, fin = _dt_dates(row.get("Periodes_regroupees"))
        if not debut:
            continue
        nom = (row.get("Nom_du_POI") or "").strip()
        if not nom:
            continue
        # Le champ contacts tient les adresses web, precedees d'un dieze.
        contact = (row.get("Contacts_du_POI") or "").lstrip("#").split("#")[0].strip()
        out.append({
            "name": nom[:200],
            "start_date": debut,
            "end_date": fin,
            "city": commune.strip() or None,
            "postal_code": cp.strip() or None,
            "venue": (row.get("Adresse_postale") or "").strip() or None,
            "url": contact if contact.startswith("http") else (row.get("URI_ID_du_POI") or None),
            "external_id": (row.get("URI_ID_du_POI") or "").rsplit("/", 1)[-1] or None,
            "description": (row.get("Description") or "")[:1500] or None,
            "source": source_name,
        })
    return out


def run_source(source_id):
    """Lance une source et enregistre les tournois trouves."""
    row = db.q1("SELECT * FROM sources WHERE id = ?", (source_id,))
    if not row:
        return {"ok": False, "error": "source introuvable"}
    src = dict(row)
    config = {}
    if src.get("config"):
        try:
            config = json.loads(src["config"])
        except ValueError:
            config = {}
    try:
        if src["kind"] == "ffm":
            text = ""          # interroge plus bas, en POST, departement par departement
        elif src["kind"] == "openagenda":
            uid = oa_uid(src["url"])
            if not uid:
                raise ValueError("agenda introuvable : %s" % src["url"])
            text = fetch(oa_url(uid))
        elif src["kind"] == "datatourisme":
            # L'adresse du fichier change chaque nuit : on la redemande.
            text = fetch(dt_adresse(src.get("url") or None))
        elif src["kind"] == "sportsregions":
            # Le portail pagine par quinze. A quatre pages on ne lisait que
            # cent vingt evenements sur les mille cinq cents du secteur : on
            # monte a dix, et le reglage vit dans la configuration de la
            # source pour qu'un departement charge puisse aller plus loin.
            try:
                combien = int((config or {}).get("pages") or 10)
            except (TypeError, ValueError):
                combien = 10
            # Au-dela de la derniere page, le portail rend un 404. Ce n'est
            # pas une panne : c'est la fin de la liste. Une page manquante ne
            # doit pas faire echouer toute la source — les Alpes-Maritimes
            # etaient perdues entierement pour ca.
            morceaux = []
            for u in sr_pages(src["url"], max(1, min(30, combien))):
                try:
                    morceaux.append(fetch(u))
                except Exception:
                    break
            text = "".join(morceaux)
        else:
            text = fetch(src["url"])
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError) as exc:
        msg = "echec reseau : %s" % exc
        db.update("sources", source_id, {"last_run_at": util.now_iso(),
                                         "last_status": msg, "last_count": 0})
        db.log("source", "%s : %s" % (src["name"], msg))
        return {"ok": False, "error": msg}

    kind = src["kind"]
    try:
        if kind == "ics":
            records = from_ics(text, src["name"])
        elif kind == "rss":
            records = from_rss(text, src["name"])
        elif kind == "json":
            records = from_json(text, src["name"], config)
            records += _pages_suivantes(text, src, config)
        elif kind == "html":
            records = from_html(text, src["name"], config)
        elif kind == "csv":
            records = from_csv(text, src["name"])
        elif kind == "retrocalage":
            records = from_retrocalage(fetch(src["url"]), src["name"])
        elif kind == "ffm":
            records = from_ffm(config, src["name"])
        elif kind == "sportsregions":
            records = from_sportsregions(text, src["name"], config)
        elif kind == "openagenda":
            records = from_openagenda(text, src["name"])
        elif kind == "datatourisme":
            records = from_datatourisme(text, src["name"], config)
        else:
            records = []
    except Exception as exc:  # une source mal configuree ne doit pas tuer la veille
        msg = "echec analyse : %s" % exc
        db.update("sources", source_id, {"last_run_at": util.now_iso(),
                                         "last_status": msg, "last_count": 0})
        return {"ok": False, "error": msg}

    # Un agenda de commune rend surtout du theatre, des concerts et des marches.
    # Les verser tels quels dans la veille, c'est noyer les vrais tournois sous
    # cinquante lignes qui ne rapporteront jamais une photo. Les sources
    # marquees « filtre_photo » passent donc par le tri de photos.py, qui ne
    # garde que ce dont quelqu'un, sur place, voudra acheter l'image.
    laisses, repeches = 0, 0
    if config.get("filtre_photo"):
        gardes, rejets = photos.trier(records)
        records = [f for f, _ in gardes]
        # Deuxieme avis du modele local, uniquement sur ce que les mots-cles
        # n'ont PAS su classer. Un refus motive — « marche ou brocante »,
        # « spectacle professionnel » — n'est pas repris : la liste de mots a
        # raison sur ceux-la, et on ne va pas payer trois secondes pour le
        # verifier. Reste la pile « rien reconnu », ou se cachent les vrais.
        doute = [f for f, v in rejets
                 if v["refus"] == "rien qui annonce des gens a photographier"]
        laisses = len(rejets) - len(doute)
        from . import ia
        if doute and ia.disponible():
            for f in doute[:RELECTURE_MAX]:
                avis = ia.vend_des_photos(f.get("name"), f.get("description"))
                if avis and avis["vend"]:
                    records.append(f)
                    repeches += 1
                else:
                    laisses += 1
            laisses += max(0, len(doute) - RELECTURE_MAX)
        else:
            laisses += len(doute)

    # Le tri des plateaux, pour les sources qui le demandent dans leur
    # configuration. Il remplace le filtre « 32 equipes » la ou le nombre
    # d'equipes n'est jamais publie.
    sans_plateau = 0
    mini_cat = (config or {}).get("plateau_min_categories")
    if mini_cat:
        avant = len(records)
        records = [r for r in records if assez_de_categories(r, mini_cat)]
        sans_plateau = avant - len(records)

    created, updated, skipped, ecartes = save_tournaments(
        records, src["name"], src.get("sport"))
    status = "%d lu(s) : %d nouveau(x), %d complete(s), %d deja connu(s)" % (
        len(records), len(created), updated, skipped)
    if sans_plateau:
        status += ", %d sans plateau annonce" % sans_plateau
    if laisses:
        status += ", %d sans interet photo" % laisses
    if repeches:
        status += ", %d repeche(s) par le modele" % repeches
    if ecartes:
        status += ", %d hors perimetre" % ecartes
    db.update("sources", source_id, {"last_run_at": util.now_iso(),
                                     "last_status": status, "last_count": len(created)})
    db.log("source", "%s : %s" % (src["name"], status))
    return {"ok": True, "created": created, "n_created": len(created),
            "n_updated": updated, "n_skipped": skipped, "status": status}


def run_all(only_enabled=True):
    rows = db.q("SELECT id FROM sources WHERE enabled = 1" if only_enabled
                else "SELECT id FROM sources")
    results = []
    for row in rows:
        results.append(dict(run_source(row["id"]), source_id=row["id"]))
    return results
