# -*- coding: utf-8 -*-
"""Met ou enleve la serrure du CRM, depuis le Terminal.

Pourquoi un fichier a part, et pas un appel a /api/comptes/ouverture : une
fois la serrure mise, cette route demande elle-meme un mot de passe. Un
script qui passerait par elle saurait fermer tant que c'est ouvert... et
plus du tout des que ca l'est. Autrement dit il ne saurait pas refermer,
justement quand on en a besoin. Ici on ecrit le reglage dans la base, en
local, sur le Mac ou la base vit : ca marche dans les deux sens.

    python3 outils/serrure.py mettre
    python3 outils/serrure.py enlever
    python3 outils/serrure.py etat
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prospect import db  # noqa: E402


def etat():
    return db.get_setting("ouvert_internet", "0") == "1"


def mettre():
    # Sans compte patron actif, ouvrir reviendrait a s'enfermer dehors : le
    # CRM demanderait un mot de passe que personne ne pourrait donner.
    if not db.q1("SELECT id FROM comptes WHERE role = 'patron' AND actif = 1"):
        print("Aucun compte « patron » actif. Creez le votre dans « L'equipe »")
        print("avant d'ouvrir, sinon vous vous enfermez dehors.")
        return 1
    db.set_setting("ouvert_internet", "1")
    db.log("comptes", "CRM ouvert sur internet (depuis le Terminal)")
    print("serrure mise : tout demande un mot de passe, meme depuis le Mac")
    return 0


def enlever():
    db.set_setting("ouvert_internet", "0")
    db.log("comptes", "CRM ferme sur internet (depuis le Terminal)")
    print("serrure enlevee : le CRM n'ecoute que la maison")
    return 0


if __name__ == "__main__":
    quoi = (sys.argv[1] if len(sys.argv) > 1 else "etat").lower()
    if quoi == "mettre":
        sys.exit(mettre())
    if quoi == "enlever":
        sys.exit(enlever())
    print("serrure : %s" % ("mise" if etat() else "enlevee"))
