# -*- coding: utf-8 -*-
"""Distance depuis Cannes et categories de jeunes.

Deux services rendus a l'agent qui depouille les flyers :

1. `depuis_cannes("Draguignan")` -> combien de route, et si c'est raisonnable ;
2. `categories("U9 a U13")`      -> les annees de naissance correspondantes.

La table de distances (data/distances-cannes.csv) est routiere et approximative
— a quelques kilometres pres. C'est suffisant pour decider si on y va ; ca ne
remplace pas un itineraire.
"""

import csv
import io
import json
import os
import re
import unicodedata
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(BASE, "data", "distances-cannes.csv")

# Ce qui decide d'un deplacement, c'est le temps de route, pas la distance :
# soixante-cinq kilometres d'autoroute font cinquante minutes, les memes
# soixante-cinq kilometres dans l'arriere-pays en font le double. Juger aux
# kilometres classait Vidauban « loin » a 53 minutes de Cannes.
SEUILS_MINUTES = [
    (45, "proche", "Aller-retour dans la journee sans y penser."),
    (90, "correct", "Faisable, prevoir la matinee ou l'apres-midi."),
    (180, "loin", "Ca ne vaut le coup que si le plateau est gros, ou groupe avec autre chose."),
    (100000, "hors zone", "Trop loin pour un tournoi isole."),
]

# Repli quand le temps de route est inconnu : on juge aux kilometres.
SEUILS_KM = [
    (40, "proche", "Aller-retour dans la journee sans y penser."),
    (90, "correct", "Faisable, prevoir la matinee ou l'apres-midi."),
    (200, "loin", "Ca ne vaut le coup que si le plateau est gros, ou groupe avec autre chose."),
    (10000, "hors zone", "Trop loin pour un tournoi isole."),
]


# Au-dela, le temps de route annonce ne tient pas debout : 184 km en 50
# minutes, c'est 220 km/h. Quand le geocodeur se trompe de commune, il rend un
# temps court pour une distance longue — et le verdict devient « correct »
# pour un trajet de trois heures. Dans ce cas, les kilometres font foi.
VITESSE_MAX = 110


def juger(km, minutes=None):
    """Le verdict d'un trajet : au temps de route quand il est credible."""
    if minutes and km and minutes > 0 and (km * 60.0 / minutes) > VITESSE_MAX:
        minutes = None
    table, valeur = (SEUILS_MINUTES, minutes) if minutes is not None \
        else (SEUILS_KM, km)
    if valeur is None:
        return None, None
    for limite, verdict, commentaire in table:
        if valeur <= limite:
            return verdict, commentaire
    return table[-1][1], table[-1][2]

# Point de depart de tous les trajets.
CANNES_LAT, CANNES_LON = 43.5528, 7.0174

# Le second point d'appui. SnapActions ne rayonne pas que depuis Cannes : la
# zone d'Hyeres est un bassin de travail a part entiere, et un evenement a
# vingt minutes d'Hyeres est faisable meme s'il est a deux heures de Cannes.
HYERES_LAT, HYERES_LON = 43.1206, 6.1286
TABLE_HYERES = os.path.join(BASE, "data", "distances-hyeres.csv")

# Au-dela, on n'est plus « autour d'Hyeres ».
HYERES_MINUTES = 45

UA = "prospect-clubs/1.0 (CRM local)"
DELAI = 20

# Ce que le geocodeur doit rendre pour qu'on accepte le resultat : une commune,
# pas un lieu-dit commercial ni un nom de tournoi tombe par hasard.
LIEUX = {"city", "town", "village", "municipality", "hamlet", "suburb"}

_cache = None
_echecs = set()      # communes deja cherchees en vain : on n'insiste pas


def _reseau(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=DELAI).read().decode("utf-8", "replace")


# Nominatim demande une requete par seconde au maximum. Une veille qui importe
# cent communes d'un coup se ferait bloquer sans ce frein.
_dernier_appel = [0.0]


def _patienter(minimum=1.1):
    import time
    attente = minimum - (time.time() - _dernier_appel[0])
    if attente > 0:
        time.sleep(attente)
    _dernier_appel[0] = time.time()


def _geocoder(commune, code_postal=None):
    """Position d'une commune francaise, via OpenStreetMap. None si ce n'en est pas une."""
    terme = " ".join(str(x) for x in (commune, code_postal) if x).strip()
    if not terme:
        return None
    url = ("https://nominatim.openstreetmap.org/search?format=json&limit=1"
           "&countrycodes=fr&addressdetails=1&q=" + urllib.parse.quote(terme))
    try:
        _patienter()
        res = json.loads(_reseau(url))
    except Exception:
        return None
    if not res:
        return None
    r = res[0]
    genre = (r.get("addresstype") or r.get("type") or "").lower()
    classe = (r.get("class") or "").lower()
    adresse = r.get("address") or {}
    nom = (adresse.get("city") or adresse.get("town") or adresse.get("village")
           or adresse.get("municipality"))

    # Interroge avec un code postal, Nominatim repond « postcode » et non
    # « city » : le filtre d'origine rejetait donc Saint-Etienne 42000, et la
    # commune retombait sur un homonyme du 06. On accepte tout ce qui est un
    # lieu ou une limite administrative, et on exige une commune exploitable.
    if genre not in LIEUX and classe not in ("place", "boundary"):
        return None
    if not nom:
        nom = str(r.get("display_name", "")).split(",")[0].strip()
    if not nom or nom.isdigit():
        return None
    try:
        return {"nom": nom, "lat": float(r["lat"]), "lon": float(r["lon"]),
                "cp": adresse.get("postcode")}
    except (KeyError, TypeError, ValueError):
        return None


def _router(lat, lon, origine=None):
    """Distance et duree routieres reelles depuis Cannes, via OSRM.

    En HTTP : le service public refuse la poignee de main TLS depuis Python.
    Rien de sensible ne transite, seulement deux couples de coordonnees.
    """
    o_lat, o_lon = origine or (CANNES_LAT, CANNES_LON)
    url = ("http://router.project-osrm.org/route/v1/driving/"
           "%s,%s;%s,%s?overview=false" % (o_lon, o_lat, lon, lat))
    try:
        route = json.loads(_reseau(url))["routes"][0]
        return int(round(route["distance"] / 1000.0)), int(round(route["duration"] / 60.0))
    except Exception:
        return None, None


def _vol_doiseau(lat, lon):
    """Repli hors ligne : distance a vol d'oiseau majoree du detour routier moyen."""
    import math
    r = 6371.0
    d_lat = math.radians(lat - CANNES_LAT)
    d_lon = math.radians(lon - CANNES_LON)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(CANNES_LAT)) * math.cos(math.radians(lat))
         * math.sin(d_lon / 2) ** 2)
    vol = 2 * r * math.asin(min(1.0, math.sqrt(a)))
    km = int(round(vol * 1.25))          # le detour routier, en moyenne
    return km, int(round(km / 75.0 * 60))  # 75 km/h de moyenne porte a porte


def _apprendre(commune, cp, km, minutes):
    """Ajoute la commune a la table pour ne plus jamais la recalculer."""
    # Deux imports de suite ecrivaient deux fois la meme ligne.
    for r in _table():
        if r.get("cp") and cp and r["cp"] == str(cp):
            return
        if not cp and r["cle"] == _cle(commune):
            return
    ligne = "%s;%s;%d;%d\n" % (commune, cp or "", km, minutes)
    try:
        with io.open(TABLE, "a", encoding="utf-8") as f:
            f.write(ligne)
    except OSError:
        pass
    if _cache is not None:
        _cache.append({"commune": commune, "cp": cp or "", "km": km,
                       "minutes": minutes, "cle": _cle(commune)})


def calculer(commune=None, code_postal=None):
    """Distance d'une commune absente de la table. None si on n'a pas pu."""
    marque = (_cle(commune), str(code_postal or ""))
    if not marque[0] and not marque[1]:
        return None
    if marque in _echecs:
        return None
    pos = _geocoder(commune, code_postal)
    if not pos:
        _echecs.add(marque)
        return None
    km, minutes = _router(pos["lat"], pos["lon"])
    if km is None:
        km, minutes = _vol_doiseau(pos["lat"], pos["lon"])
    _apprendre(pos["nom"], pos.get("cp") or code_postal, km, minutes)
    return {"commune": pos["nom"], "cp": pos.get("cp") or code_postal,
            "km": km, "minutes": minutes}


def _cle(texte):
    """Normalise un nom de commune : minuscules, sans accent ni ponctuation."""
    if not texte:
        return ""
    s = unicodedata.normalize("NFD", str(texte))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    for vieux in ("-", "'", "’", ".", ",", "/"):
        s = s.replace(vieux, " ")
    for mot in (" sur ", " sous ", " les ", " le ", " la ", " de ", " du ", " des ", " d "):
        s = s.replace(mot, " ")
    return " ".join(s.split())


def _table():
    global _cache
    if _cache is not None:
        return _cache
    lignes = []
    try:
        with io.open(TABLE, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                try:
                    r["km"] = int(r["km"])
                    r["minutes"] = int(r["minutes"])
                except (KeyError, TypeError, ValueError):
                    continue
                r["cle"] = _cle(r.get("commune"))
                lignes.append(r)
    except OSError:
        lignes = []
    _cache = lignes
    return lignes


_cache_hyeres = None


def _table_hyeres():
    """La table des trajets depuis Hyeres, apprise au fil de l'eau."""
    global _cache_hyeres
    if _cache_hyeres is not None:
        return _cache_hyeres
    lignes = {}
    try:
        with io.open(TABLE_HYERES, encoding="utf-8") as f:
            for r in csv.DictReader(f, delimiter=";"):
                try:
                    lignes[_cle(r.get("commune"))] = int(r["minutes"])
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        pass
    _cache_hyeres = lignes
    return lignes


def _retenir_hyeres(commune, minutes):
    table = _table_hyeres()
    table[_cle(commune)] = minutes
    neuf = not os.path.exists(TABLE_HYERES)
    try:
        with io.open(TABLE_HYERES, "a", encoding="utf-8") as f:
            if neuf:
                f.write("commune;minutes\n")
            f.write("%s;%d\n" % (commune, minutes))
    except OSError:
        pass


def minutes_depuis_hyeres(commune=None, code_postal=None):
    """Temps de route depuis Hyeres, ou None si on n'a pas su le calculer."""
    if not commune and not code_postal:
        return None
    connu = _table_hyeres().get(_cle(commune))
    if connu is not None:
        return connu
    pos = _geocoder(commune, code_postal)
    if not pos:
        return None
    _km, minutes = _router(pos["lat"], pos["lon"],
                           origine=(HYERES_LAT, HYERES_LON))
    if minutes is None:
        return None
    _retenir_hyeres(pos["nom"] or commune, minutes)
    return minutes


def autour_hyeres(commune=None, code_postal=None):
    """Vrai si le lieu est dans le bassin d'Hyeres."""
    m = minutes_depuis_hyeres(commune, code_postal)
    return m is not None and m <= HYERES_MINUTES


def depuis_cannes(commune=None, code_postal=None):
    """Renvoie {commune, km, minutes, verdict, commentaire} ou None si inconnue.

    On cherche d'abord sur le nom normalise, puis sur le code postal, puis sur
    une correspondance partielle (« Saint-Laurent » retrouve « Saint-Laurent-du-Var »).
    """
    lignes = _table()
    if not lignes:
        return None
    cle = _cle(commune)
    cp = str(code_postal).strip() if code_postal else None

    trouve = None
    if cp:
        # Le code postal fait foi. « Chateauneuf 42800 » est dans la Loire, pas
        # a Chateauneuf-Grasse : sans cette regle, le rapprochement par nom
        # ramenait la commune homonyme du 06 et annoncait 19 km au lieu de 450.
        for r in lignes:
            if r.get("cp") == cp:
                trouve = r
                break
        if trouve is None:
            trouve = calculer(commune, cp)
    else:
        if cle:
            for r in lignes:
                if r["cle"] == cle:
                    trouve = r
                    break
        if trouve is None and cle and len(cle) >= 4:
            candidats = [r for r in lignes if cle in r["cle"] or r["cle"] in cle]
            if len(candidats) == 1:
                trouve = candidats[0]
        if trouve is None:
            trouve = calculer(commune, None)

    if trouve is None:
        return None

    verdict, commentaire = juger(trouve["km"], trouve.get("minutes"))
    return {"commune": trouve["commune"], "cp": trouve.get("cp"),
            "km": trouve["km"], "minutes": trouve["minutes"],
            "verdict": verdict, "commentaire": commentaire}


# ---------------------------------------------------------------------------
# Categories de jeunes
# ---------------------------------------------------------------------------
# Regle federale : pour la saison N/N+1, la categorie U<x> regroupe les
# licencies nes en (N+1-x). Un U11 de la saison 2026-2027 est ne en 2016.

TRANCHES = [
    ("U6", "5 ans", "ecole de foot, plateau, pas de score"),
    ("U7", "6 ans", "ecole de foot, plateau, pas de score"),
    ("U8", "7 ans", "ecole de foot, plateau"),
    ("U9", "8 ans", "ecole de foot, plateau"),
    ("U10", "9 ans", "foot animation, 8 contre 8"),
    ("U11", "10 ans", "foot animation, 8 contre 8"),
    ("U12", "11 ans", "pre-formation, 8 contre 8"),
    ("U13", "12 ans", "pre-formation, critere federal"),
    ("U14", "13 ans", "formation, 11 contre 11"),
    ("U15", "14 ans", "formation, 11 contre 11"),
    ("U16", "15 ans", "formation"),
    ("U17", "16 ans", "formation"),
    ("U18", "17 ans", "formation"),
    ("U19", "18 ans", "derniere categorie jeune"),
]


def saison(aujourdhui=None):
    """Renvoie l'annee de debut de la saison en cours (aout -> juillet)."""
    import datetime as dt
    d = aujourdhui or dt.date.today()
    return d.year if d.month >= 8 else d.year - 1


def categories(texte=None, aujourdhui=None):
    """Rappel des categories. Sans argument, renvoie toute la grille.

    Avec un texte (« U9 a U13 », « U11 et U13 »), ne garde que celles citees.
    Chaque entree porte l'annee de naissance pour la saison en cours.
    """
    debut = saison(aujourdhui)
    haut = (texte or "").upper()
    sortie = []
    for code, age, note in TRANCHES:
        n = int(code[1:])
        sortie.append({"categorie": code, "age": age,
                       "naissance": debut + 1 - n,
                       "format": note,
                       "saison": "%d-%d" % (debut, debut + 1)})
    if not haut:
        return sortie
    cites = set()
    import re
    nums = [int(m) for m in re.findall(r"U\s?(\d{1,2})", haut)]
    if not nums:
        return sortie
    if ("A" in haut or "-" in haut or "À" in haut) and len(nums) >= 2:
        for n in range(min(nums), max(nums) + 1):
            cites.add(n)
    else:
        cites = set(nums)
    filtre = [c for c in sortie if int(c["categorie"][1:]) in cites]
    return filtre or sortie


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        d = depuis_cannes(" ".join(sys.argv[1:]))
        print(d if d else "commune inconnue de la table")
    else:
        for c in categories():
            print("%-4s %-8s nes en %d  (%s)" % (c["categorie"], c["age"],
                                                 c["naissance"], c["format"]))


def commune_dans_texte(texte, minimum=4):
    """Cherche une commune connue citee dans un texte libre.

    Une annonce Facebook ecrit « a la Base Nature de Frejus », jamais
    « 83600 ». Sans ca la fiche arrive sans lieu, donc sans distance, donc
    invisible dans les tiroirs.

    Le texte est normalise par _cle(), exactement comme les noms stockes :
    « Saint-Laurent-du-Var » et « saint laurent var » doivent se rencontrer.
    On garde le nom le plus long pour ne pas confondre une commune avec une
    autre qui la contient.
    """
    if not texte:
        return None
    plat = " " + _cle(texte) + " "
    meilleur = None
    for ligne in _table():
        cle = (ligne.get("cle") or "").strip()
        if len(cle) < minimum:
            continue
        if (" " + cle + " ") in plat:
            if meilleur is None or len(cle) > len(meilleur.get("cle") or ""):
                meilleur = ligne
    return meilleur
