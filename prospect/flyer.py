# -*- coding: utf-8 -*-
"""Depouillement d'un flyer, entierement en local et sur-le-champ.

Le texte vient du moteur de reconnaissance de macOS (prospect.ocr). Le reste
reutilise ce que l'application sait deja faire : extract pour les clubs et les
contacts, dates_fr pour les dates, geo pour la distance et les categories,
drafts pour le message. Aucune dependance exterieure, aucune attente.
"""

import os
import re
import shutil

from . import dates_fr, db, drafts, extract, geo, ocr, repo, util

EQUIPES_RE = re.compile(r"(\d{1,3})\s*[ée]quipes?", re.I)
CATEG_RE = re.compile(r"\bU\s?(\d{1,2})\b", re.I)
SPORTS = [
    ("football", "Football"), ("foot", "Football"), ("basket", "Basket-ball"),
    ("hand", "Handball"), ("volley", "Volley-ball"), ("rugby", "Rugby"),
    ("tennis", "Tennis"), ("judo", "Judo"), ("karat", "Karate"),
    ("natation", "Natation"), ("voile", "Voile"), ("regate", "Voile"),
    ("triathlon", "Triathlon"), ("trail", "Trail"), ("danse", "Danse"),
    ("gym", "Gymnastique"), ("athl", "Athletisme"), ("aviron", "Aviron"),
    ("escalade", "Escalade"), ("boxe", "Boxe"), ("hockey", "Hockey"),
]


CP_RE = re.compile(r"\b([013-9]\d{4})\b")
GENERIQUES = {"club", "sport", "sportive", "sportif", "association", "football",
              "foot", "cup", "coupe", "tournoi", "challenge", "open", "trophee",
              "association", "ecole", "academy", "academie", "team", "les", "des",
              "sc", "fc", "as", "us", "olympique", "stade", "union", "entente"}


# Ce qui signale une raison sociale de club sur une affiche.
CLUB_MARQUEURS = re.compile(
    r"\b(?:club|association|amicale|entente|union|olympique|sporting|cercle|"
    r"academie|academy|stade|racing|etoile|avenir|jeunesse|elan|esperance|"
    r"athletique|athletic|sportive|sportif|omnisports|foyer|"
    r"a\.?s\.?|u\.?s\.?|c\.?s\.?|e\.?s\.?|f\.?c\.?|s\.?c\.?|r\.?c\.?|c\.?a\.?)\b",
    re.I)

# Slogans et mentions pratiques : ce n'est jamais le nom du club.
SLOGAN = re.compile(
    r"[!?]\s*$|\b(?:plus qu.un|une famille|esprit|respect|plaisir|partage|"
    r"pour grandir|scannez|inscri|buvette|venez|rejoignez|restauration|"
    r"entree libre|gratuit|renseignements?)\b", re.I)

PARTICULES = {"de", "du", "la", "le", "les", "des", "et", "d", "l", "aux", "au"}

# « Le Cote-Chaude Sportif ORGANISE son tournoi » : le verbe fait partie de la
# phrase, pas du nom.
VERBE_FINAL = re.compile(
    r"\s+(?:organise(?:nt|ra)?|presente(?:nt)?|pr[ée]sente(?:nt)?|invite(?:nt)?|"
    r"vous\s+(?:invite|attend|propose)\w*|re[cç]oit|accueille(?:nt)?|"
    r"lance(?:nt)?|propose(?:nt)?)\b.*$", re.I)

BOUCHE_TROU = re.compile(r"\(\s*club\s+[àa]?\s*preciser\s*\)|\(club a preciser\)", re.I)


def _nettoyer_club(nom):
    """Retire le verbe et le bouche-trou d'extract d'un nom de club."""
    texte = BOUCHE_TROU.sub("", str(nom or "")).strip(" .,;:-–—")
    texte = VERBE_FINAL.sub("", texte).strip(" .,;:-–—")
    return texte

SITE_RE = re.compile(
    r"\b((?:https?://)?(?:www\.)?[a-z0-9][a-z0-9-]*(?:\.[a-z0-9-]+)*\."
    r"(?:fr|com|net|org|eu|io|be|ch)(?:/[^\s]*)?)", re.I)


def _casse(ligne):
    """Remet en casse normale, sauf si un sigle risque d'y perdre ses capitales."""
    mots = re.findall(r"[\w'’-]+", ligne)
    for m in mots:
        if m.lower() in PARTICULES:
            continue
        if len(m) <= 3 and m.isupper():
            return ligne          # « ASP PIGNAN », « CA PEYMEINADE » : on n'y touche pas
    return util.title_name(ligne) if ligne.isupper() else ligne


def _club_du_texte(texte, ville):
    """Le nom du club, lu directement sur l'affiche.

    extract ne rend un candidat que si un email ou un telephone accompagne le
    nom. Beaucoup de flyers n'en portent aucun — un QR code et une page
    Facebook tiennent lieu de contact — et le club restait alors introuvable.
    On retombe donc sur la ligne qui ressemble le plus a une raison sociale.
    """
    ville_plate = util.strip_accents(ville or "").lower()
    meilleur, note_max = None, 0
    for brut in (texte or "").splitlines():
        ligne = brut.strip(" \t-–—•*:;,.")
        if not (5 <= len(ligne) <= 70) or "@" in ligne:
            continue
        plat = util.strip_accents(ligne).lower()
        if SLOGAN.search(plat):
            continue
        note = 0
        if ville_plate and ville_plate in plat:
            note += 50
        if CLUB_MARQUEURS.search(plat):
            note += 25
        lettres = [c for c in ligne if c.isalpha()]
        if lettres and sum(1 for c in lettres if c.isupper()) / float(len(lettres)) > .7:
            note += 15            # le nom du club est en capitales sur presque tous les flyers
        if len(re.findall(r"[\w'’]+", ligne)) < 2:
            note -= 40            # un mot seul, c'est la commune ou un slogan tronque
        if re.search(r"\d", plat):
            note -= 15            # une date, un horaire, un nombre d'equipes
        if note > note_max:
            meilleur, note_max = ligne, note
    # En dessous, on devine plus qu'on ne lit : mieux vaut ne rien affirmer.
    if note_max < 60:
        return None
    propre = _nettoyer_club(meilleur)
    return _casse(propre) if len(propre) >= 4 else None


def _site(texte):
    """L'adresse web imprimee sur l'affiche : souvent le seul point de contact.

    Le site propre du club prime sur sa page Facebook, qui ne dit rien de plus
    qu'un reseau social partage par tout le monde.
    """
    from .repo import DOMAINES_PARTAGES
    trouves = []
    for m in SITE_RE.finditer(texte or ""):
        url = m.group(1).rstrip(".,;)")
        if len(url) < 8 or "@" in url:
            continue
        if not url.lower().startswith("http"):
            url = "https://" + url
        trouves.append(url)
    for url in trouves:
        hote = url.split("//", 1)[-1].split("/")[0].lower().replace("www.", "")
        if hote not in DOMAINES_PARTAGES:
            return url
    return trouves[0] if trouves else None


def _lettres(texte):
    return re.sub(r"[^a-z0-9]", "", util.strip_accents(str(texte or "")).lower())


def _domaine(texte):
    """Le domaine imprime sur l'affiche : site, ou domaine de l'email de contact."""
    for mail in (util.extract_emails(texte) or []):
        dom = util.email_domain(mail)
        if dom and dom.split(".")[0] not in ("gmail", "hotmail", "outlook", "orange",
                                             "free", "wanadoo", "yahoo", "laposte",
                                             "sfr", "icloud"):
            return dom
    site = _site(texte)
    if site:
        hote = site.split("//", 1)[-1].split("/")[0].lower().replace("www.", "")
        from .repo import DOMAINES_PARTAGES
        if hote not in DOMAINES_PARTAGES:
            return hote
    return None


def _nom_depuis_domaine(texte, domaine):
    """« ELITEFOOTEVENTS.FR » + les lignes « ELITE » / « FOOT EVENTS » -> le nom.

    Le domaine dit qui organise ; l'affiche dit comment ca s'ecrit. On cherche
    donc la suite de lignes dont les lettres, mises bout a bout, redonnent le
    domaine. Sans elle, on n'affirme rien.
    """
    base = _lettres((domaine or "").split(".")[0])
    if len(base) < 6:
        return None
    lignes = [l.strip(" \t-–—•*:;,.") for l in (texte or "").splitlines()]
    lignes = [l for l in lignes if l and len(l) <= 40]
    for i in range(len(lignes)):
        assemble = ""
        for j in range(i, min(i + 3, len(lignes))):
            assemble += _lettres(lignes[j])
            if assemble == base:
                return _casse(" ".join(lignes[i:j + 1]))
            if not base.startswith(assemble):
                break
    return None


def _mots(texte):
    """Mots significatifs d'un nom, sans accent ni generique."""
    plat = util.strip_accents(str(texte or "")).lower()
    return {m for m in re.findall(r"[a-z0-9]{3,}", plat) if m not in GENERIQUES}


# Un nom de commune ne compte que s'il est annonce comme un lieu. « Plaisir »
# est une ville des Yvelines et le dernier mot de « jeux, plaisir et amusement » :
# sans cette regle, une affiche cannoise se retrouve a 925 km.
AVANT_UN_LIEU = re.compile(
    r"\b(a|au|aux|stade|stades|gymnase|complexe|salle|terrain|terrains|parc|"
    r"city|boulodrome|piscine|halle|palais|centre|lieu|adresse|rue|avenue|"
    r"boulevard|chemin|route|place|impasse|allee|quai|ville|commune|de|du|"
    r"des|sur|pres|vers|direction)\s*$", re.I)

APRES_UN_LIEU = re.compile(r"^\s*(\(?\d{5}|\(?\d{2}\)|-|–|,|\.|$)")


def _contexte_de_lieu(plat, debut, fin):
    """Dit si le nom trouve a cette position est annonce comme un lieu."""
    avant = plat[max(0, debut - 28):debut]
    apres = plat[fin:fin + 8]
    if AVANT_UN_LIEU.search(avant):
        return True
    if APRES_UN_LIEU.match(apres):
        return True
    # Seul sur sa ligne, c'est une adresse.
    gauche = plat.rfind("\n", 0, debut)
    droite = plat.find("\n", fin)
    ligne = plat[gauche + 1:droite if droite > 0 else len(plat)].strip()
    return len(ligne) <= (fin - debut) + 12


def geo_connue(nom):
    from . import geo
    d = geo.depuis_cannes((nom or "").strip())
    return (d["commune"], d.get("cp")) if d else None


def _resoudre_commune(brut):
    """« CANNES LA BOCCA » -> (« Cannes », « 06150 »).

    Les affiches donnent des quartiers et des libelles postaux que la table
    des distances ne connait pas : on raccourcit mot a mot jusqu'a tomber sur
    une commune reelle, plutot que de rendre une distance fausse.
    """
    from . import geo
    # Le nom complet est tente en premier : « Saint-Laurent-du-Var » se
    # resout d'un coup, on ne le decoupe que s'il ne tombe sur rien.
    mots = [m for m in re.split(r"[\s,\-–]+", (brut or "").strip()) if m]
    if geo_connue(brut):
        return geo_connue(brut)
    while mots:
        d = geo.depuis_cannes(" ".join(mots))
        if d:
            return d["commune"], d.get("cp")
        mots.pop()
    return None


def _commune(texte):
    """Retrouve la commune du flyer : par code postal, puis par nom connu.

    On s'appuie sur la table des distances, qui contient les communes de la
    region et celles deja rencontrees. Rend (nom, code_postal) ou (None, None).
    """
    from . import geo
    table = geo._table()
    for m in CP_RE.finditer(texte or ""):
        cp = m.group(1)
        for r in table:
            if r.get("cp") == cp:
                return r["commune"], cp
    plat = " " + re.sub(r"\s+", " ", util.strip_accents(texte or "").lower()) + " "
    trouve = None
    for r in table:
        nom = util.strip_accents(r["commune"]).lower()
        if len(nom) < 5:
            continue
        for m in re.finditer(r"(?<![a-z])" + re.escape(nom) + r"(?![a-z])", plat):
            if not _contexte_de_lieu(plat, m.start(), m.end()):
                continue
            if trouve is None or len(nom) > len(trouve[0]):
                trouve = (nom, r)
            break
    if trouve:
        return trouve[1]["commune"], trouve[1].get("cp")

    # La table ne couvre que la region. Hors zone, on interroge le geocodeur
    # sur les lignes qui ressemblent a un nom de commune.
    for essai in _communes_possibles(texte):
        d = geo.depuis_cannes(essai)
        if d:
            return d["commune"], d.get("cp")
    return None, None


# Ce qui n'est jamais un nom de commune sur une affiche.
PAS_UNE_COMMUNE = re.compile(
    r"\b(tournoi|coupe|cup|challenge|equipes?|terrains?|arbitres?|restauration|"
    r"recompenses?|animations?|ambiance|engagement|inscription|inscrivez|confirme|"
    r"photographe|buvette|categorie|samedi|dimanche|lundi|mardi|mercredi|jeudi|"
    r"vendredi|janvier|fevrier|mars|avril|juin|juillet|aout|septembre|octobre|"
    r"novembre|decembre|football|futsal|club|region|france|suisse|elite|"
    r"renseignements|hebergement|billets|installations|synthetiques)\b", re.I)

# Ce qui trahit un nom de club plutot qu'une commune.
FIN_DE_CLUB = re.compile(r"\b(fc|as|us|sc|rc|es|ca|ol|asc|acs)\b\.?\s*$", re.I)


def _communes_possibles(texte, maximum=5):
    """Les lignes qui ressemblent le plus a un nom de commune, meilleures d'abord."""
    vus, notes = set(), []
    for brut in (texte or "").splitlines():
        ligne = brut.strip(" \t-–—•*:;,.()")
        if not (4 <= len(ligne) <= 40):
            continue
        if not re.match(r"^[A-Za-zÀ-ſ' -]+$", ligne):
            continue
        plat = util.strip_accents(ligne).lower()
        if PAS_UNE_COMMUNE.search(plat) or FIN_DE_CLUB.search(plat):
            continue
        if plat in vus:
            continue
        vus.add(plat)
        note = 0
        if "-" in ligne:
            note += 2
        if re.search(r"\b(saint|sainte|st|ste|sur|sous|les|mont|val|ville|bourg|"
                     r"vaux|aix|chateau)\b", plat):
            note += 2
        lettres = [c for c in ligne if c.isalpha()]
        if lettres and sum(1 for c in lettres if c.isupper()) / float(len(lettres)) > .7:
            note += 1
        if note:
            notes.append((note, ligne))
    notes.sort(key=lambda x: -x[0])
    return [l for _n, l in notes[:maximum]]


def _acronyme(titre):
    """« ASP CUP 2026 » -> « ASP » : le sigle en tete d'un intitule de tournoi."""
    for mot in re.findall(r"\b[A-Z]{2,5}\b", titre or ""):
        if mot.lower() not in GENERIQUES:
            return mot
    return None


def _jour(valeur):
    """Rend AAAA-MM-JJ, que dates_fr donne un objet date ou deja une chaine."""
    if valeur is None:
        return None
    return valeur.isoformat() if hasattr(valeur, "isoformat") else str(valeur)[:10]


def _sport(texte):
    plat = util.strip_accents(texte).lower()
    for cle, nom in SPORTS:
        if cle in plat:
            return nom
    return None


def _equipes(texte):
    """« 32 équipes par catégories » -> 32. On garde le plus grand nombre cite."""
    nombres = [int(m.group(1)) for m in EQUIPES_RE.finditer(texte)]
    nombres = [n for n in nombres if 2 <= n <= 400]
    return max(nombres) if nombres else None


def _categories(texte):
    """Rend la phrase lisible « U11-U12 — nés en 2015 et 2016 », ou None."""
    trouves = sorted({int(m.group(1)) for m in CATEG_RE.finditer(texte)
                      if 5 <= int(m.group(1)) <= 21})
    if not trouves:
        return None, []
    fiches = [c for c in geo.categories() if int(c["categorie"][1:]) in trouves]
    if not fiches:
        return None, []
    annees = sorted({f["naissance"] for f in fiches})
    codes = "-".join(f["categorie"] for f in fiches)
    if len(annees) == 1:
        detail = "nes en %d" % annees[0]
    elif len(annees) == 2:
        detail = "nes en %d et %d" % (annees[0], annees[1])
    else:
        detail = "nes de %d a %d" % (annees[0], annees[-1])
    return "%s — %s" % (codes, detail), fiches


def _intitule(texte, evenement, sport):
    """Le titre du tournoi : la ligne la plus parlante du flyer."""
    lignes = [l.strip(" -–—•\t") for l in (texte or "").splitlines()]
    lignes = [l for l in lignes if 3 <= len(l) <= 70]
    mots = re.compile(r"\b(cup|coupe|tournoi|challenge|trophee|troph[ée]e|open|"
                      r"championnat|gala|meeting|regate|r[ée]gate|rassemblement|"
                      r"critérium|criterium|festival|plateau|interclubs?|"
                      r"derby)\b", re.I)
    for l in lignes:
        if mots.search(util.strip_accents(l)):
            return _casse(l[:70])
    if evenement and evenement.get("nom"):
        return str(evenement["nom"])[:70]
    return (sport + " — evenement") if sport else "Evenement"


AFFICHES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "a-traiter", "traites")

# Facebook sert ses images sur un CDN signe : l'adresse expire au bout de
# quelques heures. On telecharge donc au moment de la capture, pas plus tard.
_TYPES = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
          "image/gif": "gif"}


def rapatrier_affiche(tid, url, prefixe="affiche"):
    """Telecharge l'image d'une annonce et l'attache a la fiche.

    Renvoie le nom du fichier, ou None. Aucune exception ne remonte : une
    affiche manquante ne doit jamais faire echouer l'enregistrement d'un
    tournoi.
    """
    if not tid or not url or not str(url).startswith(("http://", "https://")):
        return None
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"})
        with urllib.request.urlopen(req, timeout=15) as rep:
            ctype = (rep.headers.get("Content-Type") or "").split(";")[0].strip()
            octets = rep.read(8 * 1024 * 1024 + 1)
    except Exception:
        return None
    ext = _TYPES.get(ctype)
    if not ext or not octets or len(octets) > 8 * 1024 * 1024:
        return None
    try:
        if not os.path.isdir(AFFICHES):
            os.makedirs(AFFICHES)
        nom = "%s-%d.%s" % (prefixe, int(tid), ext)
        with open(os.path.join(AFFICHES, nom), "wb") as fh:
            fh.write(octets)
        db.update("tournaments", int(tid),
                  {"affiche": nom, "updated_at": util.now_iso()})
        return nom
    except OSError:
        return None


def depouiller(chemin, dossier_traites=None):
    """Lit un flyer et remplit le CRM. Rend un compte rendu lisible."""
    fichier = os.path.basename(chemin)
    cr = {"fichier": fichier, "texte": None, "club": None, "tournoi": None,
          "brouillon": None, "faits": {}, "soucis": []}

    try:
        texte = ocr.texte(chemin)
    except ocr.OcrIndisponible as exc:
        cr["soucis"].append(str(exc))
        return cr
    if not (texte or "").strip():
        cr["soucis"].append("aucun texte lisible sur l'image")
        return cr
    cr["texte"] = texte

    # Le modele local relit l'affiche et propose ce que les expressions
    # regulieres n'ont pas su lire. Il ne REMPLACE jamais une valeur trouvee :
    # il ne sert qu'a combler les trous. Et il ne touche ni aux telephones ni
    # aux mails, extraits plus bas par util, qui a les garde-fous.
    propose = {}
    try:
        from . import ia
        if ia.disponible():
            propose = ia.lire_flyer(texte) or {}
    except Exception:
        propose = {}

    sport = _sport(texte) or propose.get("sport")
    equipes = _equipes(texte) or propose.get("nb_equipes")
    cats, _fiches = _categories(texte)
    if not cats and propose.get("categories"):
        cats = propose["categories"]
    if propose:
        cr["lu_par_le_modele"] = {k: v for k, v in propose.items() if v}
    evenements = dates_fr.extraire(texte) or []
    ev = evenements[0] if evenements else None
    if len(evenements) > 1:
        # Plusieurs dates sur le meme flyer (U11 le samedi, U12 le dimanche) :
        # on couvre du premier au dernier jour.
        ev = dict(ev)
        ev["fin"] = evenements[-1].get("fin") or evenements[-1].get("debut")

    # --- club et contact, avec le moteur d'import de l'application ---
    titre = _intitule(texte, ev, sport)
    ville, cp = _commune(texte)
    # Le modele a lu l'affiche en entier, mise en page comprise : s'il donne
    # une commune qui tombe dans la table des distances, elle fait foi. Les
    # expressions regulieres, elles, lisent un texte a plat.
    if propose.get("city"):
        dit = _resoudre_commune(propose["city"])
        if dit:
            ville, cp = dit[0], dit[1] or cp
    mots_titre = _mots(titre)

    cands = extract.extract_any(texte, source="Flyer depose", default_sport=sport) or []
    cands = [c for c in cands if (c.get("club") or {}).get("name")]
    for c in cands:
        c["club"]["name"] = _nettoyer_club(c["club"]["name"])
    # « X (club a preciser) » est un bouche-trou : reduit a rien, on l'ecarte.
    cands = [c for c in cands if len((c["club"]["name"] or "")) >= 4]

    # Un flyer sans email ni telephone ne produit aucun candidat : on lit alors
    # le nom du club sur l'affiche elle-meme.
    # Le modele passe devant la reconstruction par expressions regulieres :
    # celle-ci recolle un sigle a une commune et fabrique « FC Cannes » a
    # partir de « FC Cannes Ranguin » — deux clubs differents.
    if not cands:
        nom_lu = _nettoyer_club(util.title_name(propose.get("organizer") or "")) \
            or _club_du_texte(texte, ville)
        if nom_lu:
            cands.append({"club": {"name": nom_lu, "sport": sport, "city": ville,
                                   "postal_code": cp,
                                   "dept": util.dept_from_postal(cp) if cp else None,
                                   "website": _site(texte),
                                   "source": "Flyer depose", "status": "prospect"},
                          "contact": {}, "confidence": 45})

    def _score(c):
        nom = (c.get("club") or {}).get("name") or ""
        s_ = 0
        # Le club qui organise se retrouve dans le titre du tournoi.
        s_ += 40 * len(_mots(nom) & mots_titre)
        # Celui dont la ville est celle du flyer.
        if ville and util.strip_accents(ville).lower() in util.strip_accents(nom).lower():
            s_ += 25
        if (c.get("club") or {}).get("city") or (c.get("club") or {}).get("postal_code"):
            s_ += 10
        if len(nom) > 45 or len(nom.split()) > 6:
            s_ -= 60          # c'est une phrase, pas un nom de club
        return s_ + (c.get("confidence") or 0) / 10.0

    cands.sort(key=_score, reverse=True)
    meilleur = _score(cands[0]) if cands else 0

    # Sigle du tournoi + commune : c'est ainsi que s'appelle le club qui
    # organise. « ASP CUP » a Pignan, c'est l'ASP Pignan — et surement pas
    # le club invite dont le logo trone en bas de l'affiche.
    sigle = _acronyme(titre)
    deja_bon = False
    if sigle and ville:
        for c in cands:
            n = util.strip_accents((c.get("club") or {}).get("name") or "").lower()
            if sigle.lower() in n and util.strip_accents(ville).lower() in n:
                deja_bon = True
                break
    if ville and sigle and not deja_bon:
        if True:
            nom_reconstruit = "%s %s" % (sigle, ville)
            base = dict(cands[0]["club"]) if cands else {}
            base["name"] = nom_reconstruit
            base["city"] = ville
            base["postal_code"] = cp
            base["dept"] = util.dept_from_postal(cp) if cp else None
            cands.insert(0, {"club": base,
                             "contact": (cands[0].get("contact") if cands else {}) or {},
                             "confidence": 40})

    # Le club a contacter est celui dont l'email ou le site figure sur l'affiche.
    # Les autres noms qui trainent sont des equipes invitees : les mettre en
    # avant revient a ecrire au mauvais club.
    domaine = _domaine(texte)
    if domaine:
        base = _lettres(domaine.split(".")[0])
        colle = any(_lettres((c.get("club") or {}).get("name")) in base
                    or base in _lettres((c.get("club") or {}).get("name"))
                    for c in cands)
        if not colle:
            nom_orga = _nom_depuis_domaine(texte, domaine)
            if nom_orga:
                cands.insert(0, {"club": {"name": nom_orga, "sport": sport,
                                          "city": ville, "postal_code": cp,
                                          "dept": util.dept_from_postal(cp) if cp else None,
                                          "website": _site(texte),
                                          "source": "Flyer depose", "status": "prospect"},
                                 "contact": (cands[0].get("contact") if cands else {}) or {},
                                 "confidence": 90})

    club_id = contact_id = None
    if cands:
        cd = dict(cands[0]["club"])
        if ville and not cd.get("city"):
            cd["city"] = ville
        if cp and not cd.get("postal_code"):
            cd["postal_code"] = cp
            cd["dept"] = util.dept_from_postal(cp)
        cd.setdefault("status", "prospect")
        if sport and not cd.get("sport"):
            cd["sport"] = sport
        if not cd.get("website"):
            cd["website"] = _site(texte)
        club_id, action = repo.upsert_club(cd)
        cr["club"] = {"id": club_id, "nom": cd.get("name"), "action": action}
        ct = dict(cands[0].get("contact") or {})
        if club_id and (ct.get("email") or ct.get("phone")):
            ct["club_id"] = club_id
            contact_id, _a = repo.upsert_contact(ct)
            cr["contact"] = ct.get("email") or ct.get("phone")
    if not club_id:
        cr["soucis"].append("aucun club identifiable sur le document")
        return cr

    club = db.row_to_dict(db.q1("SELECT * FROM clubs WHERE id = ?", (club_id,)))
    d = geo.depuis_cannes(club.get("city") or ville,
                          club.get("postal_code") or cp)
    cr["faits"] = {"sport": sport, "equipes": equipes, "categories": cats,
                   "distance": d, "ville": club.get("city")}

    # --- l'evenement ---
    if ev and ev.get("debut"):
        nom = "%s — %s" % (titre, club["name"])
        deja = db.q1("SELECT id FROM tournaments WHERE name = ? AND start_date = ?",
                     (nom, _jour(ev["debut"])))
        if deja:
            tid = deja["id"]
            cr["tournoi"] = {"id": tid, "nom": nom, "action": "deja connu",
                             "debut": _jour(ev["debut"]),
                             "fin": _jour(ev.get("fin") or ev["debut"])}
        else:
            now = util.now_iso()
            tid = db.insert("tournaments", {
                "name": nom, "sport": sport,
                "start_date": _jour(ev["debut"]),
                "end_date": _jour(ev.get("fin") or ev["debut"]),
                "city": club.get("city"), "postal_code": club.get("postal_code"),
                "dept": club.get("dept"), "organizer": club["name"], "club_id": club_id,
                "source": "Flyer depose", "status": "new", "discovered_at": now,
                "updated_at": now,
                "distance_km": d["km"] if d else None,
                "trajet_min": d["minutes"] if d else None,
                "distance_verdict": d["verdict"] if d else None,
                "nb_equipes": equipes, "categories": cats,
                "validation": "a_valider",
                "description": texte[:1500]})
            cr["tournoi"] = {"id": tid, "nom": nom, "action": "cree",
                             "debut": _jour(ev["debut"]),
                             "fin": _jour(ev.get("fin") or ev["debut"])}
        modele = "tournoi_avant"
    else:
        tid = None
        modele = "premier_contact"
        cr["soucis"].append("aucune date reperee sur le document")

    repo.add_interaction(club_id, kind="note", direction="in",
                         subject="Document depose : %s" % fichier,
                         body=texte[:1500])

    # --- le message, avec les modeles de Julien ---
    existe = db.q1("SELECT id FROM drafts WHERE club_id = ? AND status = 'draft' "
                   "AND COALESCE(tournament_id, 0) = ?", (club_id, tid or 0))
    if existe:
        cr["brouillon"] = {"id": existe["id"], "action": "deja pret"}
    else:
        dr = drafts.create_draft(club_id, modele, contact_id=contact_id,
                                 tournament_id=tid, reason="Flyer depose",
                                 created_by="agent")
        if dr:
            cr["brouillon"] = {"id": dr["id"], "objet": dr.get("subject"),
                               "corps": dr.get("body"), "action": "cree"}
        else:
            # Dire « opt-out ? » quand la vraie cause est qu'on n'a ni mail ni
            # portable envoyait chercher au mauvais endroit. On regarde.
            club = db.q1("SELECT do_not_contact, phone, email FROM clubs WHERE id = ?",
                         (club_id,))
            if club and club["do_not_contact"]:
                cr["soucis"].append("pas de message : ce club est en ne-pas-contacter")
            elif club and not (club["phone"] or club["email"]):
                cr["soucis"].append("pas de message : aucun numero ni mail sur le flyer "
                                    "— ajoute dans « trouver un numero »")
            else:
                cr["soucis"].append("pas de message : le modele %s n'a pas pu etre rendu"
                                    % modele)

    if dossier_traites:
        try:
            os.makedirs(dossier_traites, exist_ok=True)
            cible = os.path.join(dossier_traites, fichier)
            n = 2
            base, ext = os.path.splitext(fichier)
            while os.path.exists(cible):
                cible = os.path.join(dossier_traites, "%s-%d%s" % (base, n, ext))
                n += 1
            shutil.move(chemin, cible)
            # On retient le nom final : c'est lui qui permettra de reafficher
            # l'affiche a cote de la fiche. Sans ce lien, l'image etait bien
            # conservee, mais plus personne ne savait a quel tournoi elle allait
            # — et on relisait une fiche sans pouvoir verifier ce qu'elle disait.
            if tid:
                db.update("tournaments", tid,
                          {"affiche": os.path.basename(cible),
                           "updated_at": util.now_iso()})
                cr["affiche"] = os.path.basename(cible)
        except OSError as exc:
            cr["soucis"].append("fichier non range : %s" % exc)
    return cr


def depouiller_dossier(dossier):
    traites = os.path.join(dossier, "traites")
    try:
        noms = sorted(n for n in os.listdir(dossier)
                      if not n.startswith(".") and n != "LISEZ-MOI.txt"
                      and os.path.isfile(os.path.join(dossier, n)))
    except OSError as exc:
        return [{"fichier": None, "soucis": [str(exc)]}]
    return [depouiller(os.path.join(dossier, n), traites) for n in noms]
