# -*- coding: utf-8 -*-
"""Grands rassemblements : fetes, brocantes, courses, carnavals, kermesses.

Un tournoi se vend aux clubs ; un rassemblement se vend aux gens presents.
Ce qui compte n'est donc pas la discipline mais la foule : combien de monde,
des familles ou non, en exterieur, et si l'evenement revient chaque annee.
"""
import re

from . import util

# Ce qu'on cherche, et ce que ca vaut en photos vendues sur place (0 a 100).
# La note dit : y a-t-il des gens qui voudront leur photo, et sauront-ils
# qu'elle existe ? Un carnaval oui, une reunion publique non.
GENRES = [
    ("carnaval",      r"\bcarnaval|corso|char(?:s)? fleuri", 95, "Carnaval"),
    ("course",        r"\bcourse|foul[ée]es?|trail|marathon|semi-marathon|10\s?km|"
                      r"triathlon|cross\b|run\b|color run", 92, "Course à pied"),
    ("fete_patronale", r"\bf[êe]te (?:patronale|du village|de la ville|locale|votive)|"
                      r"\bf[êe]te de la [\w-]+", 88, "Fête locale"),
    ("kermesse",      r"\bkermesse|f[êe]te de l'[ée]cole|f[êe]te des [ée]coles", 90, "Kermesse"),
    ("vide_grenier",  r"\bvide[- ]greniers?|brocante|puces\b|foire[- ]a[- ]tout", 70, "Brocante"),
    ("festival",      r"\bfestival|f[êe]te de la musique|concert en plein air", 85, "Festival"),
    ("feu_artifice",  r"\bfeu(?:x)? d'artifice|pyrotechni", 60, "Feu d'artifice"),
    ("marche_noel",   r"\bmarch[ée] de no[ëe]l|village de no[ëe]l", 80, "Marché de Noël"),
    ("competition",   r"\bcompetition|championnat|meeting|gala|d[ée]monstration|"
                      r"remise des prix", 78, "Compétition"),
    ("spectacle",     r"\bspectacle|repr[ée]sentation|danse|gala de danse|cirque", 82, "Spectacle"),
    # Un defile de mode et une election de miss se photographient comme un
    # gala : des candidates sur une scene, une salle pleine de familles et
    # d'amis, et chacun veut l'image de la sienne. La note est haute parce
    # que le public est exactement celui qui achete.
    ("mode_miss",     r"\bd[ée]fil[ée] de mode|fashion show|\bmiss\b|\bmister\b|"
                      r"[ée]lection de miss|concours de beaut[ée]|\bmannequin|"
                      r"pr[êe]t[- ]?[àa][- ]?porter|haute couture|podium mode", 90,
                      "Défilé & miss"),
    ("salon",         r"\bsalon|forum des associations|journ[ée]e portes ouvertes|"
                      r"portes ouvertes", 65, "Salon / forum"),
    ("sport_nautique", r"\br[ée]gate|voile|nautique|aviron|pad[dl]le|surf", 75, "Nautique"),
    ("equestre",      r"\bconcours hippique|[ée]questre|[ée]quitation|jumping", 88, "Équestre"),
    # Les tournois des autres sports : le foot, lui, part avant (est_du_foot).
    ("tournoi_sport", r"\btournoi|\binterclubs?\b|\bplateau\b|\bchallenge\b|"
                      r"\bcoupe\b|\bcup\b|\bopen\b", 86, "Tournoi (autre sport)"),
]

# Ce qui annonce de la famille — donc des parents qui achetent les photos.
FAMILLE = re.compile(
    r"\bfamille|enfants?|jeunesse|[ée]cole|p[ée]riscolaire|U\d{1,2}\b|poussins?|"
    r"benjamins?|jeunes\b|parents?|kids?|petits?\b", re.I)

# Ce qui annonce du monde.
FOULE = re.compile(
    r"(\d[\d\s]{1,6})\s*(?:personnes|participants|visiteurs|spectateurs|"
    r"coureurs|[ée]quipes|exposants)", re.I)

# Ce qui plombe : pas de public, ou public qui n'achete pas de photo.
SANS_INTERET = re.compile(
    r"\br[ée]union publique|conseil municipal|assembl[ée]e g[ée]n[ée]rale|"
    r"permanence|collecte de sang|don du sang|conf[ée]rence|"
    r"atelier num[ée]rique|s[ée]ance du conseil", re.I)

EXTERIEUR = re.compile(
    r"\bplein air|ext[ée]rieur|stade|plage|parc\b|esplanade|place du|"
    r"promenade|jardin|hippodrome|port\b", re.I)


# Ce module ne traite PAS le foot : les tournois de club ont leur propre veille,
# leur propre ecran et leurs propres brouillons. Les melanger noyait les fetes
# et les brocantes sous des centaines de tournois U8.
FOOT = re.compile(
    r"\bfoot(?:ball)?\b|\bfutsal\b|\bU\s?\d{1,2}\b|\bU\d{1,2}[FM]?\b|"
    r"\b(?:FC|US|AS|SC|ES|RC|OM|OGC)\s+[A-Z]|\bcoupe\b|\bchallenge\b|\bcup\b|"
    r"\btournoi\b|\bcrit[ée]rium\b|\bplateau\b|\bs[ée]niors?\b|\bv[ée]t[ée]rans?\b",
    re.I)


# « Tournoi », « cup », « U13 » ne disent pas le sport : un tournoi U13 de basket
# ou un interclubs de judo tombaient dans la veille foot et disparaissaient.
# Le sport nomme tranche ; « gym » seul, pas « gymnase », qui accueille le futsal.
FOOT_NOMME = re.compile(r"\bfoot(?:ball)?\b|\bfutsal\b", re.I)
AUTRE_SPORT = re.compile(
    r"\bbasket(?:-?ball)?\b|\bhand(?:ball)?\b|\bvolley(?:-?ball)?\b|\brugby\b|"
    r"\bjudo\b|\bkarat[ée]\b|\bju-?jitsu\b|\btaekwondo\b|\bboxe\b|"
    r"\bgym\b|\bgymnastique\b|\bGR\b|\bdanse\b|\bescrime\b|\bbadminton\b|"
    r"\bhockey\b|\bwater-?polo\b|\btennis\b|\bnatation\b",
    re.I)


def est_du_foot(texte):
    """True si l'annonce releve de la veille tournois foot, pas des rassemblements."""
    t = texte or ""
    if FOOT_NOMME.search(t):
        return True
    if AUTRE_SPORT.search(t):
        return False
    return bool(FOOT.search(t))


def classer(texte):
    """Renvoie {genre, libelle, note, indices} pour un texte d'annonce.

    La note part du genre puis se corrige : la famille et la foule la montent,
    une reunion administrative la descend a zero.
    """
    t = texte or ""
    if est_du_foot(t):
        return {"genre": None, "libelle": None, "note": 0,
                "indices": [], "motif": "tournoi sportif — voir la veille foot"}
    plat = util.strip_accents(t).lower()

    genre, libelle, base = None, None, 0
    for cle, motif, note, nom in GENRES:
        if re.search(motif, plat, re.I) or re.search(motif, t, re.I):
            if note > base:
                genre, libelle, base = cle, nom, note

    if SANS_INTERET.search(t):
        return {"genre": "sans_interet", "libelle": "Sans public payant",
                "note": 0, "indices": ["réunion / permanence"]}
    if not genre:
        return {"genre": None, "libelle": None, "note": 0, "indices": []}

    note = base
    indices = []
    if FAMILLE.search(t):
        note += 8
        indices.append("familles")
    m = FOULE.search(t)
    if m:
        try:
            n = int(re.sub(r"\D", "", m.group(1)))
        except ValueError:
            n = 0
        if n >= 1000:
            note += 12; indices.append("%d personnes" % n)
        elif n >= 200:
            note += 8; indices.append("%d personnes" % n)
        elif n:
            note += 3; indices.append("%d" % n)
    if EXTERIEUR.search(t):
        note += 4
        indices.append("plein air")
    return {"genre": genre, "libelle": libelle,
            "note": max(0, min(100, note)), "indices": indices}


# Les endroits ou trouver ces rassemblements. Rien n'est moissonne
# automatiquement : ce sont des pages qu'on ouvre et qu'on depouille au favori.
CHASSES = [
    ("Groupes Facebook à rejoindre",
     "https://www.facebook.com/search/groups/?q=%s", [
         "vide grenier alpes maritimes", "brocante var", "fete village 06",
         "carnaval cote d'azur", "course a pied 06", "evenements cannes",
         "sortir a nice famille", "animations var",
     ]),
    ("Événements Facebook",
     "https://www.facebook.com/events/search/?q=%s", [
         "fete cannes", "brocante nice", "carnaval var", "course nice",
     ]),
    # Les rassemblements auto et moto : un public qui achete des photos de son
    # vehicule, contrairement aux randonneurs. Ces agendas publient les dates,
    # le lieu et souvent le telephone de l'organisateur.
    ("Rassemblements auto et moto", None, [
         "https://retrocalage.com/region-provence-alpes-cote-d-azur/evenements",
         "https://www.mvmotors.fr/rassemblements-auto/var/",
         "https://www.mvmotors.fr/rassemblements-auto/alpes-maritimes/",
         "https://www.lva-auto.fr/agenda.php?rechercheType=1&idCategorie=-1&idPays=1&idDepartement=83&periode=tous",
         "https://www.lva-auto.fr/agenda.php?rechercheType=1&idCategorie=-1&idPays=1&idDepartement=6&periode=tous",
     ]),
    ("Groupes auto/moto à rejoindre",
     "https://www.facebook.com/search/groups/?q=%s", [
         "rassemblement auto 06", "rassemblement auto var", "meeting auto paca",
         "rassemblement moto 06", "rassemblement moto var", "youngtimers paca",
         "japanese cars cote d'azur", "supermotard paca",
     ]),
    ("Agendas de sortie", None, [
         "https://www.cannes-destination.com/agenda/",
         "https://www.explorenicecotedazur.com/agenda/",
         "https://www.visitvar.fr/agenda",
         "https://www.paysdegrassetourisme.fr/agenda/",
         "https://vide-greniers.org/06-Alpes-Maritimes",
         "https://vide-greniers.org/83-Var",
     ]),
]


def liens_de_chasse():
    """Les adresses a ouvrir pour aller chercher des rassemblements."""
    out = []
    for titre, gabarit, entrees in CHASSES:
        for e in entrees:
            if gabarit:
                import urllib.parse
                url = gabarit % urllib.parse.quote(e)
                libelle = e
            else:
                url = e
                libelle = e.split("//")[-1].split("/")[0]
            out.append({"famille": titre, "libelle": libelle, "url": url})
    return out
