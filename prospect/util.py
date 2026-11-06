"""Petits utilitaires : normalisation telephone/email, departements, dates, slugs."""
import datetime as dt
import hashlib
import re
import unicodedata

# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------

def now():
    return dt.datetime.now().replace(microsecond=0)


def now_iso():
    return now().isoformat(sep=" ")


def today():
    return dt.date.today()


def parse_date(value):
    """Accepte '2026-04-12', '12/04/2026', '12-04-2026', datetime. Renvoie date ou None."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    s = s.replace("T", " ").split(" ")[0]
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def parse_dt(value):
    """Accepte une date ou un datetime, renvoie datetime (00:00 si date seule)."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return value
    s = str(value).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d/%m/%Y %H:%M"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    d = parse_date(s)
    return dt.datetime.combine(d, dt.time(9, 0)) if d else None


def days_between(a, b):
    if not a or not b:
        return None
    return (b - a).days


def human_delta(iso_ts):
    """'il y a 3 j' / 'dans 5 j' a partir d'un timestamp ISO."""
    d = parse_dt(iso_ts)
    if not d:
        return ""
    delta = (d.date() - today()).days
    if delta == 0:
        return "aujourd'hui"
    if delta == 1:
        return "demain"
    if delta == -1:
        return "hier"
    if delta > 0:
        return "dans %d j" % delta
    return "il y a %d j" % (-delta)


# --------------------------------------------------------------------------
# Texte
# --------------------------------------------------------------------------

def strip_accents(s):
    if not s:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def slugify(s, maxlen=60):
    s = strip_accents(s or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen] or "sans-nom"


def norm_key(s):
    """Cle de comparaison laxiste : minuscules, sans accents, sans ponctuation."""
    s = strip_accents(s or "").lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def title_name(s):
    """'jean-PIERRE dupont' -> 'Jean-Pierre Dupont' (particules en minuscules)."""
    if not s:
        return ""
    small = {"de", "du", "des", "le", "la", "les", "van", "von", "da", "di", "el"}
    out = []
    for word in re.split(r"(\s+)", s.strip()):
        if not word.strip():
            out.append(word)
            continue
        if word.lower() in small and out:
            out.append(word.lower())
            continue
        out.append("-".join(p.capitalize() for p in word.split("-")))
    return "".join(out)


def short_hash(*parts):
    raw = "|".join(str(p or "") for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


# --------------------------------------------------------------------------
# Telephone (France par defaut)
# --------------------------------------------------------------------------

PHONE_RE = re.compile(
    r"(?<![\w.])"
    r"(?:(?:\+|00)\s?33[\s.\-]?(?:\(0\)[\s.\-]?)?|0)"
    r"\s?[1-9](?:[\s.\-]?\d{2}){4}"
    r"(?![\w])"
)

# Numeros internationaux generiques (fallback, pour les clubs hors FR)
PHONE_INTL_RE = re.compile(r"(?<![\w.])\+\d{1,3}[\s.\-]?(?:\d[\s.\-]?){6,14}\d(?![\w])")


def normalize_phone(raw, default_cc="33"):
    """Renvoie le numero en E.164 ('+33612345678') ou None si non exploitable."""
    if not raw:
        return None
    s = str(raw).strip()
    s = s.replace("(0)", "")
    plus = s.lstrip().startswith("+") or s.lstrip().startswith("00")
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
        plus = True
    if plus:
        if len(digits) < 8 or len(digits) > 15:
            return None
        # +33 0 6 ... : certains sites laissent le 0 apres l'indicatif
        if digits.startswith("33") and len(digits) == 12 and digits[2] == "0":
            digits = "33" + digits[3:]
        return "+" + digits
    if digits.startswith("33") and len(digits) == 11:
        return "+" + digits
    # « 06 12 34 56 78 » est un numero francais, meme lu sur un annuaire
    # monegasque : un abonne de Monaco a HUIT chiffres apres le +377, jamais
    # neuf. Importer cet annuaire avec default_cc="377" a fabrique quatre
    # +377612345678 — des numeros qui n'existent dans aucun plan de
    # numerotation, et que WhatsApp ne pouvait donc pas joindre. Quand
    # l'indicatif demande ne colle pas a la longueur, on retombe sur la France.
    # Et l'inverse : « 93 12 34 56 » ecrit en local sur un site monegasque est
    # un vrai numero de Monaco. On ne l'accepte que si l'appelant a dit Monaco,
    # pour qu'un fragment a huit chiffres ne devienne jamais un numero ailleurs.
    if default_cc == "377" and len(digits) == 8 and digits[0] in "469":
        return "+377" + digits
    national = digits[1:] if digits.startswith("0") and len(digits) == 10 else (
        digits if len(digits) == 9 and digits[0] != "0" else None)
    if national is not None:
        cc = default_cc
        if cc == "377" and len(national) != 8:
            cc = "33"
        return "+" + cc + national
    return None


def lieu_lisible(*bouts):
    """Assemble un lieu sans le répéter.

    « Stade de Gilly, Stade de Gilly » : le lecteur de calendrier range la
    même chaîne dans `venue` ET dans `city`, et chaque endroit qui les
    collait bout à bout l'écrivait deux fois — sur la carte, dans le
    message au groupe, et jusque dans le planning du sourceur.

    On écarte aussi ce qui est déjà contenu ailleurs : « Gilly » n'ajoute
    rien à « Stade de Gilly ».
    """
    gardes = []
    for b in bouts:
        b = " ".join(str(b or "").split())
        if not b:
            continue
        plat = strip_accents(b).lower()
        if any(plat in strip_accents(g).lower() or strip_accents(g).lower() in plat
               for g in gardes):
            continue
        gardes.append(b)
    return ", ".join(gardes)


def format_phone(e164):
    """Affichage lisible : '+33612345678' -> '06 12 34 56 78'."""
    if not e164:
        return ""
    if e164.startswith("+33") and len(e164) == 12:
        rest = "0" + e164[3:]
        return " ".join(rest[i:i + 2] for i in range(0, 10, 2))
    return e164


def is_mobile_fr(numero):
    """Un mobile francais, quelle que soit la forme du numero recu.

    La fonction n'acceptait que du E.164 deja normalise. Or les numeros tires
    d'un flyer ou d'une annonce Facebook arrivent en « 0612345678 » ou
    « 06 98 76 54 32 » : le message basculait donc en mail vers un club qui
    n'avait qu'un portable, et n'etait jamais ecrit.
    """
    e164 = normalize_phone(numero) if numero else None
    return bool(e164) and e164.startswith("+33") and len(e164) == 12 and e164[3] in "67"


# Un horodatage de nom de fichier ('PHOTO-2026-09-05-10-57-20.jpg') ressemble a
# un numero francais. On refuse une suite qui prolonge une autre suite de chiffres.
_DIGIT_RUN_BEFORE = re.compile(r"\d\s*[-./]\s*$")
_DIGIT_RUN_AFTER = re.compile(r"^\s*[-./]\s*\d")


def _in_digit_run(text, start, end):
    return bool(_DIGIT_RUN_BEFORE.search(text[max(0, start - 8):start])
                or _DIGIT_RUN_AFTER.match(text[end:end + 8]))


# Les gabarits de site : « 04 94 00 00 00 », « 01 23 45 67 89 », « 06 00 00 00 00 ».
# Ils passent tous les tests de forme — c'est bien un numero francais valide —
# et c'est precisement le danger : rien ne les distingue d'un vrai une fois
# affiches sur une fiche. Le site de l'AS Brignoles en porte un.
def _est_gabarit(n):
    """Un numero de demonstration, laisse tel quel dans un modele de page."""
    corps = n[3:] if n.startswith("+33") else re.sub(r"\D", "", n)
    if len(corps) < 9:
        return False
    # Six chiffres identiques a la fin : 04 94 00 00 00, 06 11 11 11 11.
    # Un vrai numero ne finit pas ainsi ; un gabarit de page, si.
    if len(set(corps[-6:])) == 1:
        return True
    # La suite d'ecole : 01 23 45 67 89 et ses variantes.
    if corps[1:] in ("12345678", "23456789", "98765432", "87654321"):
        return True
    return False


def extract_phones(text, default_cc="33"):
    """Toutes les numeros trouves dans un texte libre, en E.164, dedupliques."""
    if not text:
        return []
    found = []
    for match in PHONE_RE.finditer(text):
        if _in_digit_run(text, match.start(), match.end()):
            continue
        n = normalize_phone(match.group(0), default_cc)
        if n:
            found.append(n)
    for match in PHONE_INTL_RE.finditer(text):
        if _in_digit_run(text, match.start(), match.end()):
            continue
        n = normalize_phone(match.group(0), default_cc)
        if n:
            found.append(n)
    seen, out = set(), []
    for n in found:
        if n not in seen and not _est_gabarit(n):
            seen.add(n)
            out.append(n)
    return out


# --------------------------------------------------------------------------
# Email
# --------------------------------------------------------------------------

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Emails obfusques du style "contact [at] club-x [dot] fr"
EMAIL_OBF_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*(?:\[|\(|\s)\s*(?:at|arobase|@)\s*(?:\]|\)|\s)\s*"
    r"([A-Za-z0-9.\-]+)\s*(?:\[|\(|\s)\s*(?:dot|point|\.)\s*(?:\]|\)|\s)\s*([A-Za-z]{2,})",
    re.I,
)

ROLE_EMAIL_PREFIXES = {
    "contact", "info", "infos", "secretariat", "secretary", "club", "accueil",
    "bureau", "president", "presidence", "tournoi", "tournois", "competition",
    "sportif", "administration", "admin", "hello", "bonjour", "mail",
}


def normalize_email(raw):
    if not raw:
        return None
    s = str(raw).strip().strip("<>").strip(".,;:").lower()
    if not EMAIL_RE.fullmatch(s):
        return None
    return s


def extract_emails(text):
    if not text:
        return []
    found = [m.group(0).lower().rstrip(".,;:") for m in EMAIL_RE.finditer(text)]
    for m in EMAIL_OBF_RE.finditer(text):
        found.append(("%s@%s.%s" % (m.group(1), m.group(2), m.group(3))).lower())
    seen, out = set(), []
    for e in found:
        e = normalize_email(e)
        if e and e not in seen:
            seen.add(e)
            out.append(e)
    return out


def is_role_email(email):
    """True pour contact@, info@... (adresse de structure, pas de personne)."""
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0]
    return local.split("+")[0].split(".")[0] in ROLE_EMAIL_PREFIXES


def email_domain(email):
    return email.split("@", 1)[1] if email and "@" in email else None


# --------------------------------------------------------------------------
# Geo France
# --------------------------------------------------------------------------

POSTAL_RE = re.compile(r"(?<!\d)(\d{5})(?!\d)")

DEPT_NAMES = {
    "01": "Ain", "02": "Aisne", "03": "Allier", "04": "Alpes-de-Haute-Provence",
    "05": "Hautes-Alpes", "06": "Alpes-Maritimes", "07": "Ardèche", "08": "Ardennes",
    "09": "Ariège", "10": "Aube", "11": "Aude", "12": "Aveyron", "13": "Bouches-du-Rhône",
    "14": "Calvados", "15": "Cantal", "16": "Charente", "17": "Charente-Maritime",
    "18": "Cher", "19": "Corrèze", "2A": "Corse-du-Sud", "2B": "Haute-Corse",
    "21": "Côte-d'Or", "22": "Côtes-d'Armor", "23": "Creuse", "24": "Dordogne", "25": "Doubs",
    "26": "Drôme", "27": "Eure", "28": "Eure-et-Loir", "29": "Finistère", "30": "Gard",
    "31": "Haute-Garonne", "32": "Gers", "33": "Gironde", "34": "Hérault",
    "35": "Ille-et-Vilaine", "36": "Indre", "37": "Indre-et-Loire", "38": "Isère",
    "39": "Jura", "40": "Landes", "41": "Loir-et-Cher", "42": "Loire", "43": "Haute-Loire",
    "44": "Loire-Atlantique", "45": "Loiret", "46": "Lot", "47": "Lot-et-Garonne",
    "48": "Lozère", "49": "Maine-et-Loire", "50": "Manche", "51": "Marne", "52": "Haute-Marne",
    "53": "Mayenne", "54": "Meurthe-et-Moselle", "55": "Meuse", "56": "Morbihan",
    "57": "Moselle", "58": "Nièvre", "59": "Nord", "60": "Oise", "61": "Orne",
    "62": "Pas-de-Calais", "63": "Puy-de-Dôme", "64": "Pyrénées-Atlantiques",
    "65": "Hautes-Pyrénées", "66": "Pyrénées-Orientales", "67": "Bas-Rhin", "68": "Haut-Rhin",
    "69": "Rhône", "70": "Haute-Saône", "71": "Saône-et-Loire", "72": "Sarthe", "73": "Savoie",
    "74": "Haute-Savoie", "75": "Paris", "76": "Seine-Maritime", "77": "Seine-et-Marne",
    "78": "Yvelines", "79": "Deux-Sèvres", "80": "Somme", "81": "Tarn",
    "82": "Tarn-et-Garonne", "83": "Var", "84": "Vaucluse", "85": "Vendée", "86": "Vienne",
    "87": "Haute-Vienne", "88": "Vosges", "89": "Yonne", "90": "Territoire de Belfort",
    "91": "Essonne", "92": "Hauts-de-Seine", "93": "Seine-Saint-Denis", "94": "Val-de-Marne",
    "95": "Val-d'Oise", "971": "Guadeloupe", "972": "Martinique", "973": "Guyane",
    "974": "La Réunion", "976": "Mayotte",
}

# Departements limitrophes : sert au ciblage "tournoi a cote de chez eux".
NEIGHBOURS = {
    "01": "38 39 69 71 73 74", "02": "08 51 59 60 62 77 80", "03": "18 23 42 58 63 71",
    "04": "05 06 13 26 83 84", "05": "04 26 38 73", "06": "04 83", "07": "26 30 38 42 43 48 84",
    "08": "02 51 55", "09": "11 31 66", "10": "21 51 52 77 89", "11": "09 30 31 34 66 81",
    "12": "15 30 34 46 48 81 82", "13": "04 30 83 84", "14": "27 50 61 76",
    "15": "12 19 43 46 48 63", "16": "17 24 79 86 87", "17": "16 24 33 79 85",
    "18": "03 23 36 41 45 58", "19": "15 23 24 46 63 87", "21": "10 39 52 58 70 71 89",
    "22": "29 35 56", "23": "03 18 19 36 63 87", "24": "16 17 19 33 46 47 87",
    "25": "39 70 90", "26": "05 07 38 84", "27": "14 27 28 60 61 76 78 95",
    "28": "27 41 45 61 72 78 91", "29": "22 56", "30": "07 12 13 34 48 84",
    "31": "09 11 32 34 65 81 82", "32": "31 40 47 64 65 82", "33": "16 17 24 40 47",
    "34": "11 12 30 81", "35": "22 44 49 50 53 56", "36": "18 23 37 41 86 87",
    "37": "36 37 41 49 72 86", "38": "01 05 07 26 42 69 73", "39": "01 21 25 39 70 71",
    "40": "32 33 47 64", "41": "18 28 36 37 41 45 72", "42": "01 03 07 43 63 69 71",
    "43": "07 15 42 48 63", "44": "35 49 56 85", "45": "18 28 41 45 58 77 89 91",
    "46": "12 15 19 24 47 82", "47": "24 32 33 40 46 47 82", "48": "07 12 15 30 43",
    "49": "35 37 44 49 53 72 79 85 86", "50": "14 35 53 61", "51": "02 08 10 51 52 55 77",
    "52": "10 21 51 55 70 88", "53": "35 49 50 53 61 72", "54": "55 57 67 88",
    "55": "08 51 52 54 55 88", "56": "22 29 35 44 56", "57": "54 55 67 88",
    "58": "03 18 21 45 71 89", "59": "02 62 80", "60": "02 27 60 76 77 80 95",
    "61": "14 27 28 50 53 61 72", "62": "59 80", "63": "03 15 19 23 42 43 63",
    "64": "32 40 64 65", "65": "31 32 64 65", "66": "09 11", "67": "54 57 68 88",
    "68": "67 70 88 90", "69": "01 38 42 69 71", "70": "21 25 52 68 70 88 90",
    "71": "01 03 21 39 42 58 69 71", "72": "28 37 41 49 53 61 72",
    "73": "01 05 38 74", "74": "01 73", "75": "92 93 94",
    "76": "27 60 76 80", "77": "02 10 45 51 60 77 91 93 94 95",
    "78": "27 28 91 92 95", "79": "16 17 49 79 85 86", "80": "02 59 60 62 76",
    "81": "11 12 31 34 81 82", "82": "12 31 32 46 47 81 82", "83": "04 06 13 83",
    "84": "04 07 13 26 30 84", "85": "17 44 49 79", "86": "16 36 37 49 79 86 87",
    "87": "16 19 23 24 36 86 87", "88": "52 54 55 67 68 70 88", "89": "10 21 45 58 89",
    "90": "25 68 70", "91": "28 45 77 78 91 92 94", "92": "75 78 91 93 94 95",
    "93": "75 77 92 94 95", "94": "75 77 91 92 93", "95": "60 77 78 92 93 95",
}


def dept_from_postal(postal):
    """Code departement a partir d'un code postal francais."""
    if not postal:
        return None
    m = POSTAL_RE.search(str(postal))
    if not m:
        return None
    pc = m.group(1)
    if pc.startswith(("97", "98")):
        return pc[:3]
    if pc.startswith("20"):
        return "2A" if int(pc) < 20200 else "2B"
    return pc[:2]


def dept_name(code):
    return DEPT_NAMES.get(code, code or "")


def nearby_depts(code, include_self=True):
    """Departement + limitrophes, pour cibler large sans partir a l'autre bout du pays."""
    if not code:
        return []
    out, seen = [], set()
    for d in ([code] if include_self else []) + NEIGHBOURS.get(code, "").split():
        if d and d not in seen and (include_self or d != code):
            seen.add(d)
            out.append(d)
    return out


def extract_postal(text):
    if not text:
        return None
    m = POSTAL_RE.search(str(text))
    return m.group(1) if m else None


# Un mot qui ne designe personne a lui seul : un sigle, un numero, un
# article. « Les Arts de l'air » doit donner « Les Arts », pas « Les ».
_SIGLE = re.compile(r"^[A-Za-z]{1,2}$|^\d+$", re.I)
_ARTICLES = {"le", "la", "les", "l", "du", "de", "des", "un", "une", "aux"}


def nom_bulle(nom, maxi=18):
    """Le nom court affiche devant un message : « Leslie », « AS Cannes ».

    Le repertoire garde des noms entiers — « Leslie Content Creator Grasse ».
    Dans une bulle on veut savoir qui parle, pas relire la fiche. On prend le
    premier mot quand il tient tout seul, les deux premiers quand il commence
    par un sigle ou un numero (« Cd 06 », « AS Cannes »).
    """
    propre = re.sub(r"\s+", " ", str(nom or "")).strip()
    if not propre or re.fullmatch(r"[+\d\s().-]+", propre):
        return None
    mots = propre.split(" ")
    tete = strip_accents(mots[0]).lower().strip("'’")
    if len(mots) == 1 or not (_SIGLE.match(mots[0]) or tete in _ARTICLES):
        return mots[0][:maxi]
    return " ".join(mots[:2])[:maxi]
