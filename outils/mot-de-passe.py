# -*- coding: utf-8 -*-
"""Changer le mot de passe d'un compte, depuis le Terminal.

    python3 outils/mot-de-passe.py brute

Il le demande sans l'afficher, le fait taper deux fois, et n'en garde que
l'empreinte — le mot de passe lui-meme n'est ecrit nulle part, ni dans un
fichier, ni dans l'historique du Terminal. C'est aussi la porte de secours
le jour ou on ne retrouve plus le fichier des mots de passe de depart.

Changer un mot de passe ferme les sessions ouvertes ailleurs. C'est tout
l'interet d'en changer.
"""

import getpass
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prospect import comptes, db  # noqa: E402


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("Comptes : %s" % ", ".join(
            "%s (%s)" % (c["identifiant"], c["role"]) for c in comptes.lister()))
        return 1

    ident = comptes._plat(sys.argv[1])
    r = db.q1("SELECT id, identifiant, nom, role FROM comptes "
              "WHERE identifiant = ?", (ident,))
    if not r:
        print("Aucun compte « %s »." % ident)
        print("Comptes : %s" % ", ".join(
            "%s (%s)" % (c["identifiant"], c["role"]) for c in comptes.lister()))
        return 1

    print("Compte : %s (%s)" % (r["identifiant"], r["role"]))

    # « --essayer » : savoir si CE mot de passe est le bon, sans rien changer
    # et sans bruler un des six essais. Quand l'ecran de connexion refuse et
    # qu'on ne sait plus lequel des deux a tort, c'est la seule facon de
    # trancher.
    if "--essayer" in sys.argv:
        quoi = getpass.getpass("Mot de passe a tester : ")
        plein = db.q1("SELECT empreinte, sel FROM comptes WHERE id = ?",
                      (r["id"],))
        if comptes.hmac.compare_digest(
                comptes._empreinte(quoi, plein["sel"]), plein["empreinte"]):
            print("OUI : c'est bien le mot de passe de « %s »."
                  % r["identifiant"])
            print("Si l'ecran de connexion le refuse quand meme, le probleme")
            print("n'est pas vous — dites-le moi, je cherche ailleurs.")
            return 0
        print("NON : ce n'est pas le mot de passe de « %s »."
              % r["identifiant"])
        print("Relancez sans --essayer pour en poser un neuf.")
        return 1
    if db.get_setting("ouvert_internet", "0") == "1":
        print("Attention : le CRM est OUVERT sur internet en ce moment.")
        print("Un mot de passe court se devine ; prenez-en un long.")

    un = getpass.getpass("Nouveau mot de passe : ")
    deux = getpass.getpass("Encore, pour etre sur : ")
    if un != deux:
        print("Les deux ne sont pas pareils. Rien n'a ete change.")
        return 1

    try:
        comptes.changer_mot_de_passe(r["id"], un)
    except ValueError as exc:
        print("Refuse : %s" % exc)
        return 1

    # On essaie tout de suite, pour de vrai. Sans ca on sort d'ici en
    # esperant que ca marche, et on va se battre avec l'ecran de connexion
    # sans savoir lequel des deux a tort.
    try:
        jeton, _ = comptes.connexion(r["identifiant"], un, "verification")
        comptes.deconnexion(jeton)
        essai = "verifie : la connexion marche avec ce mot de passe"
    except ValueError as exc:
        essai = "ATTENTION, l'essai de connexion a echoue : %s" % exc

    print("")
    print("C'est fait. %s" % essai)
    print("")
    print("  identifiant  : %s" % r["identifiant"])
    print("  mot de passe : celui que vous venez de taper (deux fois)")
    print("")
    print("Tapez l'identifiant EXACTEMENT comme ci-dessus : ni une adresse")
    print("mail, ni un prenom. C'est ce nom-la, et rien d'autre.")
    print("Les sessions ouvertes ailleurs sont fermees.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
