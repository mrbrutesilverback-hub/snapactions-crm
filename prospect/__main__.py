"""Ligne de commande.

  python3 -m prospect serve            demarre l'interface web
  python3 -m prospect veille           lance les sources et prepare les brouillons
  python3 -m prospect relances         prepare les brouillons des relances echues
  python3 -m prospect ics [fichier]    exporte le calendrier
  python3 -m prospect demo             remplit une base d'exemple
  python3 -m prospect stats            resume en console
"""
import argparse
import datetime as dt
import os
import sys
import webbrowser

from . import db, drafts, ics, repo, sources, util


def ouvrir_navigateur(url):
    """Chrome, pas le navigateur par defaut.

    Le Mac de Julien a Firefox en navigateur par defaut, or c'est Chrome qui
    porte les sessions WhatsApp et Facebook dont l'application se sert.
    webbrowser.open() suivait le reglage systeme et ouvrait Firefox a chaque
    demarrage — et le serveur redemarre a chaque modification de code.
    """
    import subprocess
    for app in ("Google Chrome", "Chromium", "Brave Browser"):
        try:
            fait = subprocess.run(["open", "-a", app, url],
                                  capture_output=True, timeout=8)
            if fait.returncode == 0:
                return app
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        webbrowser.open(url)
    except Exception:
        pass
    return None


def cmd_serve(args):
    from . import agenda, web
    httpd = web.serve(args.host, args.port)
    # Le calendrier se lit a cote, en continu : une page ne doit jamais
    # attendre qu'Apple ait fini de rendre ses sept colonnes.
    agenda.prechauffer()
    url = "http://%s:%d/" % (args.host, args.port)
    print("Prospection clubs — %s" % url)
    print("Base : %s" % db.DB_PATH)
    print("Ctrl-C pour arreter.")
    # Un redemarrage automatique n'est pas un demarrage : on ne rouvre pas
    # d'onglet a chaque fois qu'un fichier de code bouge.
    if not args.no_open and os.environ.get("PROSPECT_RELANCE") != "1":
        ouvrir_navigateur(url)

    # L'application est arretee par un signal, pas par Ctrl-C : quand on la
    # quitte, le lanceur envoie SIGTERM. Sans ce gestionnaire, Python mourait
    # en plein travail, le journal WAL restait a moitie ecrit, et le demarrage
    # suivant refusait la base — « database disk image is malformed », des
    # heures d'application morte pour un fichier parfaitement sain.
    import signal

    def _partir(signum, cadre):
        print("\nArret demande, je range la base...", flush=True)
        # Le tunnel d'abord : on ne laisse pas une adresse publique derriere
        # un CRM eteint. Et on remet la serrure a zero, sinon le demarrage
        # suivant demanderait un mot de passe pour rien.
        try:
            from . import tunnel
            tunnel.au_revoir()
        except Exception:
            pass
        try:
            db.ranger_avant_de_partir()
        except Exception:
            pass
        # Surtout pas httpd.shutdown() ici : appele depuis le thread qui
        # fait tourner serve_forever(), il attend l'arret de la boucle que
        # ce gestionnaire empeche justement de rendre la main. Les deux
        # s'attendaient, le processus survivait et gardait le port 8765 —
        # l'application suivante ne demarrait plus du tout.
        os._exit(0)

    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        try:
            signal.signal(sig, _partir)
        except (ValueError, OSError):
            pass

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nArret.")
        db.ranger_avant_de_partir()
        httpd.shutdown()
    finally:
        db.ranger_avant_de_partir()


def cmd_veille(args):
    db.init()
    results = sources.run_all()
    if not results:
        print("Aucune source configuree. Ajoutez-en dans Reglages.")
    for r in results:
        print(" - source %s : %s" % (r.get("source_id"), r.get("status") or r.get("error")))
    gen = drafts.generate_for_new_tournaments()
    if gen.get("ok"):
        print("Brouillons prepares : %d (sur %d tournoi(s))"
              % (gen["n_drafts"], gen["n_tournaments"]))
    else:
        print("Generation : %s" % gen.get("error"))
    print("A relire dans l'onglet Messages. Rien n'a ete envoye.")


def cmd_relances(args):
    db.init()
    r = drafts.generate_for_due_tasks(args.horizon)
    print("%d brouillon(s) de relance prepare(s), en attente de relecture." % r["n"])


def cmd_ics(args):
    db.init()
    text = ics.feed(args.kind)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print("Ecrit : %s" % args.output)
    else:
        sys.stdout.write(text)


def cmd_stats(args):
    db.init()
    s = repo.dashboard()
    print("Clubs %d · contacts %d (dont %d avec telephone)"
          % (s["clubs"], s["contacts"], s["with_phone"]))
    print("Relances : %d en retard, %d aujourd'hui, %d sous 7 jours"
          % (s["tasks_overdue"], s["tasks_today"], s["tasks_week"]))
    print("Messages : %d a relire, %d valides a envoyer"
          % (s["drafts_pending"], s["drafts_approved"]))
    print("Tournois a venir : %d" % s["tournaments_upcoming"])
    print("Clubs qui refroidissent : %d · opt-out : %d" % (s["cold"], s["opted_out"]))
    for row in s["by_status"]:
        if row["n"]:
            print("  %-12s %d" % (row["label"], row["n"]))


DEMO_FORUM = """
Re: Organisation du tournoi de printemps — retours d'experience
Poste par jpdupont le 12/03/2026

Bonjour a tous, je gere la partie inscriptions depuis 3 ans.

Jean-Pierre Dupont
President - Tennis Club de Villeurbanne
Tel 04 78 12 34 56 / portable 06 12 34 56 78
jp.dupont@tcvilleurbanne.fr
www.tcvilleurbanne.fr
69100 Villeurbanne

--------------------------------

Meme probleme chez nous, les tableaux a la main c'est une plaie.

Marie Lefebvre
Secretaire generale, AS Padel Montpellier
contact [at] aspadel-mtp [dot] fr
Tel : 06.99.88.77.66
34000 Montpellier

--------------------------------

Sophie Bernard
Responsable des tournois — Union Sportive de Lyon 3
s.bernard@uslyon3.org
06 44 55 66 77
69003 Lyon

--------------------------------

Nous on a bascule sur un tableur partage, ca tient.
Thomas Garnier, Tresorier
Entente Sportive de Grenoble
t.garnier@esgrenoble.fr - 06 33 22 11 00
38000 Grenoble
"""


def cmd_demo(args):
    from . import extract
    db.init()
    for key, value in {
        "my_name": "Alex Martin", "my_org": "Atelier Tournois",
        "my_email": "alex@ateliertournois.fr", "my_phone": "0611223344",
        "sport": "padel",
        "pitch": "On s'occupe des inscriptions, des tableaux et des relances joueurs, "
                 "pour que le bureau garde du temps pour le reste.",
    }.items():
        db.set_setting(key, value)

    items = extract.extract_any(DEMO_FORUM, source="Forum Padel FR",
                                source_url="https://forum.example.org/t/1234")
    n_clubs = n_contacts = 0
    for item in items:
        club_id, action = repo.upsert_club(item["club"])
        if not club_id:
            continue
        n_clubs += action == "created"
        item["contact"]["club_id"] = club_id
        _, caction = repo.upsert_contact(item["contact"])
        n_contacts += caction == "created"

    today = util.today()
    demo_tournaments = [
        {"name": "Open de Printemps de Villeurbanne", "start_date": today + dt.timedelta(days=38),
         "end_date": today + dt.timedelta(days=40), "city": "Villeurbanne",
         "postal_code": "69100", "url": "https://example.org/open-villeurbanne"},
        {"name": "Tournoi National de l'Herault", "start_date": today + dt.timedelta(days=52),
         "end_date": today + dt.timedelta(days=54), "city": "Montpellier",
         "postal_code": "34000"},
        {"name": "Master Regional Isere", "start_date": today + dt.timedelta(days=95),
         "city": "Grenoble", "postal_code": "38000"},
        {"name": "Trophee de la Ville de Lyon", "start_date": today + dt.timedelta(days=5),
         "city": "Lyon", "postal_code": "69003"},
    ]
    for t in demo_tournaments:
        t["start_date"] = t["start_date"].isoformat()
        if t.get("end_date"):
            t["end_date"] = t["end_date"].isoformat()
    created, _, _, _ = sources.save_tournaments(demo_tournaments, "demo", "padel")

    gen = drafts.generate_for_new_tournaments()
    first_club = db.q1("SELECT id FROM clubs ORDER BY id LIMIT 1")
    if first_club:
        repo.schedule_sequence(first_club["id"])

    print("Demo : %d clubs, %d contacts, %d tournois, %d brouillon(s)."
          % (n_clubs, n_contacts, len(created), gen.get("n_drafts", 0)))
    print("Deux tournois sont hors fenetre expres (un a J-5, un a J-95) : "
          "ils ne declenchent rien, c'est voulu.")
    print("Lancez : python3 -m prospect serve")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="prospect", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd")

    p = sub.add_parser("serve", help="interface web")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=int(os.environ.get("PROSPECT_PORT", 8765)))
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("veille", help="lance les sources tournois")
    p.set_defaults(fn=cmd_veille)

    p = sub.add_parser("relances", help="prepare les brouillons de relance echus")
    p.add_argument("--horizon", type=int, default=0)
    p.set_defaults(fn=cmd_relances)

    p = sub.add_parser("ics", help="export calendrier")
    p.add_argument("kind", nargs="?", default="all", choices=["all", "relances", "tournois"])
    p.add_argument("-o", "--output")
    p.set_defaults(fn=cmd_ics)

    p = sub.add_parser("demo", help="jeu de donnees d'exemple")
    p.set_defaults(fn=cmd_demo)

    p = sub.add_parser("stats", help="resume en console")
    p.set_defaults(fn=cmd_stats)

    args = parser.parse_args(argv)
    if not getattr(args, "fn", None):
        parser.print_help()
        return 0
    return args.fn(args) or 0


if __name__ == "__main__":
    sys.exit(main())
