# -*- coding: utf-8 -*-
"""Rendre joignables les clubs muets, sans que Julien clique.

Cent quatre-vingts clubs n'ont ni telephone ni mail. Soixante-treize n'ont
meme aucune page a lire : on connait leur nom et leur ville, rien d'autre.
Pour ceux-la, numeros.py n'a rien a se mettre sous la dent.

C'est la que Google sert, et seulement la : il retrouve les PAGES du club.
Les chiffres, eux, continuent d'etre lus sur ces pages par util, jamais
ecrits par un modele. La regle n'a pas bouge — un numero invente est
indiscernable d'un vrai, et trois ont deja fini dans cette base.

Ce qui sort du Mac : un nom de club et une ville. Rien d'autre.

Trois filtres avant d'ecrire, tous nes d'un vrai faux positif :
  - un numero vu chez plusieurs clubs est un standard (animationfoot.com
    s'etait retrouve sur trois clubs sans rapport) ;
  - un numero lu sur une page de mentions legales appartient au site, pas
    au club (118000.fr, leringolympien.fr) ;
  - plusieurs numeros differents : on ne devine pas lequel, on laisse.
"""

import re
import time

from . import db, util

# Google met 40 a 55 secondes par club. On en fait peu a la fois : rien ne
# presse, et le veilleur repasse.
# Cinq clubs toutes les cinq minutes : un club par minute environ, bien en
# dessous des limites de l'API, et la file de cent soixante-huit se vide en
# trois heures au lieu de vingt et une.
PAR_TOUR = 5
# Au-dela, on admet que Google ne repondra pas pour ce club-la.
ESSAIS_MAX = 3
INTERVALLE = 300
_dernier = {"le": None, "faits": 0, "joignables": 0, "erreur": None}


def dernier_passage():
    return dict(_dernier)


def _douteuse(page):
    p = (page or "").lower()
    return any(m in p for m in ("legal", "mention", "cgv", "cgu", "privacy",
                                "confidentialite"))


def candidats(limite=PAR_TOUR):
    """Les clubs muets qu'on n'a pas encore interroges."""
    return db.rows_to_dicts(db.q(
        "SELECT * FROM clubs "
        "WHERE COALESCE(phone,'') = '' AND COALESCE(email,'') = '' "
        "  AND COALESCE(do_not_contact, 0) = 0 "
        "  AND COALESCE(name,'') <> '' "
        "  AND contact_cherche_le IS NULL "
        "ORDER BY COALESCE(contact_cherche_essais,0), "
        "         COALESCE(relais,0) DESC, COALESCE(partenaire,0) DESC, id "
        "LIMIT ?", (int(limite),)))


def _deja_ailleurs(numero, club_id):
    """Ce numero est-il deja celui d'un AUTRE club ?"""
    fin = "%" + re.sub(r"\D", "", numero)[-8:]
    r = db.q1(
        "SELECT COUNT(*) AS n FROM clubs WHERE id <> ? "
        "AND replace(replace(replace(COALESCE(phone,''),' ',''),'.',''),'-','') LIKE ?",
        (int(club_id), fin))
    return (r["n"] if r else 0) > 0


def un_club(club):
    """Cherche les pages, lit les coordonnees, ecrit si c'est sur."""
    from . import numeros, recherche

    # D'ABORD ce qui est gratuit : si le club a deja un site ou une page
    # source, numeros.py sait la lire sans que Google intervienne. Quatre-
    # vingt-dix clubs sur cent deux etaient dans ce cas, et on appelait
    # Google pour chacun — a quelques centimes la requete.
    etat, pages = "rien", []
    deja_lisible = bool((club.get("website") or "").strip()
                        or (club.get("source_url") or "").strip())
    if deja_lisible:
        try:
            lu = numeros.chercher(club, maximum=5)
        except Exception:
            lu = {}
        bons = [t for t in (lu.get("trouves") or [])
                if (t.get("numero") or t.get("mail")) and not _douteuse(t.get("page"))]
        if bons:
            return _poser(club, lu, [], "lu sans Google")
        # La page a ete lue et ne dit rien : Google ne trouvera pas mieux,
        # et chaque requete ancree coute. On n'insiste que si AUCUNE page
        # n'a pu etre ouverte — la, oui, il faut en chercher d'autres.
        if any(v.get("lue") for v in (lu.get("vues") or [])):
            db.ex("UPDATE clubs SET contact_cherche_le = ?, "
                  "contact_cherche_etat = ? WHERE id = ?",
                  (util.now_iso(), "page lue, aucune coordonnée dessus",
                   club["id"]))
            return {"club": club["name"], "etat": "rien sur sa page",
                    "sans_google": True}

    if recherche.disponible():
        try:
            d = recherche.pages_du_club(club)
            pages = [u for u, _ in (d.get("pages") or [])]
        except recherche.Epuise:
            # Porte fermee : on ne touche a rien. Le club garde sa place dans
            # la file et repassera quand le quota reviendra.
            raise
        except Exception as exc:
            # Google met de 40 a plus de 200 secondes selon le club, et il
            # tombe souvent. Marquer le club « fait » sur une panne le
            # retirait DEFINITIVEMENT de la file : une seconde d'absence
            # valait une condamnation. On compte les essais, et on ne
            # renonce qu'au troisieme.
            essais = int(club.get("contact_cherche_essais") or 0) + 1
            fini = essais >= ESSAIS_MAX
            db.ex("UPDATE clubs SET contact_cherche_essais = ?, "
                  "contact_cherche_etat = ?, contact_cherche_le = ? WHERE id = ?",
                  (essais, "google muet (essai %d) : %s" % (essais, str(exc)[:60]),
                   util.now_iso() if fini else None, club["id"]))
            return {"club": club["name"],
                    "etat": "renonce" if fini else "a reessayer",
                    "essais": essais, "erreur": str(exc)[:120]}

    # La premiere page devient le site du club : meme sans numero, c'est
    # une prise. numeros.py saura la relire plus tard.
    if pages and not (club.get("website") or "").strip():
        db.ex("UPDATE clubs SET website = ?, updated_at = ? WHERE id = ?",
              (pages[0], util.now_iso(), club["id"]))
        club = dict(club, website=pages[0])
        etat = "site trouve"

    try:
        lu = numeros.chercher(club, maximum=6)
    except Exception:
        lu = {}
    return _poser(club, lu, pages, etat)


def _poser(club, lu, pages, etat):
    """Retient ce qui est sur, et marque la fiche comme faite."""
    tels, mails = [], []
    for t in (lu.get("trouves") or []):
        if t.get("numero") and not _douteuse(t.get("page")):
            if not _deja_ailleurs(t["numero"], club["id"]):
                tels.append(t)
        if t.get("mail") and not _douteuse(t.get("page")):
            mails.append(t)

    uniques = {t["numero"]: t for t in tels}
    patch, pris = {}, []
    if len(uniques) == 1:
        t = list(uniques.values())[0]
        patch["phone"] = t["numero"]
        pris.append("Téléphone lu le %s sur %s" % (util.today().isoformat(),
                                                   t.get("page")))
    umails = {m["mail"]: m for m in mails}
    if len(umails) == 1:
        m = list(umails.values())[0]
        patch["email"] = m["mail"]
        pris.append("Mail lu le %s sur %s" % (util.today().isoformat(),
                                              m.get("page")))

    if patch:
        patch["updated_at"] = util.now_iso()
        patch["notes"] = ((club.get("notes") or "").strip() + "\n"
                          + "\n".join(pris)).strip()
        db.update("clubs", club["id"], patch)
        etat = "joignable"

    db.ex("UPDATE clubs SET contact_cherche_le = ?, contact_cherche_etat = ? "
          "WHERE id = ?", (util.now_iso(), etat, club["id"]))
    return {"club": club["name"], "etat": etat,
            "tel": patch.get("phone"), "mail": patch.get("email"),
            "pages": len(pages),
            "ecartes": len(uniques) - (1 if patch.get("phone") else 0)}


def un_tour(limite=PAR_TOUR):
    """Un passage. Ne leve jamais : un veilleur ne doit pas mourir."""
    from . import recherche
    faits, joignables, souci = [], 0, None
    try:
        for club in candidats(limite):
            r = un_club(club)
            faits.append(r)
            if r["etat"] == "joignable":
                joignables += 1
            time.sleep(1)
    except recherche.Epuise as exc:
        # On arrete net : insister ferait echouer les clubs suivants et
        # consommerait leurs essais pour rien.
        souci = str(exc)
    except Exception as exc:
        souci = str(exc)[:160]

    if faits:
        # On dit qui a fait le travail. « Google » sur chaque ligne, meme
        # quand il n'a pas ete appele, faisait croire qu'il servait.
        paye = sum(1 for f in faits if not f.get("sans_google"))
        db.log("enrichissement",
               "%d club(s) traite(s), %d joignable(s) — %d lu(s) gratuitement, "
               "%d passe(s) par Google"
               % (len(faits), joignables, len(faits) - paye, paye))
    _dernier.update({"le": util.now_iso(), "faits": len(faits),
                     "joignables": joignables, "erreur": souci})
    return {"faits": faits, "n": len(faits), "joignables": joignables,
            "erreur": souci}


def reste():
    r = db.q1("SELECT COUNT(*) AS n FROM clubs "
              "WHERE COALESCE(phone,'') = '' AND COALESCE(email,'') = '' "
              "AND COALESCE(do_not_contact,0) = 0 AND contact_cherche_le IS NULL")
    return r["n"] if r else 0


def veiller(arret=None):
    """En fond, pour que Julien n'ait plus jamais a cliquer « Chercher »."""
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

    fil = threading.Thread(target=tourner, name="enrichir-veilleur", daemon=True)
    fil.start()
    return fil
