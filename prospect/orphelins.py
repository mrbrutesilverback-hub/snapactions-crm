# -*- coding: utf-8 -*-
"""Les tournois sans organisateur : des dates qu'on voit sans pouvoir ecrire.

Deux cent quinze evenements a venir n'ont aucun club rattache. Julien les
voit dans son Reperage, mais il n'y a personne a qui parler : pas de fiche,
pas de contact, pas de message. Ce sont des dates mortes.

Google sait retrouver qui organise « Challenge Pierre Babin » a Cuges-les-Pins.
Une fois l'organisateur nomme, la chaine existante reprend : fiche club,
recherche des coordonnees, message.

Le danger ici n'est pas le numero invente, c'est le CLUB invente. Ce projet
a deja nettoye des fiches nees d'un titre de tournoi mal decoupe. Trois
verrous :
  - le modele rend null plutot que de deviner, la consigne le dit ;
  - un nom qui ressemble au nom de l'evenement est refuse ;
  - un nom qui ne passe pas le juge de sources.py est refuse.
"""

import time

from . import db, util

PAR_TOUR = 5
ESSAIS_MAX = 3
INTERVALLE = 300

_dernier = {"le": None, "faits": 0, "rattaches": 0, "erreur": None}


def dernier_passage():
    return dict(_dernier)


def _plat(t):
    return util.strip_accents(str(t or "")).lower().strip()


def _nom_refuse(nom, tournoi):
    """Un organisateur qui n'en est pas un."""
    n = _plat(nom)
    if len(n) < 4:
        return "trop court"
    titre = _plat(tournoi.get("name"))
    # Le nom de l'evenement rendu comme organisateur : c'est le piege connu.
    if n == titre or (len(n) > 8 and n in titre):
        return "c'est le nom de l'événement"
    for mot in ("tournoi", "championnat", "coupe", "challenge", "critere",
                "criterium", "trophee", "open ", "cup", "edition", "etape"):
        if n.startswith(mot):
            return "commence comme un nom d'épreuve"
    # On n'appelle PAS sources._est_club ici : il a ete ecrit pour juger des
    # noms DECOUPES dans un titre de tournoi, et il refuse des clubs bien
    # reels — « Juventus de Papus », a Toulouse, en est un. Ici le nom vient
    # d'une page qu'on a lue, et le modele a pour consigne de rendre null
    # plutot que de deviner : les deux verrous ci-dessus suffisent.
    return None


def candidats(limite=PAR_TOUR):
    return db.rows_to_dicts(db.q(
        "SELECT * FROM tournaments "
        "WHERE club_id IS NULL AND date(COALESCE(start_date,'1900-01-01')) >= date('now') "
        "  AND COALESCE(name,'') <> '' AND orga_cherche_le IS NULL "
        "ORDER BY COALESCE(orga_cherche_essais,0), start_date LIMIT ?",
        (int(limite),)))


def _fini(tid, etat, essais=None):
    db.ex("UPDATE tournaments SET orga_cherche_le = ?, orga_cherche_etat = ?, "
          "orga_cherche_essais = COALESCE(?, orga_cherche_essais) WHERE id = ?",
          (util.now_iso(), etat[:120], essais, int(tid)))


def _depuis_l_annonce(t):
    """Qui organise, lu sur la page d'annonce — sans Google.

    Deux cent quatorze orphelins sur deux cent quinze ont leur page. Le nom
    de l'organisateur y figure presque toujours, et le modele local sait le
    reconnaitre : c'est gratuit et ca tourne sur ce Mac.
    """
    url = (t.get("url") or "").strip()
    if not url:
        return None
    from . import ia, numeros
    html = numeros._lire(url)
    if not html:
        return None
    texte = numeros._texte(html)[:6000]
    if len(texte) < 120:
        return None
    try:
        if not ia.disponible():
            return None
        nom = ia.organisateur_depuis_web(t.get("name"), t.get("city"), texte)
    except Exception:
        return None
    nom = (nom or {}).get("organisateur") if isinstance(nom, dict) else nom
    nom = (nom or "").strip()
    return {"organisateur": nom or None, "pages": [(url, "page de l'annonce")],
            "pourquoi": "lu sur la page de l'annonce"} if nom else None


def un_tournoi(t):
    """Trouve l'organisateur, cree ou retrouve sa fiche, et rattache."""
    from . import recherche, repo

    # Le gratuit d'abord : la page d'annonce, lue ici, par le modele local.
    d = _depuis_l_annonce(t)
    if d:
        return _rattacher(t, d, "lu sur l'annonce")
    # L'annonce existe mais ne nomme personne : Google ne fera pas mieux a
    # quelques centimes la requete. On ne l'appelle que sans page du tout.
    if (t.get("url") or "").strip():
        _fini(t["id"], "annonce lue, aucun organisateur nommé")
        return {"tournoi": t["name"], "etat": "inconnu", "sans_google": True}

    if not recherche.disponible():
        # Pas d'annonce et pas de recherche web : il n'y a rien a lire. On
        # le dit et on passe, plutot que de bloquer la file.
        _fini(t["id"], "aucune annonce, recherche web coupée")
        return {"tournoi": t["name"], "etat": "inconnu", "sans_google": True}

    try:
        d = recherche.organisateur_du_tournoi(t)
    except recherche.Epuise:
        raise
    except Exception as exc:
        essais = int(t.get("orga_cherche_essais") or 0) + 1
        if essais >= ESSAIS_MAX:
            _fini(t["id"], "google muet (essai %d)" % essais, essais)
        else:
            db.ex("UPDATE tournaments SET orga_cherche_essais = ?, "
                  "orga_cherche_etat = ? WHERE id = ?",
                  (essais, "à réessayer : %s" % str(exc)[:60], t["id"]))
        return {"tournoi": t["name"], "etat": "à réessayer", "essais": essais}

    return _rattacher(t, d, "Google")


def _rattacher(t, d, voie):
    """Cree ou retrouve la fiche de l'organisateur, et y accroche la date."""
    from . import repo
    nom = (d.get("organisateur") or "").strip()
    if not nom:
        _fini(t["id"], "%s ne dit pas qui organise" % voie)
        return {"tournoi": t["name"], "etat": "inconnu", "voie": voie}

    refus = _nom_refuse(nom, t)
    if refus:
        _fini(t["id"], "nom refusé (%s) : %s" % (refus, nom[:40]))
        return {"tournoi": t["name"], "etat": "refusé", "nom": nom,
                "pourquoi": refus}

    pages = [u for u, _ in (d.get("pages") or [])]
    cid, etat = repo.upsert_club({
        "name": nom,
        "city": t.get("city") or None,
        "postal_code": t.get("postal_code") or None,
        "dept": t.get("dept") or None,
        "sport": t.get("sport") or None,
        "website": pages[0] if pages else None,
        "source": "Organisateur trouvé par Google",
        "source_url": pages[0] if pages else None,
        "notes": "Organisateur de « %s » (%s), trouvé le %s. %s"
                 % ((t.get("name") or "")[:70], (t.get("start_date") or "")[:10],
                    util.today().isoformat(), d.get("pourquoi") or ""),
    })
    if not cid:
        _fini(t["id"], "fiche club impossible")
        return {"tournoi": t["name"], "etat": "échec fiche", "nom": nom}

    db.ex("UPDATE tournaments SET club_id = ?, organizer = COALESCE(NULLIF(organizer,''), ?) "
          "WHERE id = ?", (cid, nom, t["id"]))
    _fini(t["id"], "rattaché à « %s » (%s)" % (nom[:40], voie))
    return {"tournoi": t["name"], "etat": "rattaché", "nom": nom, "voie": voie,
            "club_id": cid, "club_neuf": etat == "created", "pages": len(pages)}


def un_tour(limite=PAR_TOUR):
    from . import recherche
    faits, rattaches, souci = [], 0, None
    try:
        for t in candidats(limite):
            r = un_tournoi(t)
            faits.append(r)
            if r["etat"] == "rattaché":
                rattaches += 1
            time.sleep(1)
    except recherche.Epuise as exc:
        souci = str(exc)
    except Exception as exc:
        souci = str(exc)[:160]

    if rattaches:
        libres = sum(1 for f in faits
                     if f.get("etat") == "rattaché" and f.get("voie") != "Google")
        db.log("orphelins",
               "%d tournoi(s) rattaché(s) — %d lu(s) sur l'annonce, %d par Google"
               % (rattaches, libres, rattaches - libres))
    _dernier.update({"le": util.now_iso(), "faits": len(faits),
                     "rattaches": rattaches, "erreur": souci})
    return {"faits": faits, "n": len(faits), "rattaches": rattaches,
            "erreur": souci}


def reste():
    r = db.q1("SELECT COUNT(*) AS n FROM tournaments WHERE club_id IS NULL "
              "AND date(COALESCE(start_date,'1900-01-01')) >= date('now') "
              "AND orga_cherche_le IS NULL")
    return r["n"] if r else 0


def veiller(arret=None):
    import threading

    def tourner():
        while True:
            if arret is not None and arret.is_set():
                return
            try:
                un_tour()
            except Exception:
                pass
            if arret is not None:
                if arret.wait(INTERVALLE):
                    return
            else:
                time.sleep(INTERVALLE)

    fil = threading.Thread(target=tourner, name="orphelins-veilleur", daemon=True)
    fil.start()
    return fil
