# -*- coding: utf-8 -*-
"""Repere les dates d'evenement dans un message ecrit a la main.

Concu sur de vrais messages de clubs, ou personne n'ecrit une date proprement :
« le weekend du 10/11 octobre », « vendredi 4decembre », « du 10 au 12 mars ».
L'annee est presque toujours absente : on prend la prochaine occurrence.
"""
import datetime as dt
import re

from . import util

MOIS = {
    "janvier": 1, "janv": 1, "jan": 1,
    "fevrier": 2, "fevr": 2, "fev": 2,
    "mars": 3,
    "avril": 4, "avr": 4,
    "mai": 5,
    "juin": 6,
    "juillet": 7, "juil": 7, "jui": 7,
    "aout": 8,
    "septembre": 9, "sept": 9, "sep": 9,
    "octobre": 10, "octo": 10, "oct": 10,
    "novembre": 11, "nov": 11,
    "decembre": 12, "dec": 12,
}
MOIS_NOMS = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet",
             "aout", "septembre", "octobre", "novembre", "decembre"]
JOURS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4,
         "samedi": 5, "dimanche": 6}

_MOIS_ALT = "|".join(sorted(MOIS, key=len, reverse=True))
_JOUR_ALT = "|".join(JOURS)

# « 10/11 octobre », « 10-11 octobre », « 10 et 11 octobre », « du 10 au 12 mars »
# et la forme que les affiches preferent : « samedi 19 et dimanche 20 septembre ».
PLAGE_RE = re.compile(
    r"(?:du\s+)?(?:(?:%s)\s+)?(?P<d1>\d{1,2})"
    r"\s*(?:/|-|\s+et\s+|\s+au\s+|\s*&\s*)\s*"
    r"(?:(?:%s)\s+)?(?P<d2>\d{1,2})"
    r"\s*(?P<mois>%s)\.?\s*(?P<an>\d{4})?" % (_JOUR_ALT, _JOUR_ALT, _MOIS_ALT), re.I)

# « vendredi 4 decembre 2026 », « le 4decembre », « 4 dec »
SIMPLE_RE = re.compile(
    r"(?:(?P<jour>%s)\s+)?(?:le\s+)?(?P<d>\d{1,2})\s*(?:er)?\s*"
    r"(?P<mois>%s)\.?\s*(?P<an>\d{4})?" % (_JOUR_ALT, _MOIS_ALT), re.I)

# « 12/04/2026 », « 12-04-26 »
NUM_RE = re.compile(r"(?<!\d)(?P<d>\d{1,2})[/.-](?P<m>\d{1,2})[/.-](?P<a>\d{2,4})(?!\d)")

# Mots qui nomment l'evenement autour de la date
EVENT_MOTS = re.compile(
    r"\b(tournoi\w*|comp[ée]tition|championnat|coupe|cup|open|gala|spectacle|"
    r"repr[ée]sentation|match\w*|rencontre|stage|forum|salon|f[êe]te|t[ée]l[ée]thon|"
    r"nuit|journ[ée]e|portes? ouvertes?|assembl[ée]e|loto|kermesse|d[ée]monstration|"
    r"remise des prix|photos? (?:individuelles?|d'[ée]quipe)|shooting|s[ée]ance photo)\b",
    re.I)

WEEKEND_RE = re.compile(r"\b(?:week[- ]?end|we)\b", re.I)


def _annee_probable(mois, jour, aujourd_hui=None, marge=45):
    """Sans annee ecrite : la prochaine occurrence, avec une petite tolerance
    pour une date qui vient tout juste de passer."""
    today = aujourd_hui or util.today()
    for annee in (today.year, today.year + 1):
        try:
            d = dt.date(annee, mois, jour)
        except ValueError:
            continue
        if (d - today).days >= -marge:
            return d
    return None


def _label(texte, debut, fin, defaut="Événement"):
    """Nomme l'evenement d'apres les mots autour de la date.

    On recolle le groupe nominal complet : « nuit du telethon » plutot que
    « telethon », « tournoi de printemps » plutot que « tournoi ».
    """
    avant = texte[max(0, debut - 130):debut]
    apres = texte[fin:fin + 90]

    ancre, zone, dans_avant = None, None, True
    for m in EVENT_MOTS.finditer(avant):
        ancre, zone = m, avant           # le plus proche avant la date
    if not ancre:
        m = EVENT_MOTS.search(apres)
        if m:
            ancre, zone, dans_avant = m, apres, False
    if not ancre:
        return defaut

    mot = ancre.group(0)
    # Complement a droite : « tournoi DE PRINTEMPS »
    droite = zone[ancre.end():ancre.end() + 34]
    m_droite = re.match(r"\s+(?:du|de la|de|des|d')\s+[\w'À-ſ-]+", droite)
    if m_droite:
        mot += m_droite.group(0).rstrip(" ,.;")
    # Complement a gauche : « NUIT DU telethon »
    gauche = zone[:ancre.start()]
    m_gauche = re.search(r"([\w'À-ſ-]+)\s+(?:du|de la|de|des|d')\s*$", gauche)
    if m_gauche and m_gauche.group(1).lower() not in ("le", "la", "les", "un", "une"):
        mot = "%s %s" % (m_gauche.group(0).strip(), mot)

    mot = re.sub(r"\s{2,}", " ", mot).strip(" ,.;:")
    return (mot[:70] or defaut).capitalize()


def extraire(texte, aujourd_hui=None):
    """Renvoie une liste d'evenements {nom, debut, fin, extrait, confiance}."""
    if not texte:
        return []
    brut = texte
    plat = util.strip_accents(texte).lower()
    trouves, pris = [], []

    def chevauche(a, b):
        return any(not (b <= s or a >= e) for s, e in pris)

    # 1) plages « 10/11 octobre », « du 10 au 12 mars »
    for m in PLAGE_RE.finditer(plat):
        mois = MOIS.get(m.group("mois"))
        if not mois:
            continue
        d1, d2 = int(m.group("d1")), int(m.group("d2"))
        if not (1 <= d1 <= 31 and 1 <= d2 <= 31):
            continue
        an = int(m.group("an")) if m.group("an") else None
        debut = dt.date(an, mois, d1) if an else _annee_probable(mois, d1, aujourd_hui)
        if not debut:
            continue
        try:
            fin = dt.date(debut.year, mois, d2)
        except ValueError:
            fin = debut
        if fin < debut:
            fin = debut
        trouves.append({"debut": debut, "fin": fin, "span": (m.start(), m.end()),
                        "confiance": 90})
        pris.append((m.start(), m.end()))

    # 2) dates numeriques completes
    for m in NUM_RE.finditer(plat):
        if chevauche(m.start(), m.end()):
            continue
        an = int(m.group("a"))
        an += 2000 if an < 100 else 0
        try:
            d = dt.date(an, int(m.group("m")), int(m.group("d")))
        except ValueError:
            continue
        trouves.append({"debut": d, "fin": d, "span": (m.start(), m.end()),
                        "confiance": 95})
        pris.append((m.start(), m.end()))

    # 3) dates simples « vendredi 4decembre »
    for m in SIMPLE_RE.finditer(plat):
        if chevauche(m.start(), m.end()):
            continue
        mois = MOIS.get(m.group("mois"))
        jour = int(m.group("d"))
        if not mois or not (1 <= jour <= 31):
            continue
        an = int(m.group("an")) if m.group("an") else None
        d = dt.date(an, mois, jour) if an else _annee_probable(mois, jour, aujourd_hui)
        if not d:
            continue
        conf = 85 if m.group("jour") else 75
        # Le jour de semaine annonce colle-t-il ? Si oui on est plus sur.
        if m.group("jour") and JOURS.get(m.group("jour")) == d.weekday():
            conf = 95
        trouves.append({"debut": d, "fin": d, "span": (m.start(), m.end()),
                        "confiance": conf})
        pris.append((m.start(), m.end()))

    sorties = []
    for t in sorted(trouves, key=lambda x: x["span"][0]):
        s, e = t["span"]
        # « weekend du 10/11 » : on etend au dimanche si un seul jour a ete lu
        if t["debut"] == t["fin"] and WEEKEND_RE.search(plat[max(0, s - 40):s]):
            t["fin"] = t["debut"] + dt.timedelta(days=1)
        sorties.append({
            "nom": _label(brut, s, e),
            "debut": t["debut"].isoformat(),
            "fin": t["fin"].isoformat(),
            "extrait": brut[max(0, s - 60):e + 40].strip().replace("\n", " "),
            "confiance": t["confiance"],
            "annee_devinee": not re.search(r"\d{4}", plat[s:e]),
        })
    return sorties
