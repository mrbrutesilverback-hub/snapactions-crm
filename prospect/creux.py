# -*- coding: utf-8 -*-
"""Les semaines creuses, et de quoi les remplir.

Une semaine sans date, c'est une semaine sans chiffre d'affaires — et on s'en
apercoit en general trop tard, quand il ne reste plus le temps de demarcher.
Le calendrier le montre pourtant : au moment ou ce module a ete ecrit, trois
semaines d'affilee etaient vides en novembre, alors que la veille avait vingt
tournois a portee sur ces memes semaines, dont quatre a moins d'une demi-heure.

Le module ne fait donc rien d'autre que rapprocher deux choses qui existaient
deja chacune de leur cote : l'agenda de travail, et les tournois reperes.

Deux etats valent d'etre distingues, parce qu'ils n'appellent pas la meme
reaction :
  - la semaine vide, ou il n'y a rien du tout ;
  - la semaine dont le WEEK-END est libre, alors qu'un shooting est pose un
    mardi. Les tournois se tiennent samedi et dimanche : un mardi occupe ne
    remplit pas le week-end, et c'est la que l'argent se gagne.
"""

import datetime as dt
import re

from . import agenda, db, photos, util

# Au-dela, demarcher ne sert plus a rien : le club a deja choisi son prestataire.
SEMAINES_DEFAUT = 10

# Une date a plus de deux heures ne se decide pas a la legere ; on ne la
# propose comme bouche-trou que si rien de plus pres n'existe.
PORTEE_MIN = 120

# Combien de pistes on montre par semaine. Au-dela on ne choisit plus, on subit.
PISTES_PAR_SEMAINE = 6


def _lundi(d):
    return d - dt.timedelta(days=d.weekday())


def _palier(t):
    """Le meme classement que l'ecran de veille : la route d'abord."""
    m = t.get("trajet_min")
    if m is None:
        return 3
    if m <= 45:
        return 0
    if m <= 90:
        return 1
    if m <= PORTEE_MIN:
        return 2
    return 3


def _equipes(t):
    try:
        return int(t.get("nb_equipes") or 0)
    except (TypeError, ValueError):
        return 0


def _pistes(debut, fin, jours_pris):
    """Les tournois a portee qui tombent dans cette semaine."""
    rows = db.rows_to_dicts(db.q(
        "SELECT id, name, sport, city, start_date, end_date, trajet_min, "
        "       distance_km, nb_equipes, COALESCE(validation,'a_valider') AS validation, "
        "       COALESCE(lieu_verifie,1) AS lieu_verifie "
        "FROM tournaments "
        "WHERE date(start_date) BETWEEN ? AND ? "
        "  AND COALESCE(trajet_min, 9999) <= ? "
        "  AND COALESCE(validation,'a_valider') != 'non' "
        "ORDER BY start_date", (debut.isoformat(), fin.isoformat(), PORTEE_MIN)))
    for r in rows:
        d = dt.date.fromisoformat(r["start_date"])
        r["jour"] = d.isoformat()
        r["week_end"] = d.weekday() >= 5
        # Combien de monde : c'est ce qui decide si la journee vaut le
        # deplacement. On vend une photo par personne presente.
        cle, dit, poids = photos.ampleur(r.get("name"), None, r.get("nb_equipes"))
        r["ampleur"] = cle
        r["ampleur_dit"] = dit
        r["ampleur_poids"] = poids
        # Une piste le jour d'une date deja posee n'est pas disqualifiee : elle
        # est seulement moins evidente, et il vaut mieux le dire que la cacher.
        r["jour_deja_pris"] = r["jour"] in jours_pris
    # Palier de route, puis le plateau le plus gros, puis la date — et a
    # egalite, le plus pres : trois tournois le meme dimanche ne doivent pas
    # s'afficher dans l'ordre ou la base les a rendus.
    # Le monde attendu passe devant la route : deux heures pour un marathon de
    # huit mille dossards valent mieux que vingt minutes pour une mensuelle de
    # douze voitures. La route departage ensuite.
    rows.sort(key=lambda t: (-t["ampleur_poids"], _palier(t), -_equipes(t),
                             t["start_date"],
                             t.get("trajet_min") if t.get("trajet_min") is not None else 9999))
    return rows


def semaines(nombre=SEMAINES_DEFAUT, aujourdhui=None, frais=False):
    """L'etat de chaque semaine a venir, et les pistes pour celles qui sont creuses."""
    auj = aujourdhui or dt.date.today()
    depart = _lundi(auj)

    try:
        evs, lu_le, du_cache = agenda.lister_vite(depuis=depart, frais=frais)
        source = "Calendrier"
    except agenda.AgendaIndisponible as exc:
        return {"semaines": [], "erreur": str(exc), "source": None}

    cale = {}
    for e in evs:
        try:
            d = dt.date.fromisoformat((e.get("debut") or "")[:10])
        except ValueError:
            continue
        if d < depart:
            continue
        cale.setdefault(_lundi(d).isoformat(), []).append(
            {"jour": d.isoformat(), "titre": e.get("titre") or "",
             "week_end": d.weekday() >= 5, "uid": e.get("uid")})

    sortie = []
    for i in range(nombre):
        debut = depart + dt.timedelta(weeks=i)
        fin = debut + dt.timedelta(days=6)
        dedans = sorted(cale.get(debut.isoformat(), []), key=lambda x: x["jour"])
        we = [x for x in dedans if x["week_end"]]
        jours_pris = {x["jour"] for x in dedans}

        if not dedans:
            etat, dit = "vide", "Rien de prévu"
        elif not we:
            etat, dit = "we_libre", "Week-end libre"
        elif len(we) == 1:
            etat, dit = "leger", "Un seul jour de week-end pris"
        else:
            etat, dit = "plein", "Week-end pourvu"

        # Un week-end pris ne ferme pas la semaine. On cherchait les pistes
        # UNIQUEMENT quand le week-end etait libre : une course le mercredi,
        # un plateau le vendredi, un carnaval le lundi de Pentecote n'avaient
        # donc aucune chance d'etre proposes des que le samedi etait pose.
        # On cherche toujours, et sur une semaine pourvue on ne garde que ce
        # qui tombe un jour encore libre.
        piste = _pistes(debut, fin, jours_pris)
        if etat == "plein":
            piste = [p for p in piste if not p["jour_deja_pris"]]
        sortie.append({
            "lundi": debut.isoformat(), "dimanche": fin.isoformat(),
            "numero": debut.isocalendar()[1],
            "etat": etat, "dit": dit,
            "cale": dedans, "n_cale": len(dedans), "n_we": len(we),
            "pistes": piste[:PISTES_PAR_SEMAINE],
            "n_pistes": len(piste),
            "jours_libres": jours_libres(debut, jours_pris, auj),
            "proches": sum(1 for p in piste if _palier(p) == 0),
            "cette_semaine": i == 0,
        })

    creuses = [s for s in sortie if s["etat"] in ("vide", "we_libre")]
    return {
        "semaines": sortie,
        "source": source,
        "lu_le": lu_le,
        "du_cache": du_cache,
        "n_creuses": len(creuses),
        # Les memes clubs servent pour n'importe quel jour de semaine vide :
        # on les calcule une fois, l'ecran les pose sous chaque semaine.
        "clubs_semaine": clubs_en_semaine(),
        # La premiere semaine creuse sur laquelle il reste le temps d'agir :
        # demarcher un club l'avant-veille ne sert a rien.
        "prochaine_creuse": next((s["lundi"] for s in creuses
                                  if dt.date.fromisoformat(s["lundi"]) >= auj
                                  + dt.timedelta(days=7)), None),
    }

# ---------------------------------------------------------------------------
# Les jours de semaine
# ---------------------------------------------------------------------------
# Un mardi vide ne se remplit pas avec un tournoi : il n'y en a pas le mardi.
# Il se remplit avec un autre produit, vendu aux memes gens — une seance a
# l'entrainement, les portraits de l'equipe, un stage de vacances scolaires.
# On ne propose donc pas des evenements mais des CLUBS, et en priorite ceux
# qui ont deja dit oui une fois : un club qui vous connait dit oui pour un
# mercredi soir bien plus vite qu'un inconnu.
#
# Le mercredi est a part en France : c'est le jour ou les enfants s'entrainent.
# Il merite d'etre signale, parce que c'est le plus facile a vendre.
JOURS_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]

CLUBS_PAR_SEMAINE = 8

# En semaine on ne traverse pas la region pour deux heures de seance : une
# demi-heure de route, pas davantage.
PORTEE_SEMAINE_MIN = 45

# Un club deja client passe devant un prospect jamais contacte.
RANG_STATUT = {"client": 0, "contacte": 1, "prospect": 2}

def _minutes_par_commune():
    """Le temps de route par commune, appris des tournois deja mesures.

    Les clubs n'ont pas de distance en base ; les tournois, si. Deux cents
    communes sont ainsi deja chiffrees, ce qui couvre trois clubs sur quatre,
    et sans un seul appel reseau.
    """
    table = {}
    for r in db.q("SELECT LOWER(TRIM(city)) AS v, MIN(trajet_min) AS m "
                  "FROM tournaments WHERE trajet_min IS NOT NULL "
                  "AND TRIM(COALESCE(city,'')) <> '' GROUP BY 1"):
        table[r["v"]] = r["m"]
    return table


def jours_libres(debut, jours_pris, aujourdhui=None):
    """Les jours ouvrables de la semaine ou il n'y a rien, a partir d'aujourd'hui."""
    auj = aujourdhui or dt.date.today()
    out = []
    for i in range(5):                       # lundi a vendredi
        d = debut + dt.timedelta(days=i)
        if d < auj or d.isoformat() in jours_pris:
            continue
        out.append({"jour": d.isoformat(), "nom": JOURS_FR[i],
                    "mercredi": i == 2})
    return out


# Combien de temps une fiche studio disparait de la liste, selon le geste.
# « Contacte » n'est pas « jamais » : on repasse dans deux mois, parce qu'un
# non en octobre peut devenir un oui en janvier.
ECARTS_STUDIO = {"contacte": 60, "plus_tard": 14, "non": 365}


def _ecartes_studio():
    """Les clubs mis de cote et pas encore revenus, avec leur date de retour."""
    auj = util.today().isoformat()
    return {int(r["cle"].split(":", 1)[1]): dict(r) for r in db.q(
        "SELECT * FROM journee_ecarts WHERE cle LIKE 'studio:%' AND jusquau > ?",
        (auj,)) if r["cle"].split(":", 1)[1].isdigit()}


def ecarter_studio(club_id, motif="plus_tard"):
    """Retire une fiche de la liste studio, pour un temps."""
    jours = ECARTS_STUDIO.get(motif, 14)
    jusquau = (util.today() + dt.timedelta(days=jours)).isoformat()
    db.ex("INSERT INTO journee_ecarts (cle, jusquau, motif, pose_le) "
          "VALUES (?, ?, ?, ?) "
          "ON CONFLICT(cle) DO UPDATE SET jusquau=excluded.jusquau, "
          "  motif=excluded.motif, pose_le=excluded.pose_le",
          ("studio:%d" % int(club_id), jusquau, motif, util.now_iso()))
    return {"jusquau": jusquau, "jours": jours}


def reprendre_studio(club_id=None):
    """Remet une fiche, ou toutes, dans la liste."""
    if club_id is None:
        n = db.ex("DELETE FROM journee_ecarts WHERE cle LIKE 'studio:%'")
    else:
        n = db.ex("DELETE FROM journee_ecarts WHERE cle = ?",
                  ("studio:%d" % int(club_id),))
    return {"remises": n if isinstance(n, int) else 0}


def clubs_studio(portee=None):
    """Tous les clubs a qui proposer une seance studio, sans plafond.

    Meme matiere que clubs_en_semaine(), mais pour la page dediee : on ne
    coupe ni a huit fiches ni a deux par commune, puisque c'est la liste qu'on
    descend un soir en appelant.
    """
    par_commune = _minutes_par_commune()
    ecartes = _ecartes_studio()
    limite_route = portee if portee is not None else PORTEE_SEMAINE_MIN
    rows = db.rows_to_dicts(db.q(
        "SELECT id, name, city, postal_code, sport, phone, email, status, "
        "       partenaire, last_contact_at "
        "FROM clubs "
        "WHERE COALESCE(do_not_contact, 0) = 0 "
        "  AND COALESCE(relais, 0) = 0 "
        "  AND TRIM(COALESCE(phone, '')) <> '' "
        "ORDER BY name"))
    sortie = []
    for c in rows:
        if c["id"] in ecartes:
            continue
        minutes = par_commune.get((c.get("city") or "").strip().lower())
        if minutes is not None and minutes > limite_route:
            continue
        c["trajet_min"] = minutes
        c["genre"] = _genre(c)
        c["connu"] = bool(c.get("partenaire") or c.get("status") in ("client", "contacte"))
        c["pourquoi"] = _pourquoi_semaine(c)
        sortie.append(c)
    sortie.sort(key=lambda c: (
        0 if c.get("partenaire") else 1,
        RANG_STATUT.get(c.get("status"), 3),
        c["trajet_min"] if c["trajet_min"] is not None else 999,
        c.get("last_contact_at") or "",
    ))
    return sortie


def clubs_en_semaine(limite=CLUBS_PAR_SEMAINE):
    """A qui ecrire pour remplir un jour de semaine.

    Deux tas, parce que ce ne sont pas les memes mots : ceux qu'on RELANCE
    (ils connaissent la maison, il suffit de rappeler qu'on a de la place) et
    ceux qu'on DEMARCHE (il faut encore expliquer qui on est).

    Aucune profession n'est ecartee : un salon de toilettage a autant de
    portraits a vendre qu'un club de judo, et c'est la meme seance studio.
    """
    par_commune = _minutes_par_commune()
    rows = db.rows_to_dicts(db.q(
        "SELECT id, name, city, postal_code, sport, phone, email, status, "
        "       partenaire, last_contact_at "
        "FROM clubs "
        "WHERE COALESCE(do_not_contact, 0) = 0 "
        "  AND TRIM(COALESCE(phone, '')) <> '' "
        "  AND id NOT IN (SELECT club_id FROM tournaments "
        "                 WHERE club_id IS NOT NULL AND date(start_date) >= date('now'))"))

    sortie = []
    for c in rows:
        minutes = par_commune.get((c.get("city") or "").strip().lower())
        if minutes is not None and minutes > PORTEE_SEMAINE_MIN:
            continue
        c["trajet_min"] = minutes
        c["trajet_estime"] = minutes is not None     # appris d'une commune, pas mesure
        c["genre"] = _genre(c)
        c["connu"] = bool(c.get("partenaire") or c.get("status") in ("client", "contacte"))
        c["pourquoi"] = _pourquoi_semaine(c)
        sortie.append(c)

    # Ceux qui ont deja travaille avec vous d'abord, les plus pres ensuite, et
    # a egalite celui qu'on n'a pas appele depuis le plus longtemps.
    sortie.sort(key=lambda c: (
        0 if c.get("partenaire") else 1,
        RANG_STATUT.get(c.get("status"), 3),
        c["trajet_min"] if c["trajet_min"] is not None else 999,
        c.get("last_contact_at") or "",
    ))

    # Deux clubs par commune au maximum. Sans cette regle la liste rendait six
    # sections du meme omnisports : meme ville, meme federation, meme personne
    # au bout du fil — on n'a pas huit pistes, on en a une.
    tas = {"relancer": [], "demarcher": []}
    par_ville = {}
    for c in sortie:
        ou = "relancer" if c["connu"] else "demarcher"
        if len(tas[ou]) >= limite:
            continue
        ville = (c.get("city") or "?").strip().lower()
        cle = ou + "/" + ville
        if par_ville.get(cle, 0) >= 2:
            continue
        par_ville[cle] = par_ville.get(cle, 0) + 1
        tas[ou].append(c)
    return tas


# Un salon de toilettage, un centre equestre et un club de judo ne se parlent
# pas pareil, mais ils achetent la meme chose : une seance de portraits de
# leurs adherents, en semaine, dans un studio monte sur place.
ANIMAUX = re.compile(r"toilettage|canin|chien|chat|felin|v[ée]t[ée]rinaire|"
                     r"pension|[ée]levage|animal|animaux|\bponey\b|[ée]questre|"
                     r"[ée]quitation|cheval|chevaux|haras", re.I)


def _genre(c):
    texte = (c.get("sport") or "") + " " + (c.get("name") or "")
    return "animaux" if ANIMAUX.search(texte) else "club"


def _pourquoi_semaine(c):
    """La raison, en une ligne, d'appeler celui-la plutot qu'un autre."""
    if c.get("partenaire"):
        return "Partenaire : il a deja dit oui une fois."
    if c.get("status") == "client":
        return "Deja client — la vente la plus facile de la liste."
    if c.get("status") == "contacte":
        quand = (c.get("last_contact_at") or "")[:10]
        return ("Contacte le %s, jamais conclu." % quand) if quand \
            else "Deja contacte, jamais conclu."
    return "Jamais travaille ensemble : a presenter."
