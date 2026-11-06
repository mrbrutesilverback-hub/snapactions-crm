# -*- coding: utf-8 -*-
"""Le carnet d'adresses du Mac, surveille.

Julien enregistre un numero sur son iPhone en serrant la main de quelqu'un.
Quelques heures plus tard iCloud le pousse sur le Mac — et il se noie dans
cinq mille deux cent quatre-vingts fiches.

La date de creation ne sert a rien pour le retrouver : la synchronisation de
juillet 2026 a ecrase les vraies dates, et 5 278 contacts sur 5 280 portent
le 2 juillet. Demander « qui est nouveau depuis hier » a Contacts, c'est donc
une impasse.

On procede autrement, et c'est plus sur : on garde l'empreinte de ce qu'on a
deja vu. Ce qui n'y figure pas est nouveau — quelle que soit la date que la
fiche s'attribue. Le premier relev fait la photo de depart ; a partir de la,
tout ce qui arrive se voit.
"""

import json
import os
import re
import subprocess

from . import db, util

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EMPREINTE = os.path.join(RACINE, "data", "carnet-vu.json")

# Lire les 5 280 fiches colonne par colonne coute trois secondes. On ne le
# fait donc pas a chaque ouverture d'ecran, mais au scan du matin et sur
# demande explicite.
DELAI = 60

# Au-dela, on ne croit plus a des rencontres : voir le garde-fou de relever().
SEUIL = 150

_JXA = r"""
function run() {
  const C = Application("Contacts");
  const col = C.people;
  const ids = col.id(), noms = col.name();
  const org = col.organization(), job = col.jobTitle();
  const notes = col.note();
  const crees = col.creationDate(), modif = col.modificationDate();
  const out = [];
  for (let i = 0; i < ids.length; i++) {
    out.push({
      id: String(ids[i]),
      nom: noms[i] || "",
      org: org[i] || "",
      role: job[i] || "",
      note: (notes[i] || "").slice(0, 300),
      cree: crees[i] ? String(crees[i]) : "",
      modif: modif[i] ? String(modif[i]) : "",
    });
  }
  return JSON.stringify(out);
}
"""

# Les numeros sont demandes a part : une fiche peut en porter plusieurs, et
# le lot ne se lit pas colonne par colonne.
_JXA_TELS = r"""
function run(argv) {
  const C = Application("Contacts");
  const voulus = JSON.parse(argv[0]);
  const out = {};
  for (let i = 0; i < voulus.length; i++) {
    try {
      const p = C.people.byId(voulus[i]);
      const tels = p.phones().map(function (t) { return t.value(); });
      const mails = p.emails().map(function (e) { return e.value(); });
      out[voulus[i]] = {tels: tels, mails: mails};
    } catch (e) { out[voulus[i]] = {tels: [], mails: []}; }
  }
  return JSON.stringify(out);
}
"""


class CarnetIndisponible(Exception):
    """Contacts n'a pas repondu — on le dit, on ne devine pas."""


def _appeler(script, argument=None):
    cmd = ["osascript", "-l", "JavaScript", "-e", script]
    if argument is not None:
        cmd.append(json.dumps(argument))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise CarnetIndisponible("Contacts n'a pas répondu en %d s" % DELAI)
    except OSError as e:
        raise CarnetIndisponible("osascript indisponible : %s" % e)
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        raise CarnetIndisponible(detail[-1] if detail else "erreur Contacts")
    try:
        return json.loads(r.stdout or "null")
    except ValueError:
        raise CarnetIndisponible("réponse illisible de Contacts")


def _vus():
    try:
        with open(EMPREINTE, encoding="utf-8") as f:
            d = json.load(f)
        return set(d.get("ids") or []), d.get("le")
    except (OSError, ValueError):
        return set(), None


def _noter(ids):
    try:
        with open(EMPREINTE, "w", encoding="utf-8") as f:
            json.dump({"le": util.now_iso(), "ids": sorted(ids)}, f)
    except OSError:
        pass


# -- Lecture directe des bases du carnet ------------------------------------
#
# Contacts.app repond en trois secondes et il faut qu'il soit la. Les bases
# sont juste a cote, et il y en a cinq : la principale plus les caches de
# synchronisation. On les lit toutes, en lecture seule, sur une copie — on ne
# touche jamais au carnet de Julien.
#
# Les dates de creation sont inutilisables (5 162 fiches datees du 2 juillet
# 2026, le jour de l'import), on garde donc l'empreinte comme avant. Mais on
# la calcule maintenant sans dependre d'une application ouverte.

BASES = os.path.join(os.path.expanduser("~"), "Library", "Application Support",
                     "AddressBook", "Sources")


def _sources():
    try:
        noms = sorted(os.listdir(BASES))
    except OSError:
        return []
    out = []
    for n in noms:
        f = os.path.join(BASES, n, "AddressBook-v22.abcddb")
        if os.path.exists(f):
            out.append((n, f))
    return out


_REQUETE = """
SELECT x.ZUNIQUEID, x.ZFIRSTNAME, x.ZLASTNAME, x.ZORGANIZATION, x.ZJOBTITLE,
       x.ZNICKNAME, x.ZCREATIONDATE, x.ZMODIFICATIONDATE,
       (SELECT GROUP_CONCAT(p.ZFULLNUMBER, '|')
          FROM ZABCDPHONENUMBER p WHERE p.ZOWNER = x.Z_PK),
       (SELECT GROUP_CONCAT(e.ZADDRESS, '|')
          FROM ZABCDEMAILADDRESS e WHERE e.ZOWNER = x.Z_PK)
  FROM ZABCDRECORD x
 WHERE x.ZUNIQUEID IS NOT NULL
   AND (x.ZFIRSTNAME IS NOT NULL OR x.ZLASTNAME IS NOT NULL
        OR x.ZORGANIZATION IS NOT NULL)
"""


def _horodate(brut):
    """Les dates Core Data comptent depuis le 1er janvier 2001."""
    if not brut:
        return ""
    try:
        import datetime
        d = datetime.datetime.fromtimestamp(float(brut) + 978307200)
        return d.isoformat(" ", "seconds")
    except (ValueError, OSError, OverflowError):
        return ""


def _lire_une_base(chemin):
    """Copie la base et ses journaux, puis la lit. Rien n'est verrouille."""
    import shutil
    import sqlite3
    import tempfile
    dossier = tempfile.mkdtemp(prefix="carnet-")
    try:
        cible = os.path.join(dossier, "ab.abcddb")
        shutil.copyfile(chemin, cible)
        for suffixe in ("-wal", "-shm"):
            try:
                shutil.copyfile(chemin + suffixe, cible + suffixe)
            except OSError:
                pass
        con = sqlite3.connect(cible)
        try:
            lignes = con.execute(_REQUETE).fetchall()
        finally:
            con.close()
    finally:
        try:
            shutil.rmtree(dossier, ignore_errors=True)
        except OSError:
            pass

    gens = []
    for (uid, prenom, nom, org, role, surnom, cree, modif, tels, mails) in lignes:
        entier = " ".join(x for x in (prenom, nom) if x).strip()
        if not entier:
            entier = (surnom or org or "").strip()
        gens.append({
            "id": str(uid),
            "nom": entier,
            "org": (org or "").strip(),
            "role": (role or "").strip(),
            "note": "",
            "cree": _horodate(cree),
            "modif": _horodate(modif),
            "tels": [t for t in (tels or "").split("|") if t.strip()],
            "mails": [m for m in (mails or "").split("|") if m.strip()],
        })
    return gens


def lire_bases():
    """Toutes les fiches des cinq sources, fusionnees sur leur identifiant.

    Une meme personne figure souvent dans la base principale et dans un
    cache ; la fiche la mieux garnie gagne, pour ne pas perdre un numero.
    """
    fusion = {}
    for _, chemin in _sources():
        try:
            gens = _lire_une_base(chemin)
        except Exception:
            continue          # une base qui tousse n'empeche pas les autres
        for g in gens:
            ancien = fusion.get(g["id"])
            if ancien is None or len(g["tels"]) > len(ancien["tels"]):
                fusion[g["id"]] = g
    return list(fusion.values())


def relever(enregistrer=True):
    """Compare le carnet a la derniere empreinte et rend ce qui est nouveau.

    Au tout premier appel il n'y a pas d'empreinte : on la pose et on ne
    declare rien de neuf — sinon les cinq mille fiches du carnet
    arriveraient d'un coup comme autant de rencontres du jour.
    """
    # Les bases d'abord : instantanees, et elles n'exigent pas que Contacts
    # soit ouvert. Contacts ne sert plus que de secours.
    gens = lire_bases()
    direct = bool(gens)
    if not direct:
        gens = _appeler(_JXA)
    if not isinstance(gens, list) or not gens:
        raise CarnetIndisponible("le carnet d'adresses est illisible")
    tous = {g["id"] for g in gens}
    vus, le = _vus()

    if not vus:
        if enregistrer:
            _noter(tous)
        return {"premier_releve": True, "total": len(tous), "nouveaux": [],
                "depuis": None}

    neufs = [g for g in gens if g["id"] not in vus]

    # Garde-fou. Un carnet ne gagne pas deux cents personnes dans la journee.
    # Si ca arrive, c'est que la facon de lire a change ou que le carnet a ete
    # reconstruit : on repose l'empreinte au lieu de noyer Julien sous cinq
    # mille cartes qu'il n'a jamais rencontrees.
    if len(neufs) > SEUIL:
        if enregistrer:
            _noter(tous)
        return {"premier_releve": False, "repose": True, "total": len(tous),
                "nouveaux": [], "ecartes": len(neufs), "depuis": le}

    # Les numeros, seulement pour les nouveaux : c'est ce qui coute cher.
    # En lecture directe ils sont deja la, on ne redemande rien.
    if neufs and not direct:
        coords = _appeler(_JXA_TELS, [g["id"] for g in neufs[:60]]) or {}
        for g in neufs:
            c = coords.get(g["id"]) or {}
            g["tels"] = c.get("tels") or []
            g["mails"] = c.get("mails") or []
    if enregistrer:
        _noter(tous)
    return {"premier_releve": False, "total": len(tous),
            "nouveaux": neufs, "depuis": le}


def ou_etait_il(jour):
    """Ce que le calendrier de travail dit de cette journee-la.

    C'est la cle du souvenir. Un numero enregistre le 18 octobre ne dit rien
    tout seul — « Daniel », et puis ? Mais si l'agenda porte « Randonnee
    aviron Baie des Anges » ce jour-la, alors Daniel est quelqu'un de la
    rando, et tout revient. On ne devine pas : on rapproche deux faits.
    """
    from . import agenda
    try:
        evs, _, _ = agenda.lister_vite(attendre=False)
    except Exception:  # noqa: BLE001 — sans agenda on note le contact quand meme
        return []
    sortie = []
    for e in evs:
        debut = (e.get("debut") or "")[:10]
        fin = (e.get("fin") or debut)[:10]
        if debut and debut <= jour <= (fin or debut):
            titre = (e.get("titre") or "").strip()
            if titre and titre not in sortie:
                sortie.append(titre)
    return sortie[:3]


def _plat_nom(n):
    return util.strip_accents(str(n or "")).lower().strip()


def enregistrer_rencontres(neufs):
    """Range les nouveaux venus du carnet dans la table des rencontres."""
    _table()
    faits = 0
    moi = _plat_nom(db.get_setting("my_name", "") or "")
    for g in neufs:
        tel = (g.get("tels") or [None])[0]
        mail = (g.get("mails") or [None])[0]
        nom = (g.get("nom") or "").strip()
        # Une rencontre a un nom. Un contact sans nom, ou dont le « nom » est
        # son propre numero, n'en est pas une : ce sont les restes d'un
        # carnet, pas des gens croises sur le terrain. Treize sont entrees
        # ainsi le 24/10/2026, le jour ou l'application a obtenu l'acces au
        # disque — dont Julien lui-meme.
        if not nom or re.fullmatch(r"[+\d\s().-]{4,}", nom):
            continue
        if moi and _plat_nom(nom) == moi:
            continue
        if not (tel or mail):
            continue
        cle = g.get("id")
        if db.q1("SELECT id FROM rencontres WHERE carnet_id = ?", (cle,)):
            continue
        jour = util.today().isoformat()
        contexte = ou_etait_il(jour)
        db.insert("rencontres", {
            "carnet_id": cle, "nom": nom,
            "organisation": (g.get("org") or "").strip() or None,
            "role": (g.get("role") or "").strip() or None,
            "telephone": util.normalize_phone(tel) if tel else None,
            "email": util.normalize_email(mail) if mail else None,
            "note": (g.get("note") or "").strip() or None,
            # Ou vous etiez ce jour-la : c'est ce qui fait revenir le souvenir.
            "ou": " · ".join(contexte) or None,
            "source": "Carnet d'adresses",
            "vu_le": jour,
            "created_at": util.now_iso(),
        })
        faits += 1
    return faits


SCHEMA = """
CREATE TABLE IF NOT EXISTS rencontres (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    carnet_id    TEXT UNIQUE,
    nom          TEXT NOT NULL,
    organisation TEXT,
    role         TEXT,
    telephone    TEXT,
    email        TEXT,
    note         TEXT,
    ou           TEXT,
    source       TEXT,
    vu_le        TEXT,
    club_id      INTEGER REFERENCES clubs(id) ON DELETE SET NULL,
    traite       INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT NOT NULL
);
"""


def _table():
    db.ex(SCHEMA)
    # Ce dont on a parle avec cette personne. Sans ca, le CRM ne sait pas
    # faire la difference entre « croise deux minutes au bord du bassin » et
    # « on a discute d'une couverture de regate » — et il renvoie sa
    # presentation a quelqu'un qui la connait deja.
    try:
        colonnes = {r["name"] for r in db.q("PRAGMA table_info(rencontres)")}
    except Exception:
        return
    if "sujet" not in colonnes:
        try:
            db.ex("ALTER TABLE rencontres ADD COLUMN sujet TEXT")
        except Exception:
            pass


def ajouter_a_la_main(nom, telephone=None, email=None, role=None,
                      organisation=None, ou=None, note=None):
    """La saisie directe : un numero pris sur le terrain, tapé en dix secondes.

    C'est le chemin qui ne depend de rien — ni d'iCloud, ni de WhatsApp, ni
    d'une synchronisation qui arrivera peut-etre ce soir.
    """
    _table()
    nom = (nom or "").strip()
    tel = util.normalize_phone(telephone) if telephone else None
    if not nom and not tel:
        return {"ok": False, "error": "il faut au moins un nom ou un numéro"}
    if tel:
        deja = db.q1("SELECT id, nom FROM rencontres WHERE telephone = ?", (tel,))
        if deja:
            return {"ok": True, "id": deja["id"], "deja": True, "nom": deja["nom"]}
    rid = db.insert("rencontres", {
        "nom": nom or tel, "telephone": tel,
        "email": util.normalize_email(email) if email else None,
        "role": (role or "").strip() or None,
        "organisation": (organisation or "").strip() or None,
        "ou": (ou or "").strip() or None,
        "note": (note or "").strip() or None,
        "source": "Saisi sur le terrain",
        "vu_le": util.today().isoformat(),
        "created_at": util.now_iso(),
    })
    return {"ok": True, "id": rid, "deja": False}


def liste(inclure_traites=False):
    _table()
    where = "" if inclure_traites else "WHERE COALESCE(r.traite, 0) = 0"
    rows = db.rows_to_dicts(db.q(
        "SELECT r.*, c.name AS club_nom FROM rencontres r "
        "LEFT JOIN clubs c ON c.id = r.club_id %s "
        "ORDER BY r.vu_le DESC, r.id DESC" % where))
    for r in rows:
        r["mot"] = mot_pour(r)
    return rows


def mot_pour(r):
    """Le message qu'on envoie apres avoir vu quelqu'un.

    Deux situations, et elles n'ont rien a voir :

    - on a DISCUTE de quelque chose de precis (« une couverture de regate ») :
      la presentation a deja ete faite de vive voix. La lui refaire par ecrit
      est au mieux inutile, au pire vexant. Trois lignes suffisent : on
      rappelle le sujet, on envoie ce qu'on a promis, on demande la suite.
    - on s'est juste croises : la, oui, il faut expliquer en deux phrases ce
      qu'on fait.

    Dans les deux cas c'est un message WhatsApp, pas une lettre : pas de
    « Bien cordialement », pas de « plaquette informative ».
    """
    prenom = (r.get("nom") or "").split()[0] if r.get("nom") else ""
    if prenom and not prenom[0].isalpha():
        prenom = ""
    ou = (r.get("ou") or "").strip()
    sujet = (r.get("sujet") or "").strip()

    tete = "Bonjour%s, Julien de SnapActions" % ((" " + prenom) if prenom else "")
    if ou:
        tete += " — on s'est vus %s." % _dit_ou(ou)
    else:
        tete += "."

    if sujet:
        # On a parle d'un projet : on reprend LE sujet, et on donne la suite.
        return ("%s\n\nContent d'avoir parlé %s. Je vous envoie la "
                "plaquette comme convenu.\n\nDites-moi dès que vous avez la "
                "date, je la bloque." % (tete, _sans_article(sujet)))

    quand = r.get("vu_le") or ""
    if not ou and quand:
        tete = ("Bonjour%s, Julien de SnapActions — on a échangé nos numéros %s."
                % ((" " + prenom) if prenom else "", _dit_quand(quand)))
    return ("%s\n\nJe photographie les clubs et leurs événements dans le coin : "
            "les familles retrouvent leurs photos dans une galerie, et le club "
            "n'avance rien.\n\nSi ça peut servir chez vous, je vous envoie la "
            "plaquette." % tete)


def _sans_article(sujet):
    """Colle « de » au sujet sans casser la langue.

    « une couverture de la régate » -> « d'une couverture de la régate ».
    Retirer l'article donnait « parlé de couverture », qui ne se dit pas.
    """
    import re as _re
    s = (sujet or "").strip().rstrip(".")
    s = _re.sub(r"^de\s+(?=(?:la|le|les|l'|un|une|des)\b)", "", s, flags=_re.I)
    if not s:
        return ""
    if _re.match(r"^(?:un|une|des)\b", s, _re.I) or s[:1].lower() in "aeiouéèêh":
        return "d'%s" % s[:120]
    return "de %s" % s[:120]


# Deux consignes, parce que ce sont deux messages differents. Celle du bas
# a ete ecrite apres un ratage reel : le modele avait sorti « en prenant en
# charge les frais de photographie » — ce qui dit que Julien paie, l'inverse
# de son argument — et « couvrir l'evenement photographiquement ».

_SOCLE = """Julien est photographe sportif independant (SnapActions, pres de \
Cannes). Il ecrit un message WhatsApp, pas une lettre.

Tu rends UNIQUEMENT un objet JSON : {"corps": "..."}.

Regles communes :
- vouvoiement ;
- francais simple et parle, des phrases courtes ;
- ne mets NI salutation d'ouverture NI signature : on les ajoute nous-memes ;
- n'invente JAMAIS un lieu, une date, un nom de club, un evenement ou un prix ;
- INTERDIT : « cordialement », « bien a vous », « plaquette informative », \
« couvrir photographiquement », « prestation », « je me permets de ».

Son offre, a dire avec SES mots si tu dois l'expliquer : les familles \
retrouvent leurs photos dans une galerie en ligne, et le club ou le comite \
N'AVANCE RIEN — ce sont les familles qui achetent leurs photos.
INTERDIT de dire que Julien prend en charge des frais ou paie quoi que ce \
soit : c'est l'inverse."""

CONSIGNE_RENCONTRE = _SOCLE + """

Situation : ils se sont croises, sans parler d'un projet precis.
- deux a trois phrases ;
- expliquer brievement l'offre ;
- finir en proposant d'envoyer la plaquette, comme quelque chose de nouveau ;
- INTERDIT de laisser croire qu'un document a deja ete envoye ou montre."""

CONSIGNE_SUITE = _SOCLE + """

Situation : ils se sont VRAIMENT parle, d'un projet precis qu'on te donne.
Julien lui a deja explique son offre de vive voix.
- TROIS PHRASES MAXIMUM, c'est un message de suite de conversation ;
- INTERDIT de reexpliquer l'offre ou de se presenter : il le sait deja, le \
lui redire par ecrit est vexant ;
- rappeler le sujet dont ils ont parle, en une demi-phrase ;
- dire qu'on envoie la plaquette comme convenu ;
- finir par une question concrete sur la suite (la date, le lieu, qui \
decide) ;
- ton d'un pro content d'avoir discute, pas d'un commercial qui relance."""


def mot_par_ia(r):
    """La meme reprise de contact, mais ecrite par le modele local.

    Le modele ne recoit que ce qu'on sait vraiment : le prenom, le role,
    l'organisation, l'endroit. Il ne peut donc pas inventer une rencontre.
    None si Ollama dort ou si la reponse est inutilisable.
    """
    from . import ia
    if not ia.disponible():
        return None
    su = []
    if (r.get("nom") or "").strip():
        su.append("Prenom : %s" % (r["nom"] or "").split()[0])
    if (r.get("role") or "").strip():
        su.append("Son role : %s" % r["role"])
    if (r.get("organisation") or "").strip():
        su.append("Sa structure : %s" % r["organisation"])
    if (r.get("ou") or "").strip():
        su.append("Ou ils se sont vus : %s" % r["ou"])
    else:
        su.append("On ne sait PAS ou ils se sont vus : n'en parle pas.")
    if (r.get("note") or "").strip():
        su.append("Note de Julien : %s" % r["note"][:200])
    sujet = (r.get("sujet") or "").strip()
    if sujet:
        su.append("Ce dont ils ont parle ensemble : %s" % sujet[:300])
    d = ia._json(CONSIGNE_SUITE if sujet else CONSIGNE_RENCONTRE, "\n".join(su))
    corps = ia._mot(d, "corps", 900) if d else None
    if not corps or "[" in corps:
        return None
    import re as _re
    corps = _re.sub(r"^\s*(bonjour|bonsoir|salut)\b[^,\n]*,?\s*", "", corps,
                    flags=_re.I)
    corps = _re.sub(r"\s*(bonne (journ[ée]e|soir[ée]e)|cordialement)[\s\S]*$",
                    "", corps, flags=_re.I)
    corps = _re.sub(r"\s*julien\s*[—-]?\s*snapactions\.?\s*$", "", corps,
                    flags=_re.I).strip()
    if not corps:
        return None
    prenom = (r.get("nom") or "").split()[0] if r.get("nom") else ""
    if prenom and not prenom[0].isalpha():
        prenom = ""
    tete = "Bonjour%s, Julien de SnapActions." % ((" " + prenom) if prenom else "")
    return "%s\n\n%s" % (tete, corps[:1].upper() + corps[1:])


# Le genre des mots qui ouvrent un nom d'evenement, chez Julien. Sans ca on
# ecrivait « a la forum des sports ». Ce qui n'est pas dans la liste garde sa
# majuscule et se passe d'article : « a Trophee Jean Moulin » se lit comme un
# nom propre, « a la Trophee » ne se lit pas du tout.
FEMININS = frozenset("""
rando randonnee regate course coupe journee journees fete finale demi
competition remise rencontre rencontres ronde marche nuit traversee
foulee foulees classique boucle boucles corrida kermesse gala soiree
""".split())
MASCULINS = frozenset("""
forum tournoi championnat criterium gala meeting trophee open stage salon
challenge derby grand prix match tour critère festival concours raid
rallye trail marathon semi triathlon interclubs plateau
""".split())


def _dit_ou(ou):
    """« a la rando aviron du 18 octobre », « au forum des sports ».

    Les guillemets et la majuscule du titre font fiche administrative dans
    une phrase. Mais on ne devine pas le genre : hors liste, on garde le
    titre tel quel, sans article.
    """
    import re as _re
    premier = (ou.split(" \u00b7 ")[0] or "").strip().strip("\u00ab\u00bb\" ")
    if not premier:
        return ""
    if _re.match(r"^(?:au|aux|\u00e0|a|chez|lors)\b", premier, _re.I):
        return premier
    if _re.match(r"^(?:la|le|les|l'|l\u2019)\b", premier, _re.I):
        return "\u00e0 %s" % premier
    mot = _re.sub(r"[^a-z\u00e0-\u00ff]", "", premier.split()[0].lower())
    sans_accent = (mot.replace("\u00e9", "e").replace("\u00e8", "e")
                      .replace("\u00ea", "e").replace("\u00e0", "a"))
    bas = premier[:1].lower() + premier[1:]
    # Devant une voyelle le genre ne s'entend plus : « a l'open ».
    if sans_accent[:1] in "aeiou" and (sans_accent in FEMININS
                                       or sans_accent in MASCULINS):
        return "\u00e0 l'%s" % bas
    if sans_accent in FEMININS:
        return "\u00e0 la %s" % bas
    if sans_accent in MASCULINS:
        return "au %s" % bas
    # Inconnu : le titre reste un nom propre, et la phrase tient debout.
    return "\u00e0 %s" % premier


def _dit_quand(iso):
    d = util.parse_date(str(iso)[:10])
    if not d:
        return "récemment"
    ecart = util.days_between(d, util.today())
    if ecart is None:
        return "récemment"
    if ecart <= 0:
        return "aujourd'hui"
    if ecart == 1:
        return "hier"
    if ecart < 7:
        return "il y a %d jours" % ecart
    return "le %s" % d.strftime("%d/%m")


def trancher(rid, traite=True):
    _table()
    db.ex("UPDATE rencontres SET traite = ? WHERE id = ?",
          (1 if traite else 0, int(rid)))
    return {"ok": True}


# ---------------------------------------------------------------------------
# Le chemin qui ne depend pas d'iCloud : AirDrop
# ---------------------------------------------------------------------------
# Le carnet du Mac s'arrete quand iCloud s'arrete — et le 18 octobre 2026 il
# s'etait tu depuis deux jours, pendant que Julien enregistrait des numeros
# sur son iPhone. Attendre une synchronisation qu'on ne controle pas, pour
# des contacts qui valent du travail, ne tient pas.
#
# Sur l'iPhone : on selectionne une fiche (ou plusieurs), Partager, AirDrop
# vers le Mac. Le fichier .vcf atterrit dans Telechargements. On le ramasse.
# Trente secondes, aucun compte, aucun reglage.

TELECHARGEMENTS = os.path.join(os.path.expanduser("~"), "Downloads")
RANGES = os.path.join(RACINE, "data", "vcards-lues")


def avaler_vcards(texte, ou=None, source="AirDrop depuis l'iPhone"):
    """Lit des cartes de contact et en fait des rencontres.

    Le meme lecteur que pour les cartes partagees dans WhatsApp : il sait
    deja lire FN, N, TEL, EMAIL, ORG et TITLE.
    """
    from . import whatsapp
    _table()
    cartes = whatsapp.parse_vcards(texte or "")
    faits, deja = [], 0
    for c in cartes:
        nom = (c.get("nom") or "").strip()
        tels = c.get("telephones") or []
        mails = c.get("emails") or []
        tel = tels[0] if tels else None
        if not nom and not tel:
            continue
        if tel and db.q1("SELECT id FROM rencontres WHERE telephone = ?", (tel,)):
            deja += 1
            continue
        jour = util.today().isoformat()
        rid = db.insert("rencontres", {
            "nom": nom or tel,
            "telephone": tel,
            "email": (mails[0] if mails else None),
            "organisation": (c.get("organisation") or "").strip() or None,
            "role": (c.get("role") or c.get("titre") or "").strip() or None,
            # Ou vous etiez ce jour-la, si l'agenda le dit — sinon ce que
            # Julien a tape lui-meme en important.
            "ou": (ou or "").strip() or (" · ".join(ou_etait_il(jour)) or None),
            "source": source,
            "vu_le": jour,
            "created_at": util.now_iso(),
        })
        faits.append({"id": rid, "nom": nom or tel, "telephone": tel,
                      "autres_numeros": tels[1:3]})
    return {"lues": len(cartes), "crees": faits, "deja_connus": deja}


def ramasser_telechargements(ou=None, limite=40):
    """Les .vcf poses par AirDrop dans Telechargements.

    Chaque fichier lu est note pour ne pas etre relu : on ne deplace ni ne
    supprime rien dans le dossier de Julien, c'est le sien.
    """
    _table()
    try:
        os.makedirs(RANGES, exist_ok=True)
    except OSError:
        pass
    vus = set()
    try:
        vus = set(os.listdir(RANGES))
    except OSError:
        pass

    fichiers = []
    try:
        for nom in sorted(os.listdir(TELECHARGEMENTS)):
            if nom.lower().endswith((".vcf", ".vcard")) and nom not in vus:
                fichiers.append(nom)
    except OSError as exc:
        return {"ok": False, "error": "dossier Téléchargements illisible : %s" % exc}

    total = {"fichiers": 0, "lues": 0, "crees": [], "deja_connus": 0}
    for nom in fichiers[:limite]:
        chemin = os.path.join(TELECHARGEMENTS, nom)
        try:
            with open(chemin, encoding="utf-8", errors="replace") as f:
                texte = f.read()
        except OSError:
            continue
        r = avaler_vcards(texte, ou=ou,
                          source="AirDrop · %s" % nom[:40])
        total["fichiers"] += 1
        total["lues"] += r["lues"]
        total["crees"] += r["crees"]
        total["deja_connus"] += r["deja_connus"]
        # On note le fichier comme lu, sans y toucher.
        try:
            with open(os.path.join(RANGES, nom), "w") as f:
                f.write(util.now_iso())
        except OSError:
            pass
    total["ok"] = True
    total["en_attente"] = max(0, len(fichiers) - limite)
    return total


# -- Le veilleur -------------------------------------------------------------
#
# Julien ne doit rien cliquer. Toutes les trois minutes, en fond :
# on relit les cinq bases du carnet, on ramasse les cartes deposees par
# AirDrop dans Telechargements, et tout nouveau venu devient une fiche.
#
# Rien n'est envoye a personne. On range, c'est tout.

INTERVALLE = 180
_dernier = {"le": None, "trouves": 0, "erreur": None}


def dernier_passage():
    return dict(_dernier)


def un_tour():
    """Un passage du veilleur. Rend ce qu'il a trouve, sans jamais lever."""
    trouves, souci = 0, None

    try:
        d = relever()
        if not d["premier_releve"] and not d.get("repose") and d["nouveaux"]:
            trouves += enregistrer_rencontres(d["nouveaux"])
    except CarnetIndisponible as exc:
        souci = str(exc)
    except Exception as exc:
        souci = "carnet : %s" % exc

    try:
        r = ramasser_telechargements()
        if r.get("ok"):
            trouves += len(r.get("crees") or [])
        elif not souci:
            souci = r.get("error")
    except Exception as exc:
        if not souci:
            souci = "Téléchargements : %s" % exc

    _dernier.update({"le": util.now_iso(), "trouves": trouves,
                     "erreur": souci})
    return {"trouves": trouves, "erreur": souci}


def veiller(arret=None):
    """Lance le veilleur en fond. Un seul, et il ne meurt pas d'une erreur."""
    import threading

    def tourner():
        # On laisse le serveur finir de demarrer avant le premier passage.
        premier = True
        while True:
            if arret is not None and arret.is_set():
                return
            try:
                un_tour()
            except Exception:
                pass
            if premier:
                premier = False
            if arret is not None:
                if arret.wait(INTERVALLE):
                    return
            else:
                import time
                time.sleep(INTERVALLE)

    fil = threading.Thread(target=tourner, name="carnet-veilleur", daemon=True)
    fil.start()
    return fil


def noter_sujet(rid, sujet):
    """Ce dont on a parle avec cette personne.

    C'est la pièce qui manquait : sans elle, le CRM renvoyait sa
    presentation a quelqu'un qui venait de l'entendre de vive voix.
    """
    _table()
    sujet = (sujet or "").strip()[:400]
    if not db.q1("SELECT id FROM rencontres WHERE id = ?", (int(rid),)):
        return {"ok": False, "error": "fiche introuvable"}
    db.ex("UPDATE rencontres SET sujet = ? WHERE id = ?",
          (sujet or None, int(rid)))
    r = db.q1("SELECT * FROM rencontres WHERE id = ?", (int(rid),))
    return {"ok": True, "sujet": sujet, "mot": mot_pour(dict(r))}


def derniere_arrivee():
    """La date du dernier contact VRAIMENT recu du telephone.

    Les 5 162 fiches datees du 2 juillet 2026 sont l'import initial : elles
    ne disent rien. Ce qui compte, c'est ce qui est arrive apres.
    """
    try:
        gens = lire_bases()
    except Exception:
        return None
    jours = sorted({(g.get("cree") or "")[:10] for g in gens if g.get("cree")},
                   reverse=True)
    for j in jours:
        if j and j > "2026-07-02":
            return j
    return jours[0] if jours else None
