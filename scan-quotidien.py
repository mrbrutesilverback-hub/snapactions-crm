#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Le passage quotidien : la veille, les flyers deposes, les brouillons.

Tourne sans le serveur web — c'est voulu. Si l'application n'est pas lancee au
moment du scan, le travail doit se faire quand meme ; on le retrouvera a
l'ouverture. Journalise dans data/scan.log, en gardant les 400 dernieres lignes.
"""
import datetime as dt
import io
import os
import sys
import traceback

RACINE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, RACINE)
os.chdir(RACINE)

JOURNAL = os.path.join(RACINE, "data", "scan.log")
GARDE = 400


def dire(texte):
    ligne = "[%s] %s" % (dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), texte)
    print(ligne, flush=True)
    try:
        lignes = []
        if os.path.exists(JOURNAL):
            with io.open(JOURNAL, encoding="utf-8") as f:
                lignes = f.readlines()[-(GARDE - 1):]
        with io.open(JOURNAL, "w", encoding="utf-8") as f:
            f.writelines(lignes)
            f.write(ligne + "\n")
    except OSError:
        pass


def _par_le_serveur(chemin, corps=None):
    """Fait faire le travail par l'application si elle tourne, sinon None.

    Le serveur demarre depuis lancer.command, donc depuis le Terminal : il
    herite de ses autorisations macOS, ce que launchd n'a pas.

    On presente la cle de la routine. Sans elle, des que le CRM est ouvert
    pour le sourceur, le serveur repond 401 et ce scan n'avait aucun moyen de
    le savoir : l'erreur etait avalee avec les pannes reseau, et le journal
    annoncait « application fermee » alors qu'elle tournait. Les grilles de
    groupes ne s'ouvraient plus le matin, sans un mot pour le dire.
    """
    import json
    import urllib.error
    import urllib.request
    donnees = json.dumps(corps or {}).encode("utf-8")
    entetes = {"Content-Type": "application/json"}
    try:
        from prospect import comptes
        cle = comptes.cle_routine()
        if cle:
            entetes["X-Routine"] = cle
    except Exception:
        pass
    requete = urllib.request.Request(
        "http://127.0.0.1:8765" + chemin, data=donnees, headers=entetes)
    try:
        with urllib.request.urlopen(requete, timeout=120) as rep:
            return json.loads(rep.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # HTTPError descend de URLError : il doit etre attrape AVANT, sinon
        # un refus net redevient une panne muette.
        dire("  refus de l'application sur %s (%s)" % (chemin, exc.code))
        return None
    except (urllib.error.URLError, OSError, ValueError):
        return None


def main():
    from prospect import db, drafts, flyer, repo, sources, wa, wa_local

    dire("=== scan quotidien ===")

    # 0. La sauvegarde, avant tout le reste : si quelque chose casse pendant
    #    le scan, la copie du matin est deja faite.
    copie = db.sauvegarder()
    dire("  sauvegarde : %s" % (os.path.basename(copie) if copie else "ÉCHEC"))

    # 1. La veille : les sources configurees.
    total = 0
    for r in sources.run_all():
        nom = db.q1("SELECT name FROM sources WHERE id = ?", (r.get("source_id"),))
        nom = nom["name"] if nom else "source"
        if r.get("ok"):
            total += r.get("n_created", 0)
            dire("  veille %-38s %s" % (nom[:38], r.get("status")))
        else:
            dire("  veille %-38s ECHEC : %s" % (nom[:38], r.get("error")))

    # 2. Les flyers deposes dans a-traiter/ depuis la derniere fois.
    depot = os.path.join(RACINE, "a-traiter")
    attente = [n for n in os.listdir(depot)
               if not n.startswith(".") and n != "LISEZ-MOI.txt"
               and os.path.isfile(os.path.join(depot, n))] if os.path.isdir(depot) else []
    if attente:
        dire("  flyers en attente : %d" % len(attente))
        for r in flyer.depouiller_dossier(depot):
            dire("    %-24s club=%s tournoi=%s %s" % (
                (r.get("fichier") or "")[:24],
                (r.get("club") or {}).get("nom"),
                (r.get("tournoi") or {}).get("nom"),
                r.get("soucis") or ""))
    else:
        dire("  aucun flyer en attente")

    # 3. WhatsApp : ou en est chaque conversation. Lecture seule de la base
    #    locale, aucun message n'est lu ni envoye. Demande l'Acces complet au
    #    disque pour le processus qui lance ce script.
    try:
        base, conversations = wa_local.lire_journal()
        b = wa.enregistrer(conversations)
        c = wa.synchroniser_contacts()
        env = wa.rapprocher_brouillons()
        dire("  whatsapp : %d conversation(s), %d rattachee(s), "
             "%d contact(s) cree(s), %d brouillon(s) marque(s) envoyes"
             % (b["vus"], b["lies"], c["crees"], env))
    except wa_local.Inaccessible as exc:
        # Lance par launchd, ce script n'a pas l'Acces complet au disque : le
        # serveur, lui, l'a (il demarre depuis le Terminal). On lui demande de
        # faire la lecture a notre place quand il tourne.
        dire("  whatsapp : lecture directe refusee, on passe par l'application")
        bilan = _par_le_serveur("/api/whatsapp/relire")
        if bilan is None:
            # Etape optionnelle : elle repere les clubs qui ont repondu sur
            # WhatsApp. Le reste du scan n'en depend pas, donc on passe sans
            # en faire une alerte. Pour l'activer sans ouvrir l'application,
            # il faut donner l'Acces complet au disque a /usr/bin/python3.
            dire("  whatsapp : ignore (optionnel, demande l'Acces complet au disque)")
        else:
            dire("  whatsapp : %d conversation(s), %d rattachee(s), "
                 "%d contact(s) cree(s)"
                 % (bilan.get("vus", 0), bilan.get("lies", 0),
                    (bilan.get("contacts") or {}).get("crees", 0)))

    # 3 bis. Les groupes Facebook. On OUVRE les grilles d'affiches, on ne les
    #        moissonne pas : Meta interdit la collecte automatisee et ferme les
    #        comptes qui s'y risquent — or ce compte porte les adhesions, donc
    #        la source elle-meme. Le depouillement se fait d'un clic, depuis la
    #        navigation de Julien, avec le favori « Lire les annonces ».
    if db.get_setting("groupes_ouvrir", "1") == "1":
        ouvert = _par_le_serveur("/api/groupes/ouvrir")
        if ouvert is None:
            dire("  groupes  : application fermee, grilles non ouvertes")
        else:
            dire("  groupes  : %d grille(s) ouverte(s) dans Chrome, "
                 "a depouiller avec le favori" % ouvert.get("n", 0))

    # 3 ter. Plus de purge des fiches lointaines. sources.purger_hors_perimetre
    #        a ete mise a la retraite le jour ou Julien s'est mis a recruter
    #        des photographes partout : une fiche lointaine n'est plus un
    #        dechet, c'est du travail pour quelqu'un d'autre, et elle est
    #        marquee « reseau ». La fonction leve donc une RuntimeError quand
    #        on l'appelle pour de vrai — l'appel etait reste ici, et faisait
    #        mourir le scan juste avant la purge, les contacts et la suite.

    # Le reste du menage : tournois passes, affiches en double, vieilles
    # sauvegardes. Rien de ce que Julien a touche n'y passe — une mission, un
    # message envoye ou une de ses propres dates est intouchable.
    from prospect import purge
    bilan = purge.passer(pour_de_vrai=True)
    dire("  purge    : %s" % purge.resume(bilan))

    # 4. Les contacts restes dans le texte des annonces : sans eux, le
    #    brouillon nait sans destinataire et ne partira jamais.
    e = repo.enrichir_contacts_depuis_annonces()
    dire("  contacts repeches : %d club(s), %d tel, %d mail"
         % (e["clubs"], e["telephones"], e["emails"]))

    # 5. Les brouillons pour ce qui vient d'arriver. Rien n'est envoye : le
    #    reglage auto_send reste a 0, seul Julien appuie sur le bouton.
    if db.get_setting("auto_draft", "1") == "1":
        g = drafts.generate_for_new_tournaments()
        dire("  brouillons : %s" % (g.get("n_drafts") if g.get("ok") is not False
                                    else g.get("error")))
    dire("  destinataires recolles : %d" % drafts.recoller_destinataires())

    # 6. Le modele local, pendant que Julien dort. Trois travaux qui prennent
    #    chacun quelques secondes par fiche : hors de question de les faire
    #    pendant qu'il attend devant un ecran.
    try:
        from prospect import ia, wa as _wa, repo as _repo
        if ia.disponible():
            r = _wa.resumer_fils(60)
            dire("  resumes   : %d fil(s) resume(s) par %s"
                 % (r.get("faits", 0), ia.modele()))
            # Les reponses proposees aux clubs qui ont ecrit : prete au reveil,
            # l'ecran n'a plus qu'a les lire.
            n = 0
            for col in _wa.suivi().get("colonnes", []):
                if col.get("cle") != "a_traiter":
                    continue
                for carte in col.get("cartes", []):
                    if _wa.reponse_du_fil(carte):
                        n += 1
            dire("  reponses  : %d reponse(s) proposee(s) d'apres le fil" % n)
            o = _repo.reparer_organisateurs(40, creer=False)
            dire("  organisateurs : %d lu(s), %d tournoi(s) rattache(s), "
                 "%d sans organisateur nomme"
                 % (o.get("traites", 0), o.get("rattaches", 0),
                    o.get("sans_organisateur", 0)))
        else:
            dire("  modele local : absent (Ollama ferme ?), etapes sautees")
    except Exception as exc:
        dire("  modele local : %s" % exc)

    reste = db.q1("SELECT count(*) n FROM tournaments WHERE "
                  "COALESCE(validation,'a_valider')='a_valider' "
                  "AND (start_date IS NULL OR start_date >= date('now'))")
    dire("=== fin : %d nouveau(x), %d tournoi(s) a trancher ===" % (total, reste["n"]))


def passer_les_routines():
    """Les questions que Julien a mises sous surveillance.

    « Les clubs de danse a Nice » n'est pas une recherche a faire une fois :
    la reponse change chaque semaine, et le travail consiste a voir ce qui
    s'y ajoute. On repose donc la question chaque nuit, et on ne compte que
    ce qui n'y etait pas la veille.
    """
    from prospect import cerveau
    bilan = cerveau.passer_les_routines()
    if not bilan:
        return
    neufs = sum(b.get("nouveaux") or 0 for b in bilan)
    dire("  routines : %d question(s) reposee(s), %d nouveaute(s)" % (len(bilan), neufs))
    for b in bilan:
        if b.get("erreur"):
            dire("    « %s » : %s" % (b["question"][:50], b["erreur"]))
        elif b.get("nouveaux"):
            dire("    « %s » : %d — %s"
                 % (b["question"][:50], b["nouveaux"], ", ".join(b.get("exemples") or [])[:90]))


def relever_le_carnet():
    """Ce qui est arrive dans le carnet d'adresses depuis hier.

    La date de creation d'un contact ne dit rien : la synchronisation iCloud
    de juillet 2026 a donne le 2 juillet a 5 278 fiches sur 5 280. On compare
    donc le carnet a l'empreinte de la veille — ce qui n'y figure pas est
    nouveau, quelle que soit la date qu'il porte.
    """
    from prospect import carnet
    try:
        d = carnet.relever()
    except carnet.CarnetIndisponible as exc:
        dire("  carnet : %s" % exc)
        return
    if d["premier_releve"]:
        dire("  carnet : empreinte posee sur %d contacts" % d["total"])
        return
    n = carnet.enregistrer_rencontres(d["nouveaux"])
    if n:
        dire("  carnet : %d nouveau(x) contact(s) -> ecran Rencontres" % n)
        for g in d["nouveaux"][:6]:
            tel = (g.get("tels") or [""])[0]
            dire("     %s %s" % ((g.get("nom") or "")[:40], tel))
    else:
        dire("  carnet : rien de neuf sur %d contacts" % d["total"])


def prevenir_application():
    """L'application, si elle tourne, recharge ses donnees apres le scan."""
    try:
        import urllib.request
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8765/api/redemarrer",
                                   data=b"{}",
                                   headers={"Content-Type": "application/json"}),
            timeout=5).read()
    except Exception:
        pass          # l'application n'est pas ouverte : rien a prevenir


if __name__ == "__main__":
    try:
        main()
        try:
            passer_les_routines()
        except Exception as exc:          # noqa: BLE001 — le scan ne tombe pas pour ca
            dire("  routines : %s" % exc)
        try:
            relever_le_carnet()
        except Exception as exc:          # noqa: BLE001
            dire("  carnet : %s" % exc)
        prevenir_application()
    except Exception as exc:
        dire("PLANTAGE : %s" % exc)
        dire(traceback.format_exc())
        sys.exit(1)
