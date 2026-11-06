# -*- coding: utf-8 -*-
"""Est-ce qu'on peut vendre des photos sur cet evenement ?

Elargir la veille au-dela du sport ne sert a rien sans ce tri. Les agendas
publics d'une commune donnent surtout du theatre, des concerts, des marches et
des conseils de quartier : essaye pour de vrai, l'agenda de Mougins rend
cinquante-quatre evenements dont zero vendable. Noyer les vrais tournois
la-dedans ferait exactement ce que Julien reproche deja a l'outil.

Le critere n'est pas « est-ce un evenement » ni meme « est-ce du sport », mais :
**quelqu'un, sur place, voudra-t-il acheter l'image ?** Trois cas seulement :

  1. un enfant se montre a ses parents (gala, kermesse, remise de prix,
     carnaval) : c'est le coeur du metier, les familles achetent ;
  2. une competition jugee ou chacun paye son engagement (concours canin,
     hippique, de danse) : le concurrent achete sa propre photo ;
  3. un rassemblement de proprietaires (autos, motos, bateaux, chiens) :
     c'est l'objet qu'on photographie, et son proprietaire qui achete.

Tout le reste — une piece de theatre, un concert, une conference, un marche —
se photographie tres bien et ne se vend a personne : le public n'est pas sur
scene. Les mots de refus passent donc AVANT ceux d'acceptation.
"""
import re
import unicodedata


def _plat(texte):
    """Sans accents, en minuscules : « Défilé » et « defile » sont le meme mot."""
    s = unicodedata.normalize("NFD", str(texte or ""))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return s.lower()


# Ce qui ne vend rien, meme bien photographie : le public est assis, et ce
# qui se passe sur scene appartient a quelqu'un d'autre.
REFUS = [
    # Les manches de qualification au tir a l'arc : « TIR 2X18M a VEDENE »,
    # « CONCOURS SELECTIF 2X18M ». Ce sont des seances de classement entre
    # licencies adultes, sans public — personne n'y achete une photo. Les
    # tournois nommes et les categories jeunes, eux, passent : « TOURNOI
    # POUSSINS DE LORGUES » et « CHALLENGE LESAGE » n'ont rien a voir.
    ("manche de qualification au tir a l.arc",
     r"(?:^|\b)(?:tir|concours(?: selectif)?|selectif)\b[^|]{0,30}"
     r"\b\d?x?\s?(?:18|25|30|50|70)\s?m\b"),

    ("spectacle professionnel",
     r"\b(concert|recital|opera|symphoni|philharmoni|one[- ]man[- ]show|"
     r"stand[- ]?up|humoriste|piece de theatre|theatre municipal|scene nationale)\b"),
    ("projection ou parole",
     r"\b(cine[- ]?debat|cinema|projection|conference|colloque|table ronde|"
     r"rencontre litteraire|dedicace|lecture|causerie|webinaire)\b"),
    ("marche ou brocante",
     r"\b(marche de noel|marche paysan|marche artisanal|brocante|vide[- ]grenier|"
     r"vide grenier|braderie|foire aux|bourse aux|troc)\b"),
    ("exposition ou visite",
     r"\b(vernissage|exposition d.art|expo photo|visite guidee|visite de chantier|"
     r"parcours d.art|musee|galerie|escape game|atelier creatif)\b"),
    ("reunion ou service",
     r"\b(conseil municipal|reunion publique|permanence|consultation|"
     r"collecte|dechetterie|don du sang|information sur|journee d.information)\b"),
    ("repas ou bal",
     r"\b(loto|belote|repas dansant|soiree dansante|\bbal\b|apero|degustation|banquet)\b"),
]

# Ce qui se vend, et a qui. L'ordre compte : la premiere famille qui repond
# donne la raison affichee.
FAMILLES = [
    ("Enfants en representation", "les familles achetent la photo de leur enfant",
     r"\b(gala|spectacle de danse|spectacle de fin d.annee|fete de l.ecole|"
     r"fete des ecoles|kermesse|arbre de noel|remise de prix|remise des prix|"
     r"remise de diplome|remise des diplomes|ceremonie de remise|"
     r"audition|spectacle de cirque|ecole de cirque|fete de la danse|"
     r"demonstration de danse|portes ouvertes de l.ecole)\b"),

    ("Defile de mode ou election de miss",
     "les candidates sont sur scene et leurs proches veulent l.image",
     r"\b(defile de mode|fashion show|miss |mister |election de miss|"
     r"concours de beaute|podium mode|pret a porter|mannequin)"),

    ("Defile et carnaval", "tout le monde est costume, et tout le monde se cherche",
     r"\b(carnaval|corso|defile|cavalcade|majorette|twirling|batucada|"
     r"fete patronale|bravade|retraite aux flambeaux|char fleuri)\b"),

    ("Concours juge", "chaque concurrent paye son engagement et achete sa photo",
     r"\b(concours canin|exposition canine|expo canine|agility|canicross|"
     r"concours hippique|concours complet|saut d.obstacles|\bcso\b|dressage|"
     r"endurance equestre|\bponey games?\b|equitation|concours de peche|"
     r"concours agricole|comice|concours de danse|championnat de danse|"
     r"concours de majorettes|concours felin|exposition feline|"
     r"concours avicole|concours de labour)\b"),

    # « Concours juge » couvrait le chien, le cheval et la poule de concours.
    # Il laissait dehors tout le reste : la transhumance — des milliers de
    # brebis dans une rue de village, le plus gros rendez-vous photo de
    # Provence —, les courses de l'hippodrome de Cagnes a vingt minutes, les
    # fermes pedagogiques, les demonstrations de chiens de berger et les
    # rapaces. Ce sont des evenements a animaux, pas des concours : aucun mot
    # de l'ancienne liste ne les attrapait.
    ("Animaux et ferme", "le proprietaire de l'animal veut toujours son portrait",
     r"\b(transhumance|fete du cheval|fete du mouton|fete de la brebis|"
     r"spectacle equestre|courses? hippiques?|hippodrome|course de trot|"
     r"course de galop|ferme pedagogique|fete de la ferme|concours de betail|"
     r"chiens? de berger|concours de chiens|demonstrations? canines?|"
     r"cani[- ]?rando|mushing|chiens? de traineau|attelage|\btrec\b|"
     r"horse[- ]?ball|voltige equestre|randonnee equestre|"
     r"fauconnerie|rapaces|aviculture|colombophilie|apiculture|"
     r"alpagas?|\blamas?\b|reptiles?|aquariophilie|\bnac\b|"
     r"tonte des moutons|fete de la laine|fete de l.ane|"
     r"concours de pigeons|salon de l.animal|salon du chiot)\b"),

    ("Rassemblement de proprietaires", "c'est l'objet qu'on photographie, le proprietaire achete",
     r"\b(rassemblement auto|rassemblement moto|exposition de vehicules|"
     r"voitures anciennes|vehicules anciens|rallye|concours d.elegance|"
     r"\bslalom\b|course de cote|trackday|circuit auto|motocross|supercross|"
     r"\benduro\b|\btrial\b|karting|\bquad\b|\bssv\b|moto[- ]?cross|"
     r"meeting aerien|fete du nautisme|regate|rassemblement de bateaux|"
     r"salon du modelisme|\bmeeting\b.{0,20}\b(auto|moto|aero))\b"),

    ("Sport de masse", "des centaines de dossards, chacun se cherche sur les photos",
     r"\b(trail|course a pied|\bcross\b|marathon|semi[- ]marathon|corrida|"
     r"foulees|triathlon|duathlon|swimrun|raid|randonnee sportive|"
     r"course d.obstacles|color run|\bcyclosportive\b|randonnee cyclo)\b"),

    ("Tournoi ou competition de club", "les parents sont au bord du terrain toute la journee",
     r"\b(tournoi|championnat|coupe de|challenge|rencontre inter|"
     # « plateau » tout seul, c'est la scene d'un theatre : trois pieces de
     # Mougins sont entrees dans la veille par ce mot. En football il est
     # toujours suivi de la categorie — plateau U9, plateau debutants.
     r"plateau (?:u\d|debutant|jeune|poussin|benjamin)|"
     r"interclubs|competition sportive|galas? de boxe|open de)\b"),

    # Place APRES le tournoi de club, a dessein : « Tournoi Halloween U8 de
    # l'US Pradet » est un tournoi de football, et c'est ce mot-la qui dit
    # quoi photographier et a qui ecrire. Halloween ne reprend la main que
    # sur ce qui n'est pas une competition.
    # Halloween ne figurait nulle part, alors que la veille contient une
    # douzaine de tournois et de soirees qui portent ce nom, et que c'est la
    # seule soiree de l'annee ou tous les enfants d'un village sont costumes
    # dans la rue. Pareil pour la chasse aux oeufs et le Pere Noel : trois
    # dates par an, des centaines d'enfants, et personne pour les photographier.
    # Ces fetes-la ne sont pas des spectacles — les enfants y sont acteurs, et
    # c'est leurs parents qui achetent.
    ("Fete d'enfants", "les enfants sont costumes et les parents photographient mal",
     r"\b(halloween|chasse aux ?oeufs|chasse a l.oeuf|fete de paques|"
     r"pere noel|gouter de noel|spectacle de noel|fete de noel|"
     r"chasse au tresor|fete du jeu|olympiades|jeux inter[- ]?villages|"
     r"jeune public|animations? enfants?|animations? jeunesse|"
     r"centre de loisirs|accueil de loisirs|fete des enfants|"
     r"journee des enfants|apres[- ]midi recreative|village des enfants|"
     r"fete du printemps|bal costume|\bboum\b|telethon|"
     r"baptemes? de poney|fete de la jeunesse)\b"),

    ("Forum et portes ouvertes", "le bon jour pour rencontrer vingt clubs d'un coup",
     r"\b(forum des associations|forum des sports|journee des associations|"
     r"portes ouvertes|village des sports|fete du sport)\b"),
]

_REFUS = [(nom, re.compile(motif)) for nom, motif in REFUS]
# Les familles sur lesquelles on peut se fier au resume quand le titre ne dit
# rien : elles nomment un evenement, pas une qualite. « Kermesse » dans un
# resume, c'est une kermesse ; « competition » peut etre une metaphore de
# programme de theatre — et c'est comme ca que « MANIFESTO » et « ARÔME ARÔME »
# sont entres dans la veille.
SUR_LE_RESUME = {"Enfants en representation", "Defile et carnaval",
                 "Concours juge", "Rassemblement de proprietaires",
                 "Forum et portes ouvertes",
                 # « Halloween » et « transhumance » nomment une date et une
                 # chose, pas une qualite : un resume qui les contient parle
                 # bien de ca. C'est utile, parce que ces evenements portent
                 # souvent un titre de fantaisie — « La Nuit des Citrouilles ».
                 "Fete d'enfants", "Animaux et ferme",
                 # « Tournoi » et « championnat » nomment un evenement eux aussi,
                 # maintenant que le simple mot « competition » a ete retire.
                 "Tournoi ou competition de club"}

_FAMILLES = [(nom, pourquoi, re.compile(motif)) for nom, pourquoi, motif in FAMILLES]


# Un titre de moins de quatre mots peut ne rien dire — « Les Poilades »,
# « Bad Express ». Au-dela, c'est le titre qui decide : une piece de theatre
# dont le resume parle de danse reste une piece de theatre, et l'agenda de
# Mougins en a fait passer six d'un coup avant cette regle.
TITRE_MUET = 4


# Le calendrier des clubs annonce sa DISCIPLINE, ce qu'aucune autre source ne
# faisait. C'est une information decisive : « Tournoi mensuel » ne dit rien,
# « Tournoi mensuel — Bridge » dit tout. Personne n'achete la photo d'une
# table de bridge ; les parents achetent celle de leur enfant en kimono.

# Les disciplines sans spectateur qui paie : on ne vend pas de photo a des
# marcheurs, ni a quatre joueurs de cartes autour d'une table.
SPORTS_MUETS = re.compile(
    r"randonn|marche nordique|cyclotourisme|\bbridge\b|belote|tarot|\bjeux?\b|"
    r"\bp[ée]tanque\b|boule|billard|\bp[êe]che\b|yoga|gym (?:volontaire|douce|"
    r"d'entretien)|qi ?gong|t[ao].?chi|marche|a[ée]romod[ée]lisme|mod[ée]lisme|"
    r"[ée]chec|scrabble|informatique|g[ée]n[ée]alogie|chorale|th[ée][âa]tre", re.I)

# Celles ou il y a toujours des enfants et des familles au bord : une
# competition dans ces disciplines se photographie et se vend, meme quand le
# titre est avare.
# Ce qui, dans un titre, annonce une competition plutot qu'un entrainement.
COMPETITION = re.compile(
    r"tournoi|competition|championnat|challenge|\bcoupe\b|\bcup\b|\bopen\b|"
    r"meeting|interclubs?|plateau|rencontre|gala|criterium|\btrophee\b|"
    r"\bfinale|\bmanche\b|concours|\bcross\b|\bmasters?\b|\bgp\b|grand prix",
    re.I)

SPORTS_VENDEURS = re.compile(
    r"natation|nage|plongeon|water.?polo|gymnastique|\bgr\b|rythmique|trampolin|"
    r"danse|patinage|judo|karat|taekwondo|aikido|lutte|boxe|kick|\bmma\b|escrime|"
    r"athl[ée]tisme|\bfoot|rugby|basket|hand|volley|tennis|badminton|p[ée]dale|"
    r"[ée]quitation|poney|voile|aviron|canoe|kayak|surf|escalade|\bski\b|"
    r"cheerleading|twirling|majorette|course [àa] pied|trail|cross|triathlon|"
    # Ce qui manquait. Le tir a l'arc d'abord : vingt-six fiches en base, la
    # deuxieme discipline apres le football, et pas un mot pour elle ici.
    r"tir [àa] l.arc|tir sportif|ball[- ]?trap|sarbacane|arbal[èe]te|"
    r"multisports?|omnisports?|handisport|sport adapt[ée]|"
    r"\bgolf|roller|skate|\bbmx\b|parkour|ultimate|tchoukball|"
    r"baseball|softball|football am[ée]ricain|\bflag\b|floorball|"
    r"p[ée]lote|squash|padel|ping[- ]?pong|tennis de table|"
    r"savate|muay|jiu[- ]?jitsu|ju[- ]?jitsu|grappling|capoeira|kung[- ]?fu|"
    r"full[- ]?contact|self[- ]?d[ée]fense|krav|sambo|\blutte\b|"
    r"acrosport|a[ée]robic|hip[- ]?hop|modern jazz|claquettes|"
    r"rock acrobatique|country|breakdance|\bbreaking\b|"
    r"cyclisme|cyclo[- ]?cross|\bvtt\b|duathlon|aquathlon|biathlon|"
    r"course d.orientation|raid multisport|sauvetage sportif|"
    r"halt[ée]rophilie|crossfit|force athl[ée]tique|street workout|"
    r"hockey|\bcurling\b|\bsnowboard\b|motonautisme", re.I)


def juger(titre, description=None, sport=None):
    """Dit si l'evenement peut rapporter des photos, et pourquoi.

    Renvoie {vend, famille, pourquoi, refus}. Le refus porte sur tout le texte ;
    l'acceptation sur le titre seul, sauf quand le titre ne dit rien.

    `sport` est la discipline quand la source la publie. Elle tranche les cas
    que le titre seul decide mal : « Tournoi de bridge » passait pour un
    tournoi de club, « Meeting regional » pour une reunion de bureau.
    """
    t = _plat(titre)
    complet = t + " " + _plat(description)

    # La discipline d'abord, quand on l'a : elle est plus sure que le titre.
    if sport:
        if SPORTS_MUETS.search(_plat(sport)):
            return {"vend": False, "famille": None, "pourquoi": None,
                    "refus": "discipline sans photo a vendre (%s)" % sport}

    for nom, motif in _REFUS:
        if motif.search(complet):
            return {"vend": False, "famille": None, "pourquoi": None, "refus": nom}

    # Les familles d'abord : elles sont plus precises. Un gala de danse est
    # « Enfants en representation », pas « Tournoi de club » — et c'est ce
    # mot-la qui dit quoi photographier une fois sur place.
    titre_muet = len(t.split()) < TITRE_MUET
    for nom, pourquoi, motif in _FAMILLES:
        cherche = complet if (titre_muet and nom in SUR_LE_RESUME) else t
        if motif.search(cherche):
            return {"vend": True, "famille": nom, "pourquoi": pourquoi, "refus": None}

    # A defaut : une competition dans une discipline a familles se vend, meme
    # quand le titre est avare. « Meeting regional » ne dit rien ; « Meeting
    # regional — Natation » dit des bassins pleins de gamins et de parents.
    if sport and SPORTS_VENDEURS.search(_plat(sport)) \
            and COMPETITION.search(complet):
        return {"vend": True, "famille": "Tournoi ou competition de club",
                "pourquoi": "les parents sont au bord du terrain toute la journee",
                "refus": None}

    return {"vend": False, "famille": None, "pourquoi": None,
            "refus": "rien qui annonce des gens a photographier"}


def trier(fiches):
    """Separe une moisson en deux tas : ce qu'on garde, ce qu'on laisse.

    La fiche n'est pas modifiee : le verdict voyage a cote. Y coller des
    champs en plus faisait planter l'enregistrement, qui les prenait pour
    des colonnes de la table.
    """
    gardes, laisses = [], []
    for f in fiches:
        verdict = juger(f.get("name"), f.get("description"), f.get("sport"))
        (gardes if verdict["vend"] else laisses).append((f, verdict))
    return gardes, laisses


# ---------------------------------------------------------------------------
# Combien de monde ?
# ---------------------------------------------------------------------------
# Le nombre de participants decide de tout : on vend une photo par personne
# presente, pas par evenement. Un marathon de huit mille dossards et une
# mensuelle de douze voitures demandent la meme journee de travail et ne
# rapportent pas la meme chose.
#
# La donnee n'existe nulle part — ni DATAtourisme ni les annuaires ne donnent
# d'effectif. On l'estime donc par le genre d'evenement, ce qui suffit a
# trancher entre « des milliers », « des centaines » et « quelques dizaines ».
# L'estimation est annoncee comme telle, jamais comme un chiffre.
AMPLEURS = [
    ("foule", "des milliers de participants", 3,
     r"\b(carnaval|corso|f[eê]te du citron|marathon|semi[- ]marathon|"
     r"braderie|f[eê]te nationale|grande parade|color run)\b"),

    ("beaucoup", "des centaines de participants", 2,
     r"\b(trail|ultra[- ]trail|course [àa] pied|corrida|foul[ée]es|triathlon|"
     r"duathlon|swimrun|\braid\b|cross|championnat (?:de france|national|"
     r"international|du monde|d.europe)|coupe de france|open international|"
     r"forum des associations|kermesse|gala|f[eê]te de l.[ée]cole|"
     r"cyclosportive|randonn[ée]e cyclo)\b"),

    ("moyen", "quelques dizaines a quelques centaines", 1,
     r"\b(tournoi|championnat|coupe|challenge|plateau|concours|r[ée]gate|"
     r"exposition canine|slalom|course de c[ôo]te|rallye|motocross|"
     r"trackday|interclubs|meeting)\b"),
]

_AMPLEURS = [(cle, dit, poids, re.compile(motif)) for cle, dit, poids, motif in AMPLEURS]

# Ce qui annonce au contraire une petite assistance, quel que soit le reste :
# une mensuelle de club rassemble les memes vingt habitues tous les mois.
PETIT = re.compile(r"\b(mensuel|mensuelle|rendez[- ]vous du|rdv |hebdomadaire|"
                   r"r[ée]union de club|sortie du club|portes ouvertes du club)\b", re.I)


def ampleur(nom, description=None, nb_equipes=None):
    """Estime le monde attendu. Renvoie (cle, phrase, poids de 0 a 3).

    Le nombre d'equipes, quand il est connu, passe avant toute estimation :
    c'est un fait, pas une supposition.
    """
    try:
        n = int(nb_equipes or 0)
    except (TypeError, ValueError):
        n = 0
    if n >= 24:
        return "beaucoup", "%d equipes annoncees" % n, 2
    if n >= 8:
        return "moyen", "%d equipes annoncees" % n, 1
    if n:
        return "peu", "%d equipes annoncees" % n, 0

    t = _plat(nom)
    if PETIT.search(t):
        return "peu", "rendez-vous recurrent : les memes habitues", 0
    complet = t + " " + _plat(description)
    for cle, dit, poids, motif in _AMPLEURS:
        if motif.search(complet):
            return cle, dit + " (estime)", poids
    return "peu", "aucun signe d'une grosse affluence", 0


# ---------------------------------------------------------------------------
# Ce que vaut une fiche
# ---------------------------------------------------------------------------
# Le Reperage classait par distance, et rien d'autre. Un carnaval de quinze
# mille personnes a quarante minutes passait donc APRES un plateau de quatre
# equipes a trente-cinq. Trois cents fiches dans cet ordre, c'est une journee
# de lecture pour retrouver ce qui se vend.
#
# On classe maintenant par ce que la fiche peut rapporter : du monde qui
# achete des photos, atteignable, et encore dans les temps pour etre
# demarche. Les trois criteres sont ecrits ici en clair, et la carte dit
# lequel l'a emporte — un classement qu'on ne peut pas expliquer, on ne s'y
# fie pas.

# Ce que vaut une affluence quand aucune note n'a ete calculee (tournois).
_MONDE_PAR_POIDS = {0: 25, 1: 55, 2: 80, 3: 95}


def valeur(fiche, lead_min=10, lead_max=75):
    """Renvoie (score, pourquoi) : ce que cette fiche vaut, et en un mot.

    Le score n'a pas d'unite — il ne sert qu'a ranger les fiches entre elles.
    """
    import datetime as _dt

    note = fiche.get("note_photo")
    if note is None:
        _, _, poids = ampleur(fiche.get("name"), fiche.get("description"),
                              fiche.get("nb_equipes"))
        note = _MONDE_PAR_POIDS.get(poids, 25)
    score = float(note)
    raisons = []

    equipes = fiche.get("nb_equipes")
    try:
        equipes = int(equipes or 0)
    except (TypeError, ValueError):
        equipes = 0
    if equipes >= 16:
        score += 12
        raisons.append("%d équipes" % equipes)
    elif equipes >= 8:
        score += 5
        raisons.append("%d équipes" % equipes)
    elif note >= 85:
        raisons.append("beaucoup de monde attendu")

    # La route. Une heure de voiture mange la matinee ; trois heures mangent
    # la journee, et la journee ne se vend qu'une fois.
    minutes = fiche.get("trajet_min")
    if minutes is None and fiche.get("distance_km") is not None:
        minutes = float(fiche["distance_km"]) * 0.9       # faute de mieux
    if minutes is None:
        score -= 20                                       # lieu inconnu : on ne promet rien
        raisons.append("route inconnue")
    else:
        minutes = float(minutes)
        if minutes <= 30:
            raisons.append("à côté")
        elif minutes <= 60:
            score -= 8
        elif minutes <= 120:
            score -= 22
        else:
            score -= 22 + (minutes - 120) / 6.0
            raisons.append("%d h de route" % round(minutes / 60.0))

    # La fenetre. Un tournoi dans trois jours ne se demarche plus : l'equipe
    # photo est deja formee, ou ne le sera pas. Un tournoi dans huit mois ne
    # se demarche pas encore : personne n'a de reponse a donner.
    debut = fiche.get("start_date")
    if debut:
        try:
            jours = (_dt.date(*map(int, str(debut)[:10].split("-")))
                     - _dt.date.today()).days
        except (ValueError, TypeError):
            jours = None
        if jours is not None:
            if jours < 0:
                score -= 60
                raisons.append("déjà passé")
            elif jours < lead_min:
                score -= 30
                raisons.append(("c'est demain" if jours == 1
                                else "aujourd'hui" if jours == 0
                                else "dans %d jours — c'est court" % jours))
            elif jours <= lead_max:
                score += 8
                raisons.append("dans la bonne fenêtre")
            elif jours <= 180:
                score -= 5
            else:
                score -= 18
                raisons.append("encore loin")
    else:
        score -= 12
        raisons.append("sans date")

    return round(score, 1), " · ".join(raisons[:2])
