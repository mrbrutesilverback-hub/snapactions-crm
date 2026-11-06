# -*- coding: utf-8 -*-
"""Envoyer vraiment, en un clic.

Jusqu'ici le CRM n'envoyait rien : il preparait le texte, ouvrait WhatsApp
ou le logiciel de mail, et Julien appuyait. C'etait voulu — un agent qui
envoie seul a un club peut faire beaucoup de degats.

Ce module ne change pas ce principe : rien ne part sans un clic de Julien,
sur un texte qu'il a sous les yeux. Il enleve seulement le dernier
aller-retour pour le mail.

Deux choses a savoir :

  - l'envoi passe par Mail.app, avec le compte deja configure. Aucun mot de
    passe n'est demande, stocke ni lu par le CRM ;
  - WhatsApp n'a pas d'equivalent. Il n'existe aucun moyen d'envoyer un
    message WhatsApp par programme sans l'API Business payante. Le bouton
    « Ouvrir dans WhatsApp » reste donc ce qui s'en approche le plus : la
    conversation s'ouvre avec le texte deja ecrit, il reste a appuyer.
"""

import json
import subprocess

from . import db, util

DELAI = 45

_SCRIPT = r"""
function run(argv) {
  const d = JSON.parse(argv[0]);
  const Mail = Application("Mail");
  const compte = d.depuis || "";
  const msg = Mail.OutgoingMessage().make();
  msg.subject = d.objet;
  msg.content = d.corps;
  if (compte) {
    for (const a of Mail.accounts()) {
      if (a.emailAddresses().indexOf(compte) >= 0) { msg.sender = compte; break; }
    }
  }
  msg.toRecipients.push(Mail.Recipient({address: d.a}));
  msg.send();
  return "envoye";
}
"""


class Refus(Exception):
    """L'envoi n'a pas eu lieu, et on dit pourquoi."""


def par_mail(destinataire, objet, corps, depuis=None):
    """Envoie par Mail.app. Leve Refus si quelque chose cloche."""
    a = (destinataire or "").strip()
    if not a or "@" not in a:
        raise Refus("il faut une adresse mail valide")
    corps = (corps or "").strip()
    if not corps:
        raise Refus("le message est vide")
    objet = (objet or "").strip() or "SnapActions"

    charge = json.dumps({"a": a, "objet": objet, "corps": corps,
                         "depuis": (depuis or db.get_setting("my_email", "") or "")})
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _SCRIPT, charge],
            capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise Refus("Mail n'a pas repondu en %d s" % DELAI)
    except OSError as exc:
        raise Refus("osascript indisponible : %s" % exc)
    if r.returncode != 0:
        detail = [l for l in (r.stderr or "").splitlines() if l.strip()]
        raise Refus(detail[-1] if detail else "Mail a refuse l'envoi")
    return {"ok": True, "a": a, "objet": objet}


def noter_envoi(club_id, canal, destinataire, objet, corps, draft_id=None):
    """Garde la trace : un message envoye doit se retrouver."""
    quand = util.now_iso()
    try:
        db.insert("interactions", {
            "club_id": int(club_id) if club_id else None,
            "kind": "email" if canal == "mail" else canal,
            "direction": "out",
            "subject": (objet or "")[:200],
            "body": (corps or "")[:4000],
            "occurred_at": quand[:10],
            "created_at": quand,
        })
    except Exception:
        pass
    if club_id:
        try:
            db.ex("UPDATE clubs SET last_contact_at = ?, updated_at = ? WHERE id = ?",
                  (quand, quand, int(club_id)))
        except Exception:
            pass
    if draft_id:
        try:
            db.update("drafts", int(draft_id),
                      {"status": "sent", "sent_at": quand, "updated_at": quand})
        except Exception:
            pass
    quoi = "WhatsApp" if canal == "whatsapp" else "Mail"
    db.log("envoi", "%s envoyé à %s%s" % (quoi, destinataire,
           (" (%s)" % objet[:50]) if objet else ""))


# -- WhatsApp ---------------------------------------------------------------
#
# Il n'existe aucune API gratuite pour envoyer un message WhatsApp. Ce qui
# marche, c'est d'ouvrir la conversation avec le texte deja ecrit, puis de
# laisser le clavier appuyer sur Entree a la place de Julien.
#
# C'est un envoi reel, et il ne se rattrape pas. Deux verrous avant la
# touche : WhatsApp doit etre au premier plan, et on laisse la conversation
# s'ouvrir. Si l'application n'est pas devant, on n'appuie sur rien et on le
# dit — mieux vaut un envoi manque qu'un message parti au mauvais contact.

ATTENTE_WA = 4.0

_WA = r"""
function run(argv) {
  const d = JSON.parse(argv[0]);
  const App = Application.currentApplication();
  App.includeStandardAdditions = true;

  // Ouvre la conversation avec le texte deja dedans.
  App.doShellScript("open " + JSON.stringify(d.url));
  delay(d.attente);

  const SE = Application("System Events");
  const devant = SE.applicationProcesses.whose({frontmost: true})();
  const nom = devant.length ? devant[0].name() : "";
  if (nom !== "WhatsApp") {
    return JSON.stringify({ok: false, devant: nom});
  }
  delay(0.6);
  SE.keyCode(36);            // Entree
  return JSON.stringify({ok: true, devant: nom});
}
"""


# -- Les groupes -------------------------------------------------------------
#
# Un groupe WhatsApp n'a pas de numero : « whatsapp://send?phone= » ne sait
# donc pas l'ouvrir. Le seul chemin est la recherche de l'application. On
# ouvre le groupe, on colle le texte dedans — et on s'arrete la. Dans un
# groupe, un message part devant tout le monde et ne se reprend pas : c'est
# Julien qui appuie sur Entree, apres avoir lu ce qu'il envoie et a qui.

_WA_GROUPE = r"""
function run(argv) {
  const d = JSON.parse(argv[0]);
  const App = Application.currentApplication();
  App.includeStandardAdditions = true;
  const SE = Application("System Events");

  function devant() {
    const l = SE.applicationProcesses.whose({frontmost: true})();
    return l.length ? l[0].name() : "";
  }

  App.doShellScript("open whatsapp://");
  delay(d.attente);
  if (devant() !== "WhatsApp") return JSON.stringify({ok: false, etape: "ouverture", devant: devant()});

  // La recherche de WhatsApp, puis le nom du groupe.
  SE.keystroke("f", {using: ["command down"]});
  delay(0.7);
  if (devant() !== "WhatsApp") return JSON.stringify({ok: false, etape: "recherche", devant: devant()});
  SE.keystroke(d.groupe);
  delay(1.6);
  SE.keyCode(36);            // ouvre la conversation trouvee
  delay(1.2);
  if (devant() !== "WhatsApp") return JSON.stringify({ok: false, etape: "ouverture du groupe", devant: devant()});

  // Le texte est deja dans le presse-papiers : on le colle, on n'envoie pas.
  SE.keystroke("v", {using: ["command down"]});
  delay(0.4);
  return JSON.stringify({ok: true, devant: devant()});
}
"""


# Ouvrir le groupe SANS rien coller. Pour l'evenement WhatsApp, le texte ne
# va pas dans la zone de message mais dans le formulaire « Event » : coller
# ici mettrait le nom de l'evenement dans le fil du groupe.
_WA_OUVRIR = r"""
function run(argv) {
  const d = JSON.parse(argv[0]);
  const App = Application.currentApplication();
  App.includeStandardAdditions = true;
  const SE = Application("System Events");

  function devant() {
    const l = SE.applicationProcesses.whose({frontmost: true})();
    return l.length ? l[0].name() : "";
  }

  App.doShellScript("open whatsapp://");
  delay(d.attente);
  if (devant() !== "WhatsApp") return JSON.stringify({ok: false, etape: "ouverture", devant: devant()});

  SE.keystroke("f", {using: ["command down"]});
  delay(0.7);
  if (devant() !== "WhatsApp") return JSON.stringify({ok: false, etape: "recherche", devant: devant()});
  SE.keystroke(d.groupe);
  delay(1.6);
  SE.keyCode(36);
  delay(1.2);
  return JSON.stringify({ok: devant() === "WhatsApp", devant: devant()});
}
"""


def ouvrir_groupe(nom_groupe, copier=None, attente=ATTENTE_WA):
    """Met `copier` dans le presse-papiers et ouvre le groupe. Ne colle rien.

    Sert a l'evenement WhatsApp : c'est Julien qui ouvre « + > Event » et
    colle dans le champ voulu. Rien n'est publie par ce chemin.
    """
    nom = (nom_groupe or "").strip()
    if not nom:
        raise Refus("quel groupe ?")
    if copier:
        try:
            subprocess.run(["pbcopy"], input=copier.encode("utf-8"),
                           check=True, timeout=10)
        except Exception as exc:
            raise Refus("presse-papiers indisponible : %s" % exc)

    charge = json.dumps({"groupe": nom, "attente": float(attente)})
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _WA_OUVRIR, charge],
                           capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise Refus("WhatsApp n'a pas répondu")
    except OSError as exc:
        raise Refus("osascript indisponible : %s" % exc)
    if r.returncode != 0:
        detail = [l for l in (r.stderr or "").splitlines() if l.strip()]
        souci = detail[-1] if detail else "WhatsApp a refusé"
        if "assistive" in souci.lower() or "1002" in souci:
            raise Refus("il manque l'autorisation Accessibilité : Réglages "
                        "Système > Confidentialité et sécurité > Accessibilité, "
                        "ajoutez SnapActions")
        raise Refus(souci)
    try:
        out = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        out = {}
    if not out.get("ok"):
        raise Refus("WhatsApp n'est pas resté devant (%s) : le texte est dans "
                    "le presse-papiers, ouvrez le groupe à la main."
                    % (out.get("devant") or "?"))
    db.log("mission", "Groupe « %s » ouvert pour un événement (rien collé)" % nom)
    return {"ok": True, "groupe": nom, "colle": False}


def vers_groupe(nom_groupe, corps, attente=ATTENTE_WA):
    """Ouvre le groupe et y colle le message. N'envoie pas.

    Rend {ok, colle: True}. L'envoi reste un geste de Julien : dans un
    groupe, se tromper de conversation se voit par tout le monde.
    """
    nom = (nom_groupe or "").strip()
    if not nom:
        raise Refus("quel groupe ?")
    corps = (corps or "").strip()
    if not corps:
        raise Refus("le message est vide")

    try:
        subprocess.run(["pbcopy"], input=corps.encode("utf-8"),
                       check=True, timeout=10)
    except Exception as exc:
        raise Refus("presse-papiers indisponible : %s" % exc)

    charge = json.dumps({"groupe": nom, "attente": float(attente)})
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", _WA_GROUPE, charge],
                           capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise Refus("WhatsApp n'a pas répondu")
    except OSError as exc:
        raise Refus("osascript indisponible : %s" % exc)
    if r.returncode != 0:
        detail = [l for l in (r.stderr or "").splitlines() if l.strip()]
        souci = detail[-1] if detail else "WhatsApp a refusé"
        if "assistive" in souci.lower() or "1002" in souci:
            raise Refus("il manque l'autorisation Accessibilité : Réglages "
                        "Système > Confidentialité et sécurité > Accessibilité, "
                        "ajoutez SnapActions")
        raise Refus(souci)
    try:
        out = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        out = {}
    if not out.get("ok"):
        raise Refus("WhatsApp n'est pas resté devant (%s, à l'étape « %s ») : rien "
                    "n'a été collé. Le texte est dans le presse-papiers."
                    % (out.get("devant") or "?", out.get("etape") or "?"))
    db.log("mission", "Message collé dans le groupe « %s » (pas envoyé)" % nom)
    return {"ok": True, "colle": True, "groupe": nom}


def par_whatsapp(numero, corps, attente=ATTENTE_WA):
    """Ouvre la conversation et appuie sur Entree. Leve Refus si ca coince."""
    import re
    import urllib.parse
    chiffres = re.sub(r"\D", "", str(numero or ""))
    if chiffres.startswith("00"):
        chiffres = chiffres[2:]
    if chiffres.startswith("0"):
        chiffres = "33" + chiffres[1:]
    if len(chiffres) < 10:
        raise Refus("ce numero n'est pas utilisable sur WhatsApp")
    corps = (corps or "").strip()
    if not corps:
        raise Refus("le message est vide")

    url = "whatsapp://send?phone=%s&text=%s" % (
        chiffres, urllib.parse.quote(corps, safe=""))
    charge = json.dumps({"url": url, "attente": float(attente)})
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _WA, charge],
            capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise Refus("WhatsApp n'a pas repondu")
    except OSError as exc:
        raise Refus("osascript indisponible : %s" % exc)
    if r.returncode != 0:
        detail = [l for l in (r.stderr or "").splitlines() if l.strip()]
        souci = detail[-1] if detail else "WhatsApp a refuse"
        if "assistive" in souci.lower() or "1002" in souci:
            raise Refus("il manque l'autorisation Accessibilite : Reglages "
                        "Systeme > Confidentialite et securite > Accessibilite, "
                        "ajoutez SnapActions")
        raise Refus(souci)
    try:
        out = json.loads((r.stdout or "").strip() or "{}")
    except ValueError:
        out = {}
    if not out.get("ok"):
        raise Refus("WhatsApp n'est pas passe devant (%s) : rien n'a ete envoye, "
                    "la conversation est ouverte, appuyez sur Entree"
                    % (out.get("devant") or "?"))
    return {"ok": True, "a": "+" + chiffres}
