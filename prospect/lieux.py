# -*- coding: utf-8 -*-
"""Resoudre un lieu, et dire sur quelle preuve on s'appuie.

Pourquoi ce module existe. Le 14 septembre, un tournoi de Vedene (Vaucluse)
est entre en base comme Vidauban (Var). Les deux fiches etaient credibles :
un nom de commune, un code postal, une distance calculee. Rien ne signalait
que le nom venait d'une transcription et le code postal d'une deduction sur
ce nom. Julien a demarche un club a 231 km en croyant qu'il etait a 65.

La lecon n'est pas « mieux deviner » : c'est « savoir ce qu'on sait ». Ce
module ne renvoie donc jamais un lieu seul. Il renvoie le lieu, la preuve sur
laquelle il repose, et le conflit s'il y en a un.

La source est le referentiel officiel des communes (geo.api.gouv.fr) : pas de
cle, pas de compte, 35 000 communes contre les 261 de notre table maison. Les
reponses sont gardees sur disque, donc le scan quotidien fonctionne meme sans
reseau une fois les lieux connus.
"""

import io
import json
import os
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

from . import util

API = "https://geo.api.gouv.fr/communes"
CHAMPS = "nom,code,codesPostaux,centre,departement"
_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(_DATA, "communes-cache.json")

# Le referentiel entier : 35 000 communes, 3,6 Mo, six secondes de
# telechargement. Interroger le reseau mot par mot mettait plusieurs minutes a
# depouiller un seul agenda ; une fois le fichier la, tout devient local et
# le scan quotidien fonctionne meme sans connexion.
REFERENTIEL = os.path.join(_DATA, "communes-france.json")
REFERENTIEL_URL = (API + "?fields=nom,code,codesPostaux,departement&format=json")
REFERENTIEL_JOURS = 180

_index = None

_cache = None
_hors_ligne = False
# Une requete qui echoue n'est pas une panne de reseau. Basculer hors ligne au
# premier incident coupait toutes les recherches suivantes du processus : sur
# « CROSS UNSS DISTRICT CANNES », un echec sur « CROSS » empechait d'interroger
# « CANNES », et la fiche restait sans lieu.
_echecs = 0
ECHECS_AVANT_ABANDON = 4


# --------------------------------------------------------------------------
# Le cache : indispensable, le scan tourne sans surveillance a 07h30.
# --------------------------------------------------------------------------

def _charger():
    global _cache
    if _cache is None:
        try:
            with io.open(CACHE, encoding="utf-8") as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {"nom": {}, "cp": {}}
        _cache.setdefault("nom", {})
        _cache.setdefault("cp", {})
    return _cache


def _sauver():
    if _cache is None:
        return
    try:
        dossier = os.path.dirname(CACHE)
        if not os.path.isdir(dossier):
            os.makedirs(dossier)
        with io.open(CACHE, "w", encoding="utf-8") as f:
            json.dump(_cache, f, ensure_ascii=False, indent=1, sort_keys=True)
    except OSError:
        pass


def _telecharger_referentiel():
    """Rapatrie la liste complete des communes. Silencieux en cas d'echec."""
    try:
        req = urllib.request.Request(REFERENTIEL_URL,
                                     headers={"User-Agent": "SnapActions-CRM"})
        with urllib.request.urlopen(req, timeout=90) as rep:
            brut = rep.read()
        lignes = json.loads(brut.decode("utf-8"))
        if len(lignes) < 30000:
            return False               # reponse tronquee : on garde l'ancienne
        if not os.path.isdir(_DATA):
            os.makedirs(_DATA)
        with io.open(REFERENTIEL, "w", encoding="utf-8") as f:
            json.dump(lignes, f, ensure_ascii=False)
        return True
    except Exception:
        return False


def _index_local(forcer=False):
    """{nom normalise: [communes]} et {code postal: [communes]}, en memoire."""
    global _index
    if _index is not None and not forcer:
        return _index

    besoin = True
    if os.path.exists(REFERENTIEL):
        age = (time.time() - os.path.getmtime(REFERENTIEL)) / 86400.0
        besoin = age > REFERENTIEL_JOURS
    if besoin:
        _telecharger_referentiel()

    par_nom, par_cp = {}, {}
    try:
        with io.open(REFERENTIEL, encoding="utf-8") as f:
            lignes = json.load(f)
    except (OSError, ValueError):
        _index = {"nom": {}, "cp": {}}
        return _index

    for l in lignes:
        fiche = {
            "commune": l.get("nom"), "insee": l.get("code"),
            "cps": l.get("codesPostaux") or [],
            "dept": (l.get("departement") or {}).get("code"),
            "dept_nom": (l.get("departement") or {}).get("nom"),
            "lon": None, "lat": None,
        }
        par_nom.setdefault(cle(l.get("nom")), []).append(fiche)
        for cp in fiche["cps"]:
            par_cp.setdefault(str(cp), []).append(fiche)
    _index = {"nom": par_nom, "cp": par_cp}
    return _index


def cle(texte):
    """Normalise un nom de commune pour la comparaison."""
    if not texte:
        return ""
    s = unicodedata.normalize("NFD", str(texte))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    s = re.sub(r"[-'’./,]", " ", s)
    s = re.sub(r"\bst\b", "saint", s)
    s = re.sub(r"\bste\b", "sainte", s)
    return " ".join(s.split())


# --------------------------------------------------------------------------
# L'appel au referentiel
# --------------------------------------------------------------------------

def _interroger(params):
    global _hors_ligne, _echecs
    if _hors_ligne:
        return []
    url = API + "?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "SnapActions-CRM"})
        with urllib.request.urlopen(req, timeout=10) as rep:
            donnees = json.loads(rep.read().decode("utf-8"))
        _echecs = 0
        return donnees
    except urllib.error.HTTPError:
        # Le referentiel a repondu, meme pour dire non : le reseau va bien.
        _echecs = 0
        return []
    except Exception:
        # Sans reseau, on ne retente pas a chaque ligne d'un import de 200
        # fiches. Mais il faut plusieurs echecs d'affilee pour l'affirmer.
        _echecs += 1
        if _echecs >= ECHECS_AVANT_ABANDON:
            _hors_ligne = True
        return []


def _lire(brut):
    if not brut:
        return None
    centre = (brut.get("centre") or {}).get("coordinates") or [None, None]
    return {
        "commune": brut.get("nom"),
        "insee": brut.get("code"),
        "cps": brut.get("codesPostaux") or [],
        "dept": (brut.get("departement") or {}).get("code"),
        "dept_nom": (brut.get("departement") or {}).get("nom"),
        "lon": centre[0], "lat": centre[1],
    }


def par_code_postal(cp):
    """La ou les communes d'un code postal. Le cache d'abord."""
    cp = re.sub(r"\D", "", str(cp or ""))
    if len(cp) != 5:
        return []
    local = _index_local()["cp"].get(cp)
    if local:
        return local
    c = _charger()
    if cp in c["cp"]:
        return c["cp"][cp]
    lignes = [_lire(x) for x in _interroger({"codePostal": cp, "fields": CHAMPS})]
    lignes = [x for x in lignes if x]
    if lignes:
        c["cp"][cp] = lignes
        _sauver()
    return lignes


def par_nom(nom):
    """Les communes portant ce nom. Plusieurs si le nom est ambigu."""
    k = cle(nom)
    if len(k) < 3:
        return []
    index = _index_local()["nom"]
    if index:
        # Le referentiel local fait autorite : s'il ne connait pas ce nom,
        # c'est qu'aucune commune francaise ne le porte. Inutile d'appeler.
        return index.get(k, [])
    c = _charger()
    if k in c["nom"]:
        return c["nom"][k]
    lignes = [_lire(x) for x in
              _interroger({"nom": nom, "fields": CHAMPS, "limit": 10,
                           "boost": "population"})]
    # L'API accepte les approximations ; on ne garde que les noms identiques,
    # sinon « Vence » ramenerait « Venance » et on repartirait pour un Vidauban.
    lignes = [x for x in lignes if x and cle(x["commune"]) == k]
    if lignes:
        c["nom"][k] = lignes
        _sauver()
    return lignes


# --------------------------------------------------------------------------
# La resolution, avec sa preuve
# --------------------------------------------------------------------------

def _meme_endroit(k_ville, candidats):
    """Le nom donne est-il un quartier, une adresse ou un ancien nom du meme lieu ?

    On exige que l'un des deux noms contienne l'autre comme suite de mots
    entiere : « cannes » est bien dans « cannes la bocca », mais « vence » ne
    l'est pas dans « vidauban ».
    """
    mots_ville = k_ville.split()
    for c in candidats:
        k_c = cle(c["commune"])
        mots_c = k_c.split()
        if _contient(mots_ville, mots_c) or _contient(mots_c, mots_ville):
            return c
    return None


def _contient(grand, petit):
    if not petit or len(petit) > len(grand):
        return False
    return any(grand[i:i + len(petit)] == petit
               for i in range(len(grand) - len(petit) + 1))


def resoudre(ville=None, code_postal=None, dept=None, texte=None):
    """Identifie un lieu et dit sur quoi il repose.

    Renvoie toujours un dictionnaire, jamais None : l'absence de reponse est
    elle-meme une information que l'appelant doit pouvoir afficher.

    preuve   : code_postal | nom_unique | nom_et_departement | nom_dans_texte
    confiance: sure | probable | incertain | aucune
    conflit  : la phrase a montrer a Julien quand deux indices se contredisent
    """
    out = {"commune": None, "cp": None, "dept": None, "insee": None,
           "lat": None, "lon": None, "preuve": None, "confiance": "aucune",
           "conflit": None, "hors_referentiel": False}

    par_cp = par_code_postal(code_postal) if code_postal else []
    k_ville = cle(ville)

    # 1. Le code postal est la preuve la plus forte : il porte le departement.
    if par_cp:
        choisi = par_cp[0]
        if k_ville:
            exact = [x for x in par_cp if cle(x["commune"]) == k_ville]
            if exact:
                choisi = exact[0]
            elif _meme_endroit(k_ville, par_cp):
                # « Cannes la Bocca » est un quartier de Cannes, « Trigavou »
                # une commune deleguee de Pleslin-Trigavou, et « 2 Av. de
                # Suffren, 75007 Paris » contient Paris. Ce ne sont pas des
                # contradictions : crier au loup sur ces cas-la ferait ignorer
                # l'alerte le jour ou elle dit vrai.
                choisi = _meme_endroit(k_ville, par_cp)
            else:
                # Le nom et le code postal ne designent pas le meme endroit.
                # C'est exactement le cas Vedene / Vidauban : on le dit.
                autres = par_nom(ville)
                out["conflit"] = (
                    "le code postal %s désigne %s, pas %s"
                    % (code_postal, choisi["commune"], ville)
                    + (" (%s est en %s)" % (ville, autres[0]["dept"]) if autres else ""))
        out.update(commune=choisi["commune"], insee=choisi["insee"],
                   cp=str(code_postal), dept=choisi["dept"],
                   lat=choisi["lat"], lon=choisi["lon"],
                   preuve="code_postal",
                   confiance="incertain" if out["conflit"] else "sure")
        return out

    # 2. Le nom seul, s'il ne designe qu'une commune en France.
    if k_ville:
        cands = par_nom(ville)
        if dept:
            filtre = [x for x in cands if x["dept"] == str(dept)]
            if filtre:
                c = filtre[0]
                out.update(commune=c["commune"], insee=c["insee"],
                           cp=(c["cps"] or [None])[0], dept=c["dept"],
                           lat=c["lat"], lon=c["lon"],
                           preuve="nom_et_departement", confiance="sure")
                return out
            if cands:
                out["conflit"] = (
                    "aucune commune nommée %s dans le département %s — "
                    "il y en a en %s" % (ville, dept,
                                         ", ".join(sorted({x["dept"] for x in cands}))))
                out["confiance"] = "incertain"
                return out
        if len(cands) == 1:
            c = cands[0]
            out.update(commune=c["commune"], insee=c["insee"],
                       cp=(c["cps"] or [None])[0], dept=c["dept"],
                       lat=c["lat"], lon=c["lon"],
                       preuve="nom_unique",
                       confiance="incertain" if out["conflit"] else "sure")
            return out
        if len(cands) > 1:
            out["conflit"] = ("%d communes s'appellent %s (%s) — il faut un code "
                              "postal pour trancher"
                              % (len(cands), ville,
                                 ", ".join(sorted({x["dept"] for x in cands}))))
            out["confiance"] = "incertain"
            return out
        # Aucun resultat : commune etrangere, ou nom mal transcrit.
        out["hors_referentiel"] = True
        out["conflit"] = ("« %s » n'existe pas dans le référentiel des communes "
                          "françaises" % ville)
        return out

    # 3. Rien d'explicite : on cherche une commune citee dans le texte.
    if texte:
        trouve = commune_dans_texte(texte)
        if trouve:
            out.update(commune=trouve["commune"], insee=trouve["insee"],
                       cp=(trouve["cps"] or [None])[0], dept=trouve["dept"],
                       lat=trouve["lat"], lon=trouve["lon"],
                       preuve="nom_dans_texte", confiance="probable")
    return out


# Mots qui reviennent sans cesse dans les titres d'evenements et ne sont
# jamais des communes. Les tester coutait un aller-retour reseau chacun.
MOTS_NON_COMMUNES = set("""tournoi tournois competition championnat challenge
coupe trophee cross course match rencontre plateau gala open finale district
departemental regional national college lycee ecole stade gymnase complexe
salle parc sportif sportive club association equipe equipes minimes benjamins
cadets juniors seniors veterans filles garcons masculin feminin journee
matinee soiree edition septembre octobre novembre decembre janvier fevrier
mars avril juin juillet aout lundi mardi mercredi jeudi vendredi samedi
dimanche unss ffbb fff volley basket foot football hand handball rugby tennis
judo karate danse escalade natation cyclo sortie rando randonnee""".split())


def commune_dans_texte(texte, minimum=4, depts_probables=None):
    """La commune citee dans un texte libre — l'adresse d'un stade, par exemple.

    On ne teste que des suites de mots capitalises ou suivant un tiret : passer
    tout le referentiel sur chaque texte couterait un appel reseau par mot.
    """
    if not texte:
        return None
    # Un titre tout en capitales — « CROSS UNSS DISTRICT CANNES » — ne forme
    # qu'un seul bloc : il faut aussi essayer les suites de mots a l'interieur,
    # sinon « CANNES » n'est jamais teste. On va du plus long au plus court,
    # pour que « La Roquebrussanne » l'emporte sur « La ».
    blocs = re.findall(r"[A-Za-zÀ-ÿ'\u2019-]+(?:[ ][A-Za-zÀ-ÿ'\u2019-]+){0,5}", str(texte))
    candidats = []
    for bloc in blocs:
        # Le trait d'union sert aux deux : il lie « Saint-Laurent-du-Var » et
        # il colle « Roquebrussanne-Tour du Piberon ». On essaie donc le bloc
        # tel quel, puis decoupe aux tirets.
        for variante in (bloc, bloc.replace("-", " ")):
            mots = [m for m in variante.split() if m]
            for taille in range(min(4, len(mots)), 0, -1):
                for debut in range(len(mots) - taille + 1):
                    candidats.append(" ".join(mots[debut:debut + taille]))

    # Le referentiel est en memoire : tester un candidat ne coute plus rien,
    # donc on les teste tous. Le plafond qui protegeait les appels reseau
    # coupait la boucle avant le bon candidat — « Le Cannet des Maures » (83)
    # se resolvait en « Le Cannet » (06), a cent kilometres de la.
    vus, meilleur = set(), None
    for bout in candidats:
        b = bout.strip(" -'\u2019")
        k = cle(b)
        if len(k) < minimum or k in vus or k in MOTS_NON_COMMUNES:
            continue
        # Une suite dont chaque mot est ecarte n'a pas besoin d'etre testee.
        if all(m in MOTS_NON_COMMUNES for m in k.split()):
            continue
        vus.add(k)
        cands = par_nom(b)
        if depts_probables:
            # Un nom court tombe souvent sur une homonyme a l'autre bout du
            # pays : « La Londe » existe en Seine-Maritime, alors que le titre
            # « La Londe-Dolmen de Gaoutabry » parle du Var. Quand on sait de
            # quelle region vient la fiche, on s'y tient.
            proches = [c for c in cands if c["dept"] in depts_probables]
            if cands and not proches:
                continue
            cands = proches or cands
        if len(cands) == 1:
            if meilleur is None or len(b) > len(meilleur["commune"] or ""):
                meilleur = cands[0]
    return meilleur


def verdict_court(res):
    """Une phrase pour l'interface."""
    if res.get("conflit"):
        return res["conflit"]
    if not res.get("commune"):
        return "lieu non identifié"
    preuves = {"code_postal": "le code postal",
               "nom_unique": "le nom de commune",
               "nom_et_departement": "le nom et le département",
               "nom_dans_texte": "la commune citée dans le texte"}
    return "%s (%s) — d'après %s" % (res["commune"], res["dept"],
                                     preuves.get(res["preuve"], "?"))
