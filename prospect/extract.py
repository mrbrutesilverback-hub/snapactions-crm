"""Transforme du texte brut (fil de forum, signatures, page club, tableau colle)
en fiches club/contact exploitables. Rien n'est ecrit en base ici : on renvoie
des candidats que l'humain valide dans l'ecran Import."""
import csv
import io
import re

from . import util

URL_RE = re.compile(r"\bhttps?://[^\s<>\"')\]]+", re.I)
BARE_DOMAIN_RE = re.compile(
    r"\b(?:www\.)[A-Za-z0-9\-.]+\.[A-Za-z]{2,}(?:/[^\s<>\"')\]]*)?", re.I)

ROLE_PATTERNS = [
    (r"\bpr[eé]sident(?:e)?\b", "Président"),
    (r"\bvice[- ]pr[eé]sident(?:e)?\b", "Vice-président"),
    (r"\bsecr[eé]taire(?:\s+g[eé]n[eé]ral(?:e)?)?\b", "Secrétaire"),
    (r"\btr[eé]sori[eè]r(?:e)?\b", "Trésorier"),
    (r"\bresponsable\s+(?:des?\s+)?(?:tournois?|comp[eé]titions?)\b", "Resp. tournois"),
    (r"\bresponsable\s+(?:de\s+la\s+)?communication\b", "Resp. communication"),
    (r"\bcommunication\b", "Resp. communication"),
    (r"\bresponsable\s+(?:des?\s+)?jeunes?\b", "Resp. jeunes"),
    (r"\bjuge[- ]arbitre\b", "Juge-arbitre"),
    (r"\b(?:entra[iî]neur|coach)\b", "Entraîneur"),
    (r"\bdirect(?:eur|rice)(?:\s+sportif(?:ve)?)?\b", "Directeur"),
    (r"\bmanager\b", "Manager"),
    (r"\bb[eé]n[eé]vole\b", "Bénévole"),
    (r"\bmembre\s+du\s+bureau\b", "Membre du bureau"),
]

CLUB_HINTS = re.compile(
    r"\b(?:club|association|asso\.?|amicale|entente|union|olympique|sporting|academy|"
    r"acad[eé]mie|ligue|comit[eé]|team|A\.?S\.?|U\.?S\.?|C\.?S\.?|E\.?S\.?|T\.?C\.?|"
    r"S\.?C\.?|F\.?C\.?|R\.?C\.?|complexe|centre)\b", re.I)

# Mots qui trahissent une ligne de forum plutot qu'un nom
NOISE = re.compile(
    r"\b(?:message|post[eé]|r[eé]pondre|citer|inscrit|membre depuis|messages?\s*:|"
    r"le \d{1,2}[/ ]|il y a \d+|re\s*:|cordialement|bien [aà] vous|merci d'avance)\b", re.I)

NAME_RE = re.compile(
    r"^[A-ZÀ-ÖØ-Þ][\w'’\-À-ÖØ-öø-ÿ]{1,20}"
    r"(?:\s+[A-ZÀ-ÖØ-Þ][\w'’\-À-ÖØ-öø-ÿ]{1,20}){1,2}$")

CITY_CONTEXT = re.compile(r"(\d{5})\s+([A-ZÀ-ÖØ-Þ][\w'’\-\s]{2,40})")


# --------------------------------------------------------------------------

def _split_blocks(text):
    """Decoupe en blocs : lignes vides, separateurs, ou changement d'auteur."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    chunks = re.split(r"\n\s*\n|^\s*[-=_*#~]{4,}\s*$", text, flags=re.M)
    return [c.strip() for c in chunks if c and c.strip()]


def _guess_role(block):
    for pattern, label in ROLE_PATTERNS:
        if re.search(pattern, block, re.I):
            return label
    return None


def _guess_person(block, emails):
    """Nom de personne : d'abord une ligne 'Prenom Nom', sinon depuis l'email.

    Gere aussi les signatures d'une seule ligne du type 'Camille Rousseau, Presidente'
    ou 'Thomas Garnier - Tresorier', tres frequentes sur les forums.
    """
    for raw in block.split("\n"):
        line = raw.strip(" \t-–—•*:;,")
        if not line or len(line) > 90 or NOISE.search(line):
            continue
        if "@" in line or URL_RE.search(line) or any(ch.isdigit() for ch in line):
            continue
        # On teste la ligne entiere, puis chacun de ses fragments (nom, fonction).
        candidates = [line] + [f.strip() for f in re.split(r"[,;\u2013\u2014|/]|\s-\s", line)]
        for cand in candidates:
            if not cand or len(cand) > 45 or CLUB_HINTS.search(cand):
                continue
            if NAME_RE.match(cand):
                return util.title_name(cand)
    for email in emails:
        if util.is_role_email(email):
            continue
        local = email.split("@")[0]
        parts = re.split(r"[._\-]+", local)
        parts = [p for p in parts if p.isalpha() and len(p) > 1]
        if len(parts) >= 2:
            return util.title_name(" ".join(parts[:2]))
    return None


def _clean_club_line(line):
    """Enleve le libelle de fonction colle devant/derriere le nom du club.

    'President - Tennis Club de Villeurbanne'  -> 'Tennis Club de Villeurbanne'
    'Secretaire generale, AS Padel Montpellier' -> 'AS Padel Montpellier'
    """
    cleaned = re.sub(r"\s{2,}", " ", line).strip()
    cleaned = re.sub(r"^(?:club|association|structure)\s*[:\-]\s*", "", cleaned, flags=re.I)
    for pattern, _label in ROLE_PATTERNS:
        # Fonction en tete, suivie d'un separateur
        cleaned = re.sub(r"^\s*" + pattern + r"[\w\s'’()-]{0,25}?\s*[,\-–—|:/]+\s*",
                         "", cleaned, flags=re.I)
        # Fonction en fin, precedee d'un separateur
        cleaned = re.sub(r"\s*[,\-–—|:/]+\s*" + pattern + r"[\w\s'’()-]{0,25}$",
                         "", cleaned, flags=re.I)
    cleaned = cleaned.strip(" \t-–—,;:|/")
    return cleaned if CLUB_HINTS.search(cleaned) else None


def _guess_club(block, emails, urls):
    """Nom du club : ligne avec un marqueur associatif, sinon domaine du site."""
    best = None
    for raw in block.split("\n"):
        line = raw.strip(" \t-–—•*:;,")
        if not line or len(line) > 70 or "@" in line or NOISE.search(line):
            continue
        if CLUB_HINTS.search(line):
            cleaned = _clean_club_line(line)
            if cleaned and (best is None or len(cleaned) > len(best)):
                best = cleaned
    if best:
        return best.strip()
    for url in urls:
        host = re.sub(r"^https?://", "", url).split("/")[0].lower()
        host = re.sub(r"^www\.", "", host)
        base = host.split(".")[0]
        if base and base not in ("facebook", "instagram", "google", "gmail", "linkedin",
                                 "twitter", "x", "helloasso", "wixsite", "wordpress"):
            return util.title_name(base.replace("-", " "))
    for email in emails:
        dom = util.email_domain(email) or ""
        base = dom.split(".")[0]
        if base and base not in ("gmail", "hotmail", "outlook", "yahoo", "orange", "free",
                                 "wanadoo", "laposte", "sfr", "bbox", "icloud", "live", "me"):
            return util.title_name(base.replace("-", " "))
    return None


def _guess_city(block):
    m = CITY_CONTEXT.search(block)
    if m:
        city = re.sub(r"\s{2,}", " ", m.group(2)).strip(" ,;.-")
        city = city.split("\n")[0].strip()
        return m.group(1), util.title_name(city[:40])
    postal = util.extract_postal(block)
    return postal, None


def extract_blob(text, source=None, source_url=None, default_sport=None):
    """Renvoie une liste de candidats {club, contact} depuis du texte libre."""
    out = []
    for block in _split_blocks(text or ""):
        emails = util.extract_emails(block)
        phones = util.extract_phones(block)
        if not emails and not phones:
            continue
        urls = URL_RE.findall(block) + ["http://" + u for u in BARE_DOMAIN_RE.findall(block)]
        urls = [u for u in urls if "mailto:" not in u]
        postal, city = _guess_city(block)
        person = _guess_person(block, emails)
        club = _guess_club(block, emails, urls)
        role = _guess_role(block)

        personal = [e for e in emails if not util.is_role_email(e)]
        club_emails = [e for e in emails if util.is_role_email(e)]
        mobiles = [p for p in phones if util.is_mobile_fr(p)]
        landlines = [p for p in phones if not util.is_mobile_fr(p)]

        first, last = "", ""
        if person:
            bits = person.split(" ", 1)
            first = bits[0]
            last = bits[1] if len(bits) > 1 else ""

        out.append({
            "club": {
                "name": club or (person and "%s (club a preciser)" % person) or "Club a identifier",
                "sport": default_sport,
                "city": city,
                "postal_code": postal,
                "dept": util.dept_from_postal(postal),
                "website": urls[0] if urls else None,
                "email": club_emails[0] if club_emails else None,
                "phone": landlines[0] if landlines else (phones[0] if phones else None),
                "source": source,
                "source_url": source_url,
            },
            "contact": {
                "first_name": first or None,
                "last_name": last or None,
                "role": role,
                "email": (personal[0] if personal else (emails[0] if emails else None)),
                "phone": mobiles[0] if mobiles else (phones[0] if phones else None),
                "phone2": (mobiles[1:2] or landlines[:1] or [None])[0],
                "source": source,
                "source_url": source_url,
            },
            "raw": block[:1200],
            "confidence": _confidence(club, person, emails, phones),
        })
    return out


def _confidence(club, person, emails, phones):
    score = 0
    if emails:
        score += 40
    if phones:
        score += 25
    if person:
        score += 20
    if club:
        score += 15
    return min(score, 100)


# --------------------------------------------------------------------------
# Import tabulaire (CSV / TSV / colle depuis un tableur)
# --------------------------------------------------------------------------

HEADER_MAP = {
    "club": "club_name", "nom du club": "club_name", "structure": "club_name",
    "association": "club_name", "organisation": "club_name", "club_name": "club_name",
    "nom": "last_name", "last_name": "last_name", "nom de famille": "last_name",
    "prenom": "first_name", "prénom": "first_name", "first_name": "first_name",
    "contact": "full_name", "nom complet": "full_name", "full_name": "full_name",
    "role": "role", "rôle": "role", "fonction": "role", "poste": "role", "titre": "role",
    "email": "email", "e-mail": "email", "mail": "email", "courriel": "email",
    "adresse email": "email", "adresse mail": "email",
    "tel": "phone", "tél": "phone", "telephone": "phone", "téléphone": "phone",
    "phone": "phone", "portable": "phone", "mobile": "phone", "gsm": "phone",
    "tel2": "phone2", "fixe": "phone2", "tel fixe": "phone2",
    "ville": "city", "city": "city", "commune": "city",
    "cp": "postal_code", "code postal": "postal_code", "postal": "postal_code",
    "zip": "postal_code", "postal_code": "postal_code",
    "dept": "dept", "departement": "dept", "département": "dept",
    "site": "website", "site web": "website", "website": "website", "url": "website",
    "web": "website", "lien": "source_url",
    "sport": "sport", "discipline": "sport", "activite": "sport",
    "source": "source", "origine": "source", "forum": "source",
    "note": "notes", "notes": "notes", "commentaire": "notes", "remarque": "notes",
    "statut": "status", "status": "status", "etat": "status",
    "adresse": "address", "address": "address",
    # Variantes rencontrees sur des fichiers reels
    "societe": "club_name", "structure": "club_name", "organisme": "club_name",
    "association": "club_name", "asso": "club_name", "equipe": "club_name",
    "activites": "sport", "activite": "sport", "activites principales": "sport",
    "president": "full_name", "presidente": "full_name", "responsable": "full_name",
    "referent": "full_name", "interlocuteur": "full_name", "personne": "full_name",
    "station": "city", "localite": "city",
    "reseaux": "website", "reseau": "website", "site internet": "website",
    "notes terrain": "notes", "note terrain": "notes", "observations": "notes",
    "commentaires": "notes", "remarques": "notes",
    "portable 2": "phone2", "tel 2": "phone2", "autre tel": "phone2",
}


def _header_variants(header):
    """Formes a essayer pour un en-tete : complet, sans parenthese, puis chaque
    partie separee par / ou |.

    'Contact (nom / role)' -> ['contact (nom / role)', 'contact']
    'Club / Association'   -> ['club / association', 'club', 'association']
    """
    key = util.strip_accents((header or "").strip().lower())
    key = re.sub(r"\s+", " ", key)
    if not key:
        return []
    out = [key]
    without_parens = re.sub(r"\([^)]*\)", " ", key)
    without_parens = re.sub(r"\s+", " ", without_parens).strip(" :-")
    if without_parens and without_parens not in out:
        out.append(without_parens)
    for part in re.split(r"\s*[/|]\s*", without_parens or key):
        part = part.strip(" :-")
        if part and part not in out:
            out.append(part)
    return out


def _looks_like_roles(values):
    """True si une colonne contient surtout des fonctions ('President') plutot
    que des noms de personnes ('T. Gueguen')."""
    sample = [v for v in values if str(v).strip()][:20]
    if not sample:
        return False
    hits = sum(1 for v in sample
               if any(re.search(pat, str(v), re.I) for pat, _l in ROLE_PATTERNS))
    return hits > len(sample) / 2


def sniff_table(text):
    """Detecte le delimiteur et renvoie (entetes, lignes)."""
    text = (text or "").strip("\n")
    if not text:
        return [], []
    sample = "\n".join(text.split("\n")[:20])
    counts = {d: sample.count(d) for d in ("\t", ";", ",", "|")}
    delim = max(counts, key=counts.get)
    if counts[delim] == 0:
        return [], []
    reader = csv.reader(io.StringIO(text), delimiter=delim)
    rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not rows:
        return [], []
    return rows[0], rows[1:]


def map_headers(headers, rows=None):
    """Entetes brutes -> champs internes ({index: champ}).

    Si `rows` est fourni, une colonne de personne dont le contenu est surtout
    des fonctions ('President', 'Presidente') est rattachee a `role` plutot qu'a
    `full_name` : le meme en-tete n'a pas le meme sens d'un fichier a l'autre.
    """
    mapping, taken = {}, set()
    for i, h in enumerate(headers):
        field = None
        for variant in _header_variants(h):
            field = HEADER_MAP.get(variant)
            if field:
                break
        if not field or field in taken:
            continue
        if field == "full_name" and rows:
            column = [r[i] for r in rows if len(r) > i]
            if _looks_like_roles(column):
                if "role" in taken:
                    continue
                field = "role"
        mapping[i] = field
        taken.add(field)
    return mapping


# Champs cibles proposes dans l'ecran de mappage des colonnes.
IMPORT_FIELDS = [
    ("club_name", "Club"),
    ("first_name", "Prenom"),
    ("last_name", "Nom"),
    ("full_name", "Nom complet"),
    ("role", "Fonction"),
    ("email", "Email"),
    ("phone", "Telephone"),
    ("phone2", "Telephone 2"),
    ("city", "Ville"),
    ("postal_code", "Code postal"),
    ("address", "Adresse"),
    ("website", "Site web"),
    ("sport", "Discipline"),
    ("source", "Source"),
    ("source_url", "Lien source"),
    ("notes", "Notes"),
    ("status", "Statut"),
]


def split_person(value):
    """Decoupe une case 'contact' telle qu'on la remplit sur le terrain.

    'Stephanie (presidente)'  -> prenom Stephanie, fonction President
    'President'               -> pas de nom, fonction President
    'Marion (com) ; Jeremy P' -> premiere personne, le reste en note
    'Maitre El Kellali (7e dan)' -> nom complet, parenthese gardee en note
    """
    raw = str(value or "").strip()
    if not raw:
        return {}
    segments = [seg.strip() for seg in re.split(r"[;/]|\bet\b(?=\s+[A-Z])", raw)
                if seg.strip()]
    person = segments[0] if segments else raw
    note = ("Aussi : " + " ; ".join(segments[1:])) if len(segments) > 1 else None

    role, leftover = None, []
    def _take(match):
        inner = match.group(1).strip()
        found = next((lbl for pat, lbl in ROLE_PATTERNS if re.search(pat, inner, re.I)),
                     None)
        if found:
            role_holder.append(found)
        elif inner:
            leftover.append(inner)
        return " "
    role_holder = []
    person = re.sub(r"\(([^)]*)\)", _take, person)
    if role_holder:
        role = role_holder[0]
    person = re.sub(r"\s{2,}", " ", person).strip(" \t,;:-")

    # La case ne contient qu'une fonction : pas de nom a inventer.
    bare_role = next((lbl for pat, lbl in ROLE_PATTERNS
                      if re.fullmatch(r"\W*" + pat + r"\W*", person, re.I)), None)
    if bare_role:
        return {"role": role or bare_role,
                "notes": " | ".join([n for n in ([note] + leftover) if n]) or None}
    out = {"role": role,
           "notes": " | ".join([n for n in ([note] + leftover) if n]) or None}
    if person:
        bits = util.title_name(person).split(" ", 1)
        out["first_name"] = bits[0]
        if len(bits) > 1:
            out["last_name"] = bits[1]
    return out


def normalize_role(value):
    """Valide une case 'fonction'. Renvoie (fonction reconnue, reste en note).

    Une colonne 'Role / fonction' contient souvent du commentaire terrain
    ('numero du president recupere', '7e dan'). Ce qui n'est pas une fonction
    connue part en note plutot que de polluer le champ.
    """
    raw = str(value or "").strip()
    if not raw:
        return None, None
    label = next((lbl for pat, lbl in ROLE_PATTERNS if re.search(pat, raw, re.I)), None)
    if not label:
        return None, raw
    # La fonction est reconnue ; si la case dit plus que ca, on garde le reste.
    trimmed = raw
    for pat, lbl in ROLE_PATTERNS:
        if lbl == label:
            trimmed = re.sub(pat, " ", trimmed, flags=re.I)
    trimmed = re.sub(r"\s{2,}", " ", trimmed).strip(" \t,;:()-·|")
    return label, (trimmed or None)


def row_to_candidate(rec, source=None, default_sport=None, raw=None):
    """Un dict de champs deja mappes -> un candidat {club, contact}."""
    rec = {k: (v.strip() if isinstance(v, str) else v)
           for k, v in rec.items() if v not in (None, "")}
    notes_extra = []
    # La case 'fonction' n'en est pas toujours une : on ne garde que le reconnu.
    column_role, role_note = normalize_role(rec.pop("role", None))
    if role_note:
        notes_extra.append(role_note)

    full = rec.pop("full_name", None)
    parsed_role = None
    if full and not rec.get("first_name"):
        parsed = split_person(full)
        for key in ("first_name", "last_name"):
            if parsed.get(key):
                rec[key] = parsed[key]
        parsed_role = parsed.get("role")
        if parsed.get("notes"):
            notes_extra.append(parsed["notes"])
    rec["role"] = column_role or parsed_role
    if notes_extra:
        rec["contact_notes"] = " · ".join(notes_extra)
    postal = util.extract_postal(rec.get("postal_code") or "") \
        or util.extract_postal(rec.get("address") or "") \
        or util.extract_postal(rec.get("city") or "")
    email = util.normalize_email(rec.get("email"))
    phone = util.normalize_phone(rec.get("phone"))
    phone2 = util.normalize_phone(rec.get("phone2"))
    if phone2 and phone2 == phone:
        phone2 = None
    if not phone and phone2:
        phone, phone2 = phone2, None
    city = rec.get("city")
    if city:
        city = re.sub(r"\d{5}", "", city).strip(" ,-") or None
    return {
        "club": {
            "name": rec.get("club_name") or None,
            "sport": rec.get("sport") or default_sport,
            "city": util.title_name(city) if city else None,
            "postal_code": postal,
            "dept": util.dept_from_postal(postal),
            "website": rec.get("website"),
            "address": rec.get("address"),
            "email": email if (email and util.is_role_email(email)) else None,
            "phone": phone2 or (phone if (phone and not util.is_mobile_fr(phone)) else None),
            "notes": rec.get("notes"),
            "source": rec.get("source") or source,
            "source_url": rec.get("source_url"),
            "status": rec.get("status"),
        },
        "contact": {
            "first_name": util.title_name(rec["first_name"]) if rec.get("first_name") else None,
            "last_name": util.title_name(rec["last_name"]) if rec.get("last_name") else None,
            "role": rec.get("role"),
            "email": email,
            "phone": phone,
            "phone2": phone2,
            "notes": rec.get("contact_notes"),
            "source": rec.get("source") or source,
            "source_url": rec.get("source_url"),
        },
        "raw": raw or " | ".join(str(v) for v in rec.values()),
        "confidence": 90 if (email or phone) else 55,
    }


def extract_rows(headers, rows, mapping=None, source=None, default_sport=None):
    """Lignes deja tabulees + mappage {index_colonne: champ} -> candidats."""
    if mapping is None:
        mapping = map_headers(headers, rows)
    mapping = {int(k): v for k, v in mapping.items() if v}
    out = []
    for row in rows:
        rec = {}
        for i, field in mapping.items():
            if i < len(row):
                value = row[i]
                if value not in (None, ""):
                    rec[field] = str(value)
        if not rec:
            continue
        raw = " | ".join(str(c) for c in row if str(c).strip())
        candidate = row_to_candidate(rec, source, default_sport, raw)
        if not (candidate["contact"]["email"] or candidate["contact"]["phone"]
                or candidate["contact"]["last_name"] or candidate["contact"]["first_name"]
                or rec.get("club_name")):
            continue
        out.append(candidate)
    return out


def extract_table(text, source=None, default_sport=None):
    """Import d'un tableau colle. Renvoie la meme structure que extract_blob."""
    headers, rows = sniff_table(text)
    if not headers:
        return []
    mapping = map_headers(headers, rows)
    if not mapping:
        return []
    return extract_rows(headers, rows, mapping, source, default_sport)


def extract_any(text, source=None, source_url=None, default_sport=None, mode="auto"):
    """Point d'entree unique : devine tableau ou texte libre."""
    if mode == "table":
        return extract_table(text, source, default_sport)
    if mode == "blob":
        return extract_blob(text, source, source_url, default_sport)
    headers, rows = sniff_table(text)
    if headers and rows and map_headers(headers, rows):
        return extract_table(text, source, default_sport)
    return extract_blob(text, source, source_url, default_sport)


# --------------------------------------------------------------------------
# Import d'un calendrier personnel (agenda de travail, pas source de veille)
# --------------------------------------------------------------------------

# Mots qui trahissent une prise de contact plutot qu'un evenement a couvrir.
LEAD_WORDS = re.compile(
    r"\b(?:rdv|rendez[- ]vous|call|appel|appeler|telephoner|t[ée]l[ée]phoner|relance|"
    r"relancer|contact|contacter|devis|proposition|r[ée]union|meeting|visio|point|"
    r"prospect|demo|d[ée]mo|presentation|pr[ée]sentation|signature|contrat)\b", re.I)

EVENT_WORDS = re.compile(
    r"\b(?:tournoi|tournois|cup|coupe|open|championnat|champions|match|comp[ée]tition|"
    r"finale|gala|festival|forum|salon|spectacle|shoot|shooting|tournage|d[ée]tection|"
    r"stage|meeting|trophy|troph[ée]e|derby|challenge|kid|u\d{1,2})\b", re.I)

# Bruit a retirer en tete de titre pour retrouver le nom de la structure.
TITLE_NOISE = re.compile(
    r"^\s*(?:rdv|rendez[- ]vous|call|appel|relance|contact|devis|r[ée]union|visio|"
    r"point|photo\s*shoot|shooting|tournage|shoot)\b[\s:,\-–—]*"
    r"(?:pour|avec|chez|le|la|les|du|de|d')?[\s:,\-–—]*", re.I)

CLUB_WORDS = re.compile(
    r"\b(?:club|association|asso|amicale|entente|union|olympique|sporting|academy|"
    r"acad[ée]mie|ligue|comit[ée]|team|nautique|gymnase|complexe|centre|stade|"
    r"A\.?S\.?|U\.?S\.?|C\.?S\.?|E\.?S\.?|T\.?C\.?|F\.?C\.?|R\.?C\.?)\b", re.I)

SPORT_WORDS = {
    "foot": "football", "football": "football", "basket": "basket", "hand": "handball",
    "handball": "handball", "rugby": "rugby", "tennis": "tennis", "padel": "padel",
    "voile": "voile", "catamaran": "voile", "nautique": "nautique", "yatching": "voile",
    "yachting": "voile", "escalade": "escalade", "kravmaga": "krav maga",
    "krav": "krav maga", "drift": "sport auto", "danse": "danse", "judo": "judo",
    "natation": "natation", "athl": "athletisme", "volley": "volley", "golf": "golf",
}


def _clean_event_title(title):
    """'CALL POUR TOURNOIS LORIOL +33 7 86 97 02 92' -> 'TOURNOIS LORIOL'."""
    s = (title or "").strip()
    for pattern in (util.PHONE_RE, util.PHONE_INTL_RE, util.EMAIL_RE):
        s = pattern.sub(" ", s)
    # Plusieurs marqueurs peuvent s'empiler : "Rdv call avec ..."
    for _ in range(4):
        stripped = TITLE_NOISE.sub("", s)
        if stripped == s:
            break
        s = stripped
    s = re.sub(r"\s*\|\s*$", "", s)
    s = re.sub(r"\s*\(([^)]*)\)\s*$", "", s)      # parenthese finale = precision
    s = re.sub(r"\b\d+\s*[ée]quipes?\b", "", s, flags=re.I)
    s = re.sub(r"\b\d+\s*personnes?\b", "", s, flags=re.I)
    s = re.sub(r"\s{2,}", " ", s)
    s = s.strip(" \t:,-–—|")
    # Si le nettoyage a trop mange (il ne reste qu'un mot la ou le titre en avait
    # plusieurs), on garde le titre d'origine : mieux vaut trop que faux.
    original = (title or "").strip()
    if len(s.split()) < 2 <= len(original.split()):
        return original
    return s or original


def _guess_sport(text):
    low = util.strip_accents(text or "").lower()
    for key, label in SPORT_WORDS.items():
        if re.search(r"\b" + key, low):
            return label
    return None


def _city_from_ics_location(loc):
    """'20 rue d'Avranches 31200 Toulouse' -> ('Toulouse', '31200')."""
    if not loc:
        return None, None
    flat = re.sub(r"\s*\n\s*", ", ", loc).strip()
    postal = util.extract_postal(flat)
    city = None
    if postal:
        after = flat.split(postal, 1)[1]
        city = re.split(r"[,\n]", after)[0].strip(" ,-")
    if not city:
        parts = [p.strip() for p in flat.split(",") if p.strip()]
        for part in reversed(parts):
            cleaned = re.sub(r"\d{5}", "", part).strip(" ,-")
            if cleaned and cleaned.lower() not in ("france", "fr") \
                    and not re.search(r"\b(?:rue|avenue|av|bd|boulevard|chemin|route|"
                                      r"place|impasse|esplanade)\b", cleaned, re.I):
                city = cleaned
                break
    return (util.title_name(city[:40]) if city else None), postal


def classify_event(summary, description="", location=""):
    """'lead' (piste a contacter) ou 'event' (date a couvrir)."""
    head = summary or ""
    if LEAD_WORDS.search(head):
        return "lead"
    blob = " ".join([head, description or ""])
    if EVENT_WORDS.search(blob):
        return "event"
    if CLUB_WORDS.search(head):
        return "lead"
    return "event"


def extract_calendar(events, source="calendrier"):
    """Evenements iCal -> candidats classes, prets a etre valides dans l'interface.

    Chaque element porte `kind` ('lead' ou 'event'), modifiable par l'utilisateur
    avant enregistrement.
    """
    out = []
    for ev in events:
        summary = (ev.get("summary") or "").strip()
        if not summary:
            continue
        description = ev.get("description") or ""
        location = ev.get("location") or ""
        blob = " ".join([summary, description, location])

        phones = util.extract_phones(blob)
        emails = util.extract_emails(blob)
        city, postal = _city_from_ics_location(location)
        if not postal:
            postal = util.extract_postal(blob)
        title = _clean_event_title(summary)
        sport = _guess_sport(blob)
        kind = classify_event(summary, description, location)

        start = ev.get("dtstart")
        start_date = start.date().isoformat() if hasattr(start, "date") \
            else (start.isoformat() if start else None)
        end = ev.get("dtend")
        end_date = end.date().isoformat() if hasattr(end, "date") \
            else (end.isoformat() if end else None)

        mobiles = [p for p in phones if util.is_mobile_fr(p)]
        personal = [e for e in emails if not util.is_role_email(e)]

        out.append({
            "kind": kind,
            "club": {
                "name": title or summary,
                "sport": sport,
                "city": city,
                "postal_code": postal,
                "dept": util.dept_from_postal(postal),
                "address": re.sub(r"\s*\n\s*", ", ", location).strip() or None,
                "phone": (phones[0] if phones and not mobiles else None),
                "email": next((e for e in emails if util.is_role_email(e)), None),
                "source": source,
            },
            "contact": {
                "first_name": None, "last_name": None, "role": None,
                "email": personal[0] if personal else (emails[0] if emails else None),
                "phone": mobiles[0] if mobiles else (phones[0] if phones else None),
                "source": source,
            },
            "event": {
                "name": title or summary,
                "sport": sport,
                "start_date": start_date,
                "end_date": end_date,
                "city": city,
                "postal_code": postal,
                "venue": re.sub(r"\s*\n\s*", ", ", location).strip()[:120] or None,
                "url": ev.get("url"),
                "description": description[:1000] or None,
                "external_id": ev.get("uid"),
                "source": source,
            },
            "raw": summary,
            "when": start_date,
            "confidence": 85 if (phones or emails) else (60 if postal else 40),
        })
    out.sort(key=lambda x: x.get("when") or "9999")
    return out
