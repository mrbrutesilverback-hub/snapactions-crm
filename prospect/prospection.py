# -*- coding: utf-8 -*-
"""Aller chercher des clients qu'on n'a pas encore.

La barre « Chercher » fouille ce que Julien connait deja. A « des clubs de
sport autour de Draguignan », elle repondait donc « rien dans la base » — ce
qui est vrai, et parfaitement inutile : la question n'etait pas « qu'est-ce
que je connais », mais « qu'est-ce qui existe que je ne connais pas ».

La source est le repertoire officiel des entreprises et associations
(recherche-entreprises.api.gouv.fr, SIRENE + RNA, ouvert, sans cle). On y
demande les associations sportives d'un secteur, et on retire celles qui sont
deja en fiche. Ce qui reste, ce sont des clubs reels, avec leur nom legal et
leur adresse postale — pas des noms plausibles sortis d'un modele.

Les telephones et les mails ne viennent PAS d'ici : le repertoire n'en publie
pas. Ils se cherchent ensuite, club par club, sur les pages du club, et ils
sont extraits du texte par util — jamais ecrits par un modele.
"""

import json
import math
import os
import re
import urllib.parse
import urllib.request

from . import db, geo, util

API = "https://recherche-entreprises.api.gouv.fr/search"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")

# Ce qu'on cherche. Les codes sont ceux de la nomenclature officielle : on
# prend le sport, et l'enseignement sportif ou vivent les ecoles de danse,
# les poneys-clubs et les ecoles de voile.
FAMILLES = {
    "sport": ("93.12Z", "Club de sport"),
    "installation": ("93.11Z", "Gestion d'installation sportive"),
    "forme": ("93.13Z", "Salle de forme, fitness"),
    "autre_sport": ("93.19Z", "Autre activité sportive"),
    "ecole_sport": ("85.51Z", "École de sport, de danse, d'équitation"),
}
DEFAUT = ("sport", "autre_sport", "ecole_sport")

RAYON_DEFAUT = 20          # km autour du point demande
MAX_DEFAUT = 60


GEO = "https://geo.api.gouv.fr/communes"
CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data", "communes-geo.json")

# Les departements qu'on explore autour d'un point : le sien et ses voisins,
# parce qu'un rayon de vingt kilometres autour de Draguignan deborde dans le
# 06 comme dans le 04.
VOISINS = {
    "06": ["06", "83", "04"], "83": ["83", "06", "04", "13"],
    "04": ["04", "06", "83", "05"], "13": ["13", "83", "84"],
    "84": ["84", "13", "04"], "05": ["05", "04"],
}


def _plat(t):
    return util.strip_accents(str(t or "")).lower().strip()


def _distance(lat1, lon1, lat2, lon2):
    """Vol d'oiseau, en kilometres."""
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def _lire_cache():
    try:
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _ecrire_cache(d):
    try:
        with open(CACHE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except OSError:
        pass


def communes_du_dept(dept):
    """Toutes les communes d'un departement, avec leur centre exact.

    Source officielle (geo.api.gouv.fr), sans cle. On la garde sur le disque :
    un departement ne change pas de forme entre deux recherches.
    """
    cache = _lire_cache()
    if dept in cache:
        return cache[dept]
    url = "%s?codeDepartement=%s&fields=nom,codesPostaux,centre&format=json" % (GEO, dept)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=25) as rep:
            brut = json.load(rep)
    except Exception:  # noqa: BLE001 — sans reseau on travaille avec ce qu'on a
        return cache.get(dept, [])
    liste = []
    for c in brut:
        centre = (c.get("centre") or {}).get("coordinates") or []
        if len(centre) != 2:
            continue
        liste.append({"nom": c.get("nom"), "insee": c.get("code"),
                      "cps": c.get("codesPostaux") or [],
                      "lon": centre[0], "lat": centre[1]})
    cache[dept] = liste
    _ecrire_cache(cache)
    return liste


def situer(lieu):
    """Le point de depart : « Draguignan » -> commune, coordonnees, departement."""
    nom = _plat(lieu)
    chiffres = re.sub(r"\D", "", str(lieu or ""))
    # Un code postal designe son departement : on n'interroge que celui-la.
    depts = [chiffres[:2]] if len(chiffres) == 5 else list(VOISINS)
    for d in depts:
        for c in communes_du_dept(d):
            if len(chiffres) == 5 and chiffres in (c.get("cps") or []):
                return dict(c, dept=d)
            if _plat(c.get("nom")) == nom:
                return dict(c, dept=d)
    # Le nom n'est pas exact : la premiere commune qui commence pareil.
    if len(nom) >= 4:
        for d in depts:
            for c in communes_du_dept(d):
                if _plat(c.get("nom")).startswith(nom):
                    return dict(c, dept=d)
    return None


def communes_autour(lieu, rayon_km=RAYON_DEFAUT):
    """Les communes dans le rayon, avec leurs codes postaux et la distance."""
    point = situer(lieu)
    if not point:
        return None, []
    sortie = []
    for d in VOISINS.get(point["dept"], [point["dept"]]):
        for c in communes_du_dept(d):
            km = _distance(point["lat"], point["lon"], c["lat"], c["lon"])
            if km <= rayon_km:
                sortie.append(dict(c, km=round(km, 1), dept=d))
    sortie.sort(key=lambda c: c["km"])
    return point, sortie


def _connus():
    """Ce qu'on a deja en fiche, pour ne pas le reproposer.

    On compare sur la cle de nom — « US Cagnes Volley » et « U.S. CAGNES
    VOLLEY » sont le meme club — et sur le sigle, parce que le repertoire
    officiel ecrit « ASPTT DRAGUIGNAN » la ou la fiche dit « ASPTT ».
    """
    cles = set()
    for r in db.q("SELECT name, name_key FROM clubs"):
        for v in (r["name_key"], util.norm_key(r["name"] or "")):
            if v:
                cles.add(v)
    return cles


# 92xx : les associations declarees. Sans ce filtre, le repertoire rend
# surtout des coachs sportifs en auto-entreprise — « Joris Valeux »,
# « Aurore Gaillard » : de vraies personnes, mais pas des clubs, et personne
# a qui vendre des photos d'adherents.
NATURE_ASSOCIATION = "9210,9220,9221,9222,9223,9224,9230,9240,9260"

# Des associations classees « sport » qui n'ont pas d'adherents a photographier :
# un comite d'oeuvres sociales de mairie, une amicale du personnel, un comite
# departemental. On ne vend pas de portraits a un CCAS.
PAS_UN_CLUB = re.compile(
    r"\bccas\b|oeuvres soc|\bcos\b|amicale du personnel|comite d'entreprise|"
    r"comite social|\bcse\b|mutuelle|syndicat|office municipal|\bomps\b|"
    r"federation francaise|ligue (?:regionale|nationale)", re.I)


def _interroger(codes_naf, codes_postaux, page=1, par_page=25):
    params = {
        "activite_principale": ",".join(codes_naf),
        "code_postal": ",".join(codes_postaux[:100]),
        "nature_juridique": NATURE_ASSOCIATION,
        "etat_administratif": "A",
        "per_page": par_page, "page": page,
    }
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=25) as rep:
        return json.load(rep)


def explorer(lieu, rayon_km=RAYON_DEFAUT, familles=None, maximum=MAX_DEFAUT):
    """Les clubs du secteur qui ne sont pas encore en fiche."""
    point, communes = communes_autour(lieu, rayon_km)
    if not point:
        return {"ok": False, "error": "Lieu inconnu : %s" % lieu}

    codes_postaux = []
    for c in communes:
        for cp in (c.get("cps") or []):
            if cp not in codes_postaux:
                codes_postaux.append(cp)
    if not codes_postaux:
        return {"ok": False, "error": "Aucune commune trouvée autour de %s" % lieu}

    cps_secteur = set(codes_postaux)
    naf = [FAMILLES[f][0] for f in (familles or DEFAUT) if f in FAMILLES]
    connus = _connus()
    vus, candidats, total = set(), [], 0
    soucis = []

    page = 1
    while len(candidats) < maximum and page <= 8:
        try:
            d = _interroger(naf, codes_postaux, page=page)
        except Exception as exc:  # noqa: BLE001 — le repertoire peut ne pas repondre
            soucis.append("Répertoire officiel injoignable : %s" % exc)
            break
        total = d.get("total_results") or total
        lot = d.get("results") or []
        if not lot:
            break
        for r in lot:
            nom = (r.get("nom_complet") or r.get("nom_raison_sociale") or "").strip()
            if not nom:
                continue
            if PAS_UN_CLUB.search(util.strip_accents(nom)):
                continue
            cle = util.norm_key(nom)
            if cle in vus:
                continue
            vus.add(cle)
            siege = r.get("siege") or {}
            # Le filtre par code postal porte sur TOUS les etablissements :
            # une federation parisienne avec une antenne dans le Var remontait
            # donc dans une recherche autour de Draguignan. On ne garde que
            # celles dont le SIEGE est dans le secteur.
            if (siege.get("code_postal") or "") not in cps_secteur:
                continue
            # Le sigle seul compte aussi : la fiche dit souvent « ASPTT ».
            sigle_cle = util.norm_key(r.get("sigle") or "")
            if cle in connus or (sigle_cle and sigle_cle in connus):
                continue
            lat, lon = siege.get("latitude"), siege.get("longitude")
            km = None
            try:
                if lat and lon:
                    km = round(_distance(point["lat"], point["lon"],
                                         float(lat), float(lon)), 1)
            except (TypeError, ValueError):
                km = None
            candidats.append({
                "nom": util.title_name(nom) if nom.isupper() else nom,
                "nom_brut": nom,
                "sigle": r.get("sigle"),
                "siren": r.get("siren"),
                "ville": (siege.get("libelle_commune") or "").title(),
                "code_postal": siege.get("code_postal"),
                "adresse": siege.get("adresse"),
                "dept": siege.get("departement"),
                "activite": siege.get("activite_principale"),
                "cree_le": siege.get("date_creation"),
                "km_du_point": km,
                "fiche": "https://annuaire-entreprises.data.gouv.fr/entreprise/%s"
                         % (r.get("siren") or ""),
            })
            if len(candidats) >= maximum:
                break
        page += 1

    candidats.sort(key=lambda c: (c["km_du_point"] is None, c["km_du_point"] or 0))
    return {"ok": True, "lieu": point.get("commune") or lieu,
            "rayon_km": rayon_km, "communes": len(communes),
            "total_repertoire": total, "deja_en_fiche": len(vus) - len(candidats),
            "candidats": candidats, "soucis": soucis,
            "familles": [FAMILLES[f][1] for f in (familles or DEFAUT) if f in FAMILLES]}


# ---------------------------------------------------------------------------
# Le ratissage : tout un couloir d'un coup
# ---------------------------------------------------------------------------
# Une ville a la fois ne suffit pas quand la semaine est vide : ce qu'il faut
# alors, c'est tout ce qui existe entre Draguignan et Monaco, range par
# distance de la maison. On balaye donc une suite de centres qui se
# recouvrent, et on fusionne.

COULOIR = ["Draguignan", "Le Muy", "Fréjus", "Saint-Raphaël", "Grasse",
           "Cannes", "Antibes", "Vence", "Nice", "Menton"]

# Ce qu'on reconnait dans un nom d'association. Sert a ranger, pas a decider :
# un club qu'on ne sait pas classer reste dans la liste, sans etiquette.
DISCIPLINES = [
    ("Football", r"\bfoot|\bfc\b|\bus\b.*foot|futsal"),
    ("Rugby", r"\brugby|\bxv\b|ovalie"),
    ("Basket", r"\bbasket|\bbc\b\s"),
    ("Handball", r"\bhand\b|handball"),
    ("Volley", r"\bvolley|beach.?volley"),
    ("Gym & rythmique", r"\bgym|gymnastique|\bgr\b|rythmique|trampolin|acrosport"),
    ("Danse", r"\bdanse|danci|ballet|hip.?hop|flamenc|tango|zumba"),
    ("Badminton", r"badminton|\bbad\b"),
    ("Tennis & raquettes", r"\btennis|\bpadel|squash|ping|pongiste|tennis de table"),
    ("Arts martiaux", r"judo|karat|taekwondo|aikido|kung.?fu|viet|krav|\bmma\b|"
                      r"jiu.?jitsu|lutte|boxe|kick|self.?defense|dojo|budo|sambo"),
    ("Athlétisme & course", r"athlet|\bcourse|foulee|trail|marathon|running|\bac\b\s"),
    ("Natation & eau", r"natation|nageur|aquat|plongee|water.?polo|triathlon"),
    ("Voile & nautisme", r"voile|nautique|yacht|aviron|kayak|cano|surf|paddle|regate"),
    ("Équitation", r"equit|cheval|poney|hippi|cavali|ecurie|haras"),
    ("Cyclisme", r"cycl|\bvtt\b|velo\b|\bbmx\b"),
    ("Auto & moto", r"\bauto\b|moto|karting|\bquad\b|rallye|\bdrift"),
    ("Escalade & montagne", r"escalade|alpin|grimpe|randonn|speleo|\bcaf\b"),
    ("Tir & précision", r"\btir\b|archer|\barc\b|petanque|bouliste|boules|billard"),
    ("Patinage & glace", r"patin|glace|hockey|roller|skate"),
    ("Forme & bien-être", r"\bgym\b.*volontaire|fitness|pilates|yoga|musculation|"
                          r"\bforme\b|sante"),
    ("Multisport & omnisports", r"omnisport|multi.?sport|\basptt\b|\bsport\b"),
]
_DISCIPLINES = [(nom, re.compile(motif, re.I)) for nom, motif in DISCIPLINES]


def discipline(nom):
    """Ce que le nom laisse deviner. Rien d'affirme : une etiquette pour ranger."""
    plat = util.strip_accents(str(nom or ""))
    for etiquette, motif in _DISCIPLINES:
        if motif.search(plat):
            return etiquette
    return None


def ratisser(villes=None, rayon_km=15, depuis=None, maximum=1200):
    """Tout le couloir d'un coup, range par distance de la maison.

    `depuis` est le point de reference pour la distance — Cannes par defaut,
    parce que c'est de la que part la voiture.
    """
    villes = villes or COULOIR
    origine = situer(depuis) if depuis else None
    if origine is None:
        origine = {"lat": geo.CANNES_LAT, "lon": geo.CANNES_LON, "nom": "Cannes"}

    tous, vus, soucis, explores = [], set(), [], []
    for v in villes:
        d = explorer(v, rayon_km=rayon_km, maximum=400)
        if not d.get("ok"):
            soucis.append("%s : %s" % (v, d.get("error")))
            continue
        explores.append(d["lieu"])
        for c in d["candidats"]:
            cle = (util.norm_key(c["nom"]), c.get("code_postal"))
            if cle in vus:
                continue
            vus.add(cle)
            c = dict(c)
            c["discipline"] = discipline(c["nom"])
            tous.append(c)
        if len(tous) >= maximum:
            break

    # La distance depuis la maison, pas depuis le centre interroge : c'est
    # elle qui decide si on y va.
    cps = {}
    for c in tous:
        cps.setdefault(c.get("code_postal"), []).append(c)
    for dept in sorted({(c.get("dept") or "") for c in tous if c.get("dept")}):
        for commune in communes_du_dept(dept):
            km = round(_distance(origine["lat"], origine["lon"],
                                 commune["lat"], commune["lon"]), 1)
            for cp in commune.get("cps") or []:
                for c in cps.get(cp, []):
                    if c.get("km") is None or km < c["km"]:
                        c["km"] = km
    tous.sort(key=lambda c: (c.get("km") is None, c.get("km") or 0, c["nom"]))
    return {"ok": True, "villes": explores, "rayon_km": rayon_km,
            "depuis": origine.get("nom") or "Cannes",
            "total": len(tous), "candidats": tous[:maximum], "soucis": soucis}
