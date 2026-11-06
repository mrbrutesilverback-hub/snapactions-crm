# -*- coding: utf-8 -*-
"""Reformuler un message deja ecrit — sur ce Mac, et nulle part ailleurs.

Le modele tourne en local, dans Ollama. Trois consequences qui comptent :
rien ne sort de la machine, rien n'est facture, et ca marche sans internet.
Un brouillon de prospection contient le nom d'un club, parfois celui d'une
personne — autant que ca reste ici.

Le CRM n'envoie jamais rien tout seul : cette regle ne bouge pas. Ce module
prend le texte qui est dans la case, le rend autrement, et le repose dans la
case. L'envoi reste un geste de Julien, apres relecture, dans WhatsApp.
"""
import json
import re
import urllib.error
import urllib.request

from . import db

URL = "http://127.0.0.1:11434"
MODELE_DEFAUT = "qwen2.5:7b"
DELAI = 120            # un modele local demarre lentement la premiere fois

# Le garde-fou qui compte. Un modele qui « ameliore » un message commercial
# invente volontiers une date, un prix ou une reference — et c'est exactement
# ce qu'on ne peut pas se permettre d'envoyer a un club.
CONSIGNE_MAISON = """Tu reformules un message de prospection ecrit par un photographe sportif independant qui demarche des clubs de sport dans le sud de la France. Il l'enverra lui-meme par WhatsApp ou par mail, apres t'avoir relu.

Regles absolues :
- N'invente RIEN. Aucune date, aucun prix, aucun chiffre, aucun nom, aucun numero, aucune promesse qui ne soit pas deja dans le texte d'origine. Si une information manque, elle doit continuer a manquer.
- NE CHANGE PAS LE SUJET. Si le message propose quelque chose, il propose toujours la meme chose apres reformulation. N'ecris jamais que des photos existent deja, qu'une date est confirmee ou qu'un accord est pris si le texte d'origine ne le dit pas : ce serait un mensonge envoye a un client.
- Garde tous les faits du texte d'origine : les dates, les noms de clubs et de personnes, les montants, les conditions.
- Garde la signature telle quelle si elle est presente.
- Vouvoiement, francais naturel, ton d'un artisan qui connait son metier : ni jargon commercial, ni superlatifs, ni emojis sauf s'il y en avait deja.
- Longueur : un message qu'on lit sur un telephone. Des paragraphes courts.

Tu reponds UNIQUEMENT par le message reformule, en francais. Pas d'introduction, pas de commentaire, pas de guillemets autour."""


class PasLa(Exception):
    """Ollama ne repond pas, ou le modele n'est pas installe."""


class Refus(Exception):
    """L'appel n'a pas abouti."""


def _adresse():
    return (db.get_setting("ollama_url", "") or URL).rstrip("/")


def modele():
    return (db.get_setting("ia_modele", "") or "").strip() or MODELE_DEFAUT


def _appel(chemin, corps=None, delai=DELAI):
    req = urllib.request.Request(
        _adresse() + chemin,
        data=json.dumps(corps).encode("utf-8") if corps is not None else None,
        method="POST" if corps is not None else "GET",
        headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=delai) as r:
        return json.loads(r.read().decode("utf-8"))


def etat():
    """Ce que l'ecran a besoin de savoir pour ne pas proposer un bouton mort."""
    try:
        d = _appel("/api/tags", delai=4)
    except Exception:
        return {"disponible": False, "modele": modele(), "modeles": [],
                "dit": "Ollama ne repond pas sur cette machine."}
    noms = [m.get("name", "") for m in (d.get("models") or [])]
    voulu = modele()
    pret = any(n == voulu or n.startswith(voulu.split(":")[0] + ":") for n in noms)
    return {"disponible": pret, "modele": voulu, "modeles": noms,
            "dit": None if pret else "Le modele %s n'est pas installe." % voulu}


def disponible():
    return etat()["disponible"]


def reformuler(texte, consigne, contexte=None):
    """Rend le message autrement. Leve PasLa ou Refus si ca n'aboutit pas."""
    texte = (texte or "").strip()
    if not texte:
        raise Refus("il n'y a rien a reformuler")
    consigne = (consigne or "").strip() or "Reformule ce message sans en changer le sens."

    demande = "Consigne : %s\n\n" % consigne
    if contexte:
        demande += ("Contexte, uniquement pour ajuster le ton. Ne recopie NI le nom "
                    "du club NI la ville dans le message : %s\n\n" % contexte)
    demande += "Message a reformuler :\n---\n%s\n---" % texte

    try:
        rep = _appel("/api/chat", {
            "model": modele(),
            "stream": False,
            # Un modele local de sept milliards de parametres derive vite : a 0,4 il
            # a transforme « avez-vous jete un oeil a notre proposition » en
            # « avez-vous vu les photos », ce qui ferait croire au club que le
            # tournoi est deja couvert. Au plus bas, il reformule sans broder.
            "options": {"temperature": 0.15, "top_p": 0.9},
            "messages": [
                {"role": "system", "content": CONSIGNE_MAISON},
                {"role": "user", "content": demande},
            ],
        })
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("error", "")
        except Exception:
            pass
        if exc.code == 404:
            raise PasLa("modele %s introuvable dans Ollama (%s)" % (modele(), detail))
        raise Refus("Ollama a repondu %s%s" % (exc.code, " : " + detail if detail else ""))
    except urllib.error.URLError as exc:
        raise PasLa("Ollama ne repond pas : %s. Ouvrez l'application Ollama." % exc.reason)
    except Exception as exc:
        raise Refus(str(exc))

    sortie = ((rep.get("message") or {}).get("content") or "").strip()
    if not sortie:
        raise Refus("reponse vide")
    return {"texte": sortie, "modele": rep.get("model") or modele()}


# ---------------------------------------------------------------------------
# Lire du texte sale et en sortir de la structure
# ---------------------------------------------------------------------------
# C'est la seule chose qu'on demande au modele. Jamais un fait qu'il ne peut
# pas lire : ni un numero de telephone, ni un mail, ni une date qu'il
# devinerait. Ces trois-la restent extraits par util, qui a des garde-fous.
#
# Tout passe par un JSON strict : si la reponse n'est pas du JSON, on jette.
# Un modele local qui part en digression ne doit pas pouvoir ecrire en base.
RIEN = object()


def _json(consigne, demande, delai=None):
    """Interroge le modele et rend un dict. None si la reponse est inutilisable."""
    try:
        rep = _appel("/api/chat", {
            "model": modele(),
            "stream": False,
            "format": "json",
            "options": {"temperature": 0, "top_p": 0.9},
            "messages": [
                {"role": "system", "content": consigne},
                {"role": "user", "content": demande},
            ],
        }, delai or DELAI)
    except Exception:
        return None
    brut = ((rep.get("message") or {}).get("content") or "").strip()
    try:
        d = json.loads(brut)
    except ValueError:
        return None
    return d if isinstance(d, dict) else None


def _mot(d, cle, maxi=120):
    v = (d or {}).get(cle)
    if v is None or isinstance(v, (dict, list)):
        return None
    v = str(v).strip()
    if not v or v.lower() in ("null", "none", "inconnu", "n/a", "non precise",
                              "non précisé", "aucun", "?"):
        return None
    return v[:maxi]


def _entier(d, cle, maxi=2000):
    v = (d or {}).get(cle)
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if 0 < n <= maxi else None


CONSIGNE_FLYER = """Tu lis le texte brut d'une affiche de tournoi sportif ou d'une plaquette de club, en France. Tu rends UNIQUEMENT un objet JSON.

Champs : nom (le nom de l'evenement), organisateur (le club ou l'association qui organise, SANS la ville), sport, categories (les categories d'age, ex "U10, U11"), nb_equipes (un entier), ville, date_debut (AAAA-MM-JJ), date_fin (AAAA-MM-JJ).

Regles :
- Ne mets un champ que si le texte le dit. Sinon : null. Ne devine jamais.
- nb_equipes est le nombre d'EQUIPES, pas de joueurs ni de participants. Si le texte dit "60/240 joueurs", c'est null.
- organisateur n'est pas le nom du tournoi. "FBD Cup de la Fondation Bernard Dias - Grasse" a pour organisateur "Fondation Bernard Dias" et pour ville "Grasse".
- Ne rends jamais de telephone ni de mail : ils sont lus ailleurs."""


def lire_flyer(texte):
    """Ce qu'une affiche annonce, en champs. Rien d'invente."""
    d = _json(CONSIGNE_FLYER, (texte or "")[:6000])
    if not d:
        return None
    return {
        "name": _mot(d, "nom", 200),
        "organizer": _mot(d, "organisateur"),
        "sport": _mot(d, "sport", 60),
        "categories": _mot(d, "categories", 160),
        "nb_equipes": _entier(d, "nb_equipes", 300),
        "city": _mot(d, "ville", 80),
        "start_date": _mot(d, "date_debut", 10),
        "end_date": _mot(d, "date_fin", 10),
    }


CONSIGNE_ORGANISATEUR = """On te donne le nom d'un evenement sportif francais. Tu rends UNIQUEMENT un objet JSON avec deux champs : organisateur et ville.

- organisateur : le club, l'association ou la structure qui organise. JAMAIS le nom de l'evenement, jamais une categorie d'age, jamais une ville seule.
- ville : la commune, si le nom la contient. Sinon null.
- Si le nom ne permet pas d'identifier un organisateur, mets null. Ne devine pas.

Exemples :
"Tournoi de la Toussaint 2026 U10 & U11 de l'US Val d'Issole - Gareoult" -> {"organisateur": "US Val d'Issole", "ville": "Gareoult"}
"Challenge du Coq 2026 U10 & U11 du SC Cogolin" -> {"organisateur": "SC Cogolin", "ville": null}
"Trail des Maures" -> {"organisateur": null, "ville": null}"""


def organisateur(nom):
    """Qui organise, et ou — depuis le seul nom de l'evenement."""
    d = _json(CONSIGNE_ORGANISATEUR, (nom or "")[:300])
    if not d:
        return None
    return {"organisateur": _mot(d, "organisateur"), "ville": _mot(d, "ville", 80)}


CONSIGNE_RESUME = """On te donne les derniers messages echanges avec un club, dans l'ordre. Tu rends UNIQUEMENT un objet JSON avec un champ "resume".

"resume" : UNE phrase de quinze mots maximum qui dit ou en est le dossier et ce qu'on attend. Pas de politesse, pas de reformulation du dernier message : l'etat du dossier.

Exemples :
"Accord de principe, ils attendent leurs dates de ligue."
"Ils ont choisi un autre photographe."
"Sans reponse depuis la plaquette envoyee."
"Ils demandent le tarif."

N'invente aucun fait. Si les messages ne disent rien d'exploitable, mets null."""


def resume_fil(messages):
    """Une ligne qui dit ou en est le dossier."""
    bouts = []
    for m in (messages or [])[-6:]:
        qui = "Vous" if m.get("de_moi") else "Eux"
        texte = (m.get("texte") or "").strip()
        if texte:
            bouts.append("%s : %s" % (qui, texte[:600]))
    if not bouts:
        return None
    d = _json(CONSIGNE_RESUME, "\n".join(bouts))
    return _mot(d, "resume", 160) if d else None


CONSIGNE_REPONSE = """Tu ecris la reponse WhatsApp de Julien, photographe sportif independant (SnapActions, Cannes), a un organisateur de tournoi ou un club qui vient de lui ecrire. On te donne le contexte et la conversation, dans l'ordre. Tu rends UNIQUEMENT un objet JSON : {"corps": "..."} — le corps du message seulement : PAS de "Bonjour", PAS de formule de fin, PAS de signature, ils sont ajoutes ensuite.

Ce que Julien propose, et RIEN d'autre : il vient photographier l'evenement ; c'est gratuit pour le club ; ce sont les familles qui achetent les photos si elles le veulent ; le club recoit une selection de photos pour sa communication.

Regles absolues :
- Reponds a CE QU'ILS ONT DIT dans leur DERNIER message, en tenant compte de toute la conversation. S'ils posent une question, reponds-y. S'ils annoncent une date ou une periode, reprends-la telle quelle et dis que tu la notes. S'ils disent qu'ils reviendront vers lui, remercie et dis que tu attends leur retour. S'ils refusent, remercie poliment sans insister.
- N'invente RIEN : aucune date, aucun jour, aucune heure, aucun prix, aucun chiffre, aucun nom, aucune promesse absente de la conversation ou du contexte. Ne pretends jamais qu'une date est confirmee si elle ne l'est pas.
- Ne prends AUCUN engagement que Julien n'a pas deja pris lui-meme dans la conversation. S'il a ecrit "a voir" ou "peut-etre", ca reste "a voir" : n'ecris pas "je note", "je viendrai" ou "je bloque" pour cet evenement-la.
- Ne repete pas l'offre s'ils la connaissent deja et qu'elle n'apporte rien a la reponse.
- Pas de "[a completer]", pas de crochets, pas de placeholder.
- Vouvoiement, SAUF si Julien tutoie deja la personne dans la conversation : alors tutoiement. Francais simple et naturel, ton chaleureux d'un artisan, pas commercial. Pas d'emoji. Ne repete pas le prenom.
- Court : 2 ou 3 phrases, comme un vrai message WhatsApp.

Exemple. Conversation :
Eux : On fait un tournoi seniors en mai, je ne sais pas si ca vous interesse.
Julien : Pourquoi pas, a voir selon le planning. Et pour les jeunes ?
Eux : Pour les jeunes ce serait plutot en juin, je vous redis.
-> {"corps": "Merci, juin pour les jeunes, c'est parfait. Je reste disponible : dites-moi la date des que vous l'avez et je la bloque."}"""


def repondre(messages, contexte=None):
    """Le CORPS de la reponse au dernier message recu, d'apres tout le fil.

    La salutation et la signature ne sont pas laissees au modele : il les
    collait au texte ou les inventait. L'appelant les pose. None si rien
    d'utilisable.
    """
    lignes = []
    for m in (messages or [])[-8:]:
        texte = (m.get("texte") or "").strip()
        if texte:
            qui = "Julien" if m.get("de_moi") else "Eux"
            quand = (" (%s)" % m["quand"][:10]) if m.get("quand") else ""
            lignes.append("%s%s : %s" % (qui, quand, texte[:900]))
    if not lignes or lignes[-1].startswith("Julien"):
        return None                       # rien a quoi repondre
    demande = ""
    if contexte:
        demande += "Contexte :\n%s\n\n" % contexte
    demande += "Conversation :\n" + "\n".join(lignes)
    # Le modele se trompe de date d'une annee sur l'autre : on lui donne la sienne.
    demande += "\n\nAujourd'hui : %s" % __import__("datetime").date.today().isoformat()
    # Le garde-fou qui ne depend pas du modele : une date citee dans la
    # reponse doit figurer dans LEUR dernier message. Sinon le modele « note »
    # une date que Julien n'a jamais acceptee (vu : « je note le 25 octobre »
    # alors que Julien avait ecrit « a voir »). Un essai corrige, puis on jette.
    recu = []
    for m in reversed(messages or []):
        if m.get("de_moi"):
            break
        recu.append(m.get("texte") or "")
    recu = _plat(" ".join(recu))
    texte = None
    for _ in range(2):
        d = _json(CONSIGNE_REPONSE, demande)
        texte = _mot(d, "corps", 1200) if d else None
        if not texte or "[" in texte:
            return None
        etrangeres = [x for x in DATE_CITEE.findall(_plat(texte)) if x not in recu]
        if not etrangeres:
            break
        demande += ("\n\nATTENTION : ta reponse precedente parlait de « %s », qui n'est "
                    "PAS dans leur dernier message. Julien ne s'y est pas engage. "
                    "Recommence sans en parler." % ", ".join(etrangeres))
        texte = None
    if not texte:
        return None
    # Ceinture et bretelles : s'il a quand meme salue ou signe, on retire.
    texte = re.sub(r"^\s*(bonjour|bonsoir|salut)\b[^,\n]*,?\s*", "", texte, flags=re.I)
    texte = re.sub(r"\s*(bonne (journ[ée]e|soir[ée]e)|cordialement)[\s\S]*$", "", texte,
                   flags=re.I)
    texte = re.sub(r"\s*julien\s*[—-]?\s*snapactions\.?\s*$", "", texte, flags=re.I)
    # « Bonjour Mellie, / Merci Mellie, ... » : le prenom est deja en tete.
    texte = re.sub(r"^(merci|d'accord|parfait|super|pas de souci)\s+[A-ZÉ][\w-]+\s*([,!.])",
                   r"\1\2", texte.strip(), flags=re.I)
    texte = texte.strip()
    return (texte[:1].upper() + texte[1:]) if texte else None


MOIS = ("janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|"
        "novembre|decembre")
DATE_CITEE = re.compile(r"\b(\d{1,2}(?:er)?\s+(?:%s))\b" % MOIS)


def _plat(texte):
    import unicodedata
    t = unicodedata.normalize("NFD", texte or "")
    return "".join(c for c in t if unicodedata.category(c) != "Mn").lower()


CONSIGNE_PHOTO = """Un photographe sportif independant cherche des evenements ou il pourra VENDRE des photos aux gens presents. Tu rends UNIQUEMENT un objet JSON : {"vend": true/false, "pourquoi": "..."}.

vend = true seulement si l'une de ces trois conditions est remplie :
1. des enfants ou des amateurs se produisent devant leurs familles (gala, kermesse, remise de prix, carnaval, spectacle d'ecole) ;
2. une competition ou un concours ou chaque participant paye son engagement (tournoi, championnat, concours canin ou hippique, course, trail) ;
3. un rassemblement de proprietaires qui photographient leur bien (autos, motos, bateaux, chiens).

vend = false pour tout le reste : concert, theatre professionnel, conference, projection, marche, brocante, vernissage, exposition, visite guidee, loto, repas, reunion. Le public assis n'achete pas de photo de lui-meme.

"pourquoi" : cinq mots maximum."""


def vend_des_photos(nom, description=None):
    """Deuxieme avis sur un evenement que les mots-cles n'ont pas su classer."""
    texte = (nom or "").strip()
    if description:
        texte += "\n\n" + str(description)[:1200]
    d = _json(CONSIGNE_PHOTO, texte[:1500])
    if not d or not isinstance(d.get("vend"), bool):
        return None
    return {"vend": d["vend"], "pourquoi": _mot(d, "pourquoi", 60)}


CONSIGNE_DEMANDE = """Tu lis une phrase tapee par un photographe sportif dans son CRM, et tu rends UNIQUEMENT un objet JSON : {"intention": "...", "mots": ["...", "..."]}.

"intention" vaut :
- "relance" : il veut relancer un club a qui il a deja ecrit ("relance", "relancer", "revenir vers", "sans reponse")
- "studio" : il veut proposer une seance photo studio ("studio", "shooting", "portraits", "seance")
- "dates" : il veut demander le calendrier de la saison ("dates", "calendrier", "saison", "planning")
- "recherche" : il cherche seulement a retrouver quelque chose, sans ecrire

"mots" : les mots qui servent a RETROUVER le club ou la personne — un sport, une ville, un quartier, un nom de club, un prenom. Rends-les en minuscules, sans accent, un par entree.

Ne garde ni les verbes, ni "pour", "avec", "le", "la", "les", "au", "a", "du", "de".
N'invente aucun mot qui ne soit pas dans la phrase.

Exemples :
"relance pour le foot au suquet avec marcel" -> {"intention": "relance", "mots": ["foot", "suquet", "marcel"]}
"studio cannes escrime" -> {"intention": "studio", "mots": ["cannes", "escrime"]}
"je cherche le club d aviron de mandelieu" -> {"intention": "recherche", "mots": ["aviron", "mandelieu"]}
"demande les dates au basket de nice" -> {"intention": "dates", "mots": ["basket", "nice"]}"""

INTENTIONS = {"relance": "relance_1", "studio": "studio_club",
              "dates": "calendrier_mail", "recherche": None}


def comprendre(phrase):
    """Ce que la phrase demande : une intention, et des mots pour retrouver.

    Le modele ne cherche rien et ne decide de rien : il traduit une phrase en
    intention + mots-cles. C'est la base, ensuite, qui trouve les fiches — un
    modele n'a pas le droit d'inventer un club qui n'existe pas.
    """
    d = _json(CONSIGNE_DEMANDE, (phrase or "")[:400], delai=40)
    if not d:
        return None
    intention = (_mot(d, "intention", 20) or "recherche").lower()
    if intention not in INTENTIONS:
        intention = "recherche"
    mots = []
    for m in (d.get("mots") or [])[:8]:
        m = str(m).strip().lower()
        if m and len(m) > 1 and m not in mots:
            mots.append(m[:40])
    return {"intention": intention, "modele": INTENTIONS[intention], "mots": mots}


CONSIGNE_ORGA_WEB = """On te donne le nom d'un evenement sportif, sa ville, et les resultats d'une recherche sur le web (titres et extraits). Tu rends UNIQUEMENT un objet JSON : {"organisateur": "...", "pourquoi": "..."}.

- "organisateur" : le nom du club, de l'association ou de la structure qui ORGANISE l'evenement, tel qu'il apparait dans les resultats. Jamais le nom de l'evenement, jamais un annuaire, jamais un site de billetterie.
- Si les resultats ne le disent pas, mets null. Ne devine pas, n'invente pas de nom plausible.
- "pourquoi" : ou tu l'as lu, en cinq mots.

Ne rends aucun numero de telephone ni aucun mail : ils sont lus ailleurs."""


def organisateur_depuis_web(nom, ville, resultats):
    """Qui organise, d'apres ce que le moteur a rendu."""
    demande = ("Evenement : %s\nVille : %s\n\nResultats de recherche :\n%s"
               % (nom or "", ville or "", (resultats or "")[:4000]))
    d = _json(CONSIGNE_ORGA_WEB, demande, delai=60)
    if not d:
        return None
    return {"organisateur": _mot(d, "organisateur"),
            "pourquoi": _mot(d, "pourquoi", 60)}


CONSIGNE_FICHE = """On te donne une phrase dictee par Julien, photographe \
sportif, qui veut ajouter quelqu'un dans son fichier. Tu rends UNIQUEMENT un \
objet JSON :
{"nom": "...", "ville": "...", "sport": "...", "personne": "...", \
"role": "...", "categorie": "partenaire|relais|club"}

- "nom" : le nom de la structure (club, comite, media, office de tourisme). \
Ecris-le proprement, en minuscules accentuees avec les majuscules d'usage : \
« ERIC AVIRON villefranche » donne « Aviron Villefranche ».
- "personne" : le prenom ou nom de la personne citee, si la phrase en donne \
un (« ERIC » -> « Eric »). null sinon.
- "role" : son role s'il est dit (president, coach, directrice). null sinon.
- "ville" : la commune, sans le departement. null si absente.
- "sport" : le sport si la phrase le dit ou si le nom le contient \
(« aviron »). null sinon.
- "categorie" : "partenaire" si la phrase dit partenaire ; "relais" si elle \
dit relais, media, presse, office de tourisme, groupe ; sinon "club".

N'invente RIEN. Pas de numero de telephone, pas de mail, pas d'adresse : \
ils sont lus ailleurs. Si une information n'est pas dans la phrase, mets \
null plutot que de la deviner."""


def fiche_depuis_phrase(phrase):
    """Traduit « ajoute ERIC AVIRON villefranche en partenaire » en champs.

    Le modele ne lit que des mots : il ne rend ni numero ni mail, qui sont
    extraits du texte brut par util, avec ses garde-fous. None si Ollama
    dort ou si la reponse est inutilisable.
    """
    d = _json(CONSIGNE_FICHE, (phrase or "")[:400], delai=45)
    if not d:
        return None
    cat = (_mot(d, "categorie", 20) or "club").lower()
    if cat not in ("partenaire", "relais", "club"):
        cat = "club"
    out = {
        "nom": _mot(d, "nom", 120),
        "ville": _mot(d, "ville", 80),
        "sport": _mot(d, "sport", 60),
        "personne": _mot(d, "personne", 80),
        "role": _mot(d, "role", 60),
        "categorie": cat,
    }
    return out if out["nom"] else None


CONSIGNE_RELANCE = """Julien, photographe sportif (SnapActions, region de \
Cannes), a ecrit a un club et n'a pas eu de reponse. Il veut relancer sur \
WhatsApp.

Tu rends UNIQUEMENT un objet JSON : {"corps": "..."}.

Regles :
- TROIS PHRASES MAXIMUM ;
- vouvoiement, francais parle, phrases courtes ;
- reprendre LE SUJET PRECIS de sa derniere question s'il y en a une (des \
dates de tournoi, une autorisation, un creneau) : c'est tout l'interet de \
relancer ;
- s'il a deja relance plusieurs fois, changer d'angle et offrir quelque \
chose de CONCRET (un creneau, une date, un exemple) ;
- finir sur une proposition, jamais sur une porte de sortie ;
- INTERDIT de culpabiliser, de reprocher le silence, d'ecrire « je me \
permets de revenir vers vous », ou de proposer au club de dire non \
(« si ca ne vous interesse pas », « je ne reviendrai pas vous deranger », \
« derniere relance ») : ces phrases invitent le refus ;
- INTERDIT d'inventer une date, un prix, un nom ou un evenement : n'utilise \
que ce qui est ecrit dans la conversation ;
- ne mets NI salutation d'ouverture NI signature : on les ajoute nous-memes ;
- INTERDIT : « cordialement », « dans l'attente », « sauf erreur de ma \
part », « relance »."""


def relance_du_fil(messages, contexte=None):
    """Une relance ecrite d'apres la conversation, pas d'apres un modele.

    Contrairement a repondre(), on s'attend a ce que le dernier mot soit
    celui de Julien : c'est precisement pour cela qu'on relance. None si
    le modele dort ou rend quelque chose d'inutilisable.
    """
    lignes = []
    for m in (messages or [])[-8:]:
        texte = (m.get("texte") or "").strip()
        if texte:
            qui = "Julien" if m.get("de_moi") else "Eux"
            quand = (" (%s)" % m["quand"][:10]) if m.get("quand") else ""
            lignes.append("%s%s : %s" % (qui, quand, texte[:700]))
    if not lignes:
        return None
    demande = ""
    if contexte:
        demande += "Contexte :\n%s\n\n" % contexte
    demande += "Conversation :\n" + "\n".join(lignes)
    demande += "\n\nAujourd'hui : %s" % __import__("datetime").date.today().isoformat()

    # Le meme garde-fou que pour les reponses : une date citee doit figurer
    # dans la conversation. Sinon le modele « confirme » un jour que
    # personne n'a propose.
    deja = _plat(" ".join(m.get("texte") or "" for m in (messages or [])))
    texte = None
    for _ in range(2):
        d = _json(CONSIGNE_RELANCE, demande)
        texte = _mot(d, "corps", 900) if d else None
        if not texte or "[" in texte:
            return None
        etrangeres = [x for x in DATE_CITEE.findall(_plat(texte)) if x not in deja]
        if not etrangeres:
            break
        demande += ("\n\nATTENTION : ta reponse parlait de « %s », qui n'est PAS "
                    "dans la conversation. Recommence sans cette date."
                    % ", ".join(etrangeres))
        texte = None
    if not texte:
        return None
    texte = re.sub(r"^\s*(bonjour|bonsoir|salut)\b[^,\n]*,?\s*", "", texte, flags=re.I)
    texte = re.sub(r"\s*(bonne (journ[ée]e|soir[ée]e)|cordialement)[\s\S]*$", "",
                   texte, flags=re.I)
    texte = re.sub(r"\s*julien\s*[—-]?\s*snapactions\.?\s*$", "", texte, flags=re.I)
    texte = texte.strip()
    return (texte[:1].upper() + texte[1:]) if texte else None
