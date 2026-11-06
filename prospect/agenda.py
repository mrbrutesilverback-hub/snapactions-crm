# -*- coding: utf-8 -*-
"""Le calendrier de travail, en ecriture.

Jusqu'ici « Mes dates » lisait le lien iCloud publie. Un lien publie est une
sortie, pas une entree : on ne peut rien y ecrire, et iCloud y reattribue
meme des UID differents de ceux du calendrier reel — les 46 evenements du
flux et les 46 du Mac n'avaient aucun identifiant en commun. Impossible donc
de dire « modifie CELUI-LA » a partir du flux.

On passe donc par Calendrier, l'application du Mac, qui est pilotable. Elle
est deja authentifiee aupres d'iCloud : aucun mot de passe a stocker ici,
aucune implementation de CalDAV, et ce qu'on ecrit remonte sur l'iPhone comme
le reste. Le flux publie reste le filet quand Calendrier ne repond pas.

Prudence sur deux points appris en testant :
  - deplacer un evenement plus tard en posant d'abord le debut le fait passer
    apres sa propre fin, et Calendrier refuse l'enregistrement. On pose donc
    les deux bornes dans l'ordre qui garde l'etat valide a chaque instant ;
  - rien n'est interpole dans le script : la commande part en JSON par argv,
    sinon un titre contenant une apostrophe casse tout.
"""

import datetime as dt
import json
import subprocess

from . import db

CALENDRIER_DEFAUT = "SnapActions"

# Calendrier met quelques secondes a repondre a froid, moins d'une ensuite.
# Mesure du 16 octobre 2026 : UNE colonne de 54 evenements coute 5,8 s, et le
# script en lit sept — une quarantaine de secondes. A 25 s, la lecture echouait
# donc systematiquement, et comme un echec ne se met pas en cache, chaque appel
# repayait l'attente. Le menu interrogeait /api/badges a l'ouverture des quatre
# ecrans : vingt-six secondes de blocage a chaque page.
DELAI = 70


def nom_calendrier():
    return (db.get_setting("agenda_calendrier") or "").strip() or CALENDRIER_DEFAUT


_JXA = r"""
function run(argv) {
  const o = JSON.parse(argv[0]);
  const Cal = Application("Calendar");
  const bilan = (x) => JSON.stringify(x);

  if (o.quoi === "calendriers") {
    return bilan(Cal.calendars().map(function (c) {
      let w = true; try { w = c.writable(); } catch (e) {}
      return {nom: c.name(), inscriptible: w};
    }));
  }

  let cal;
  try { cal = Cal.calendars.byName(o.calendrier); cal.name(); }
  catch (e) { return bilan({erreur: "calendrier introuvable : " + o.calendrier}); }

  // Les bornes arrivent en composants, jamais en texte : « 2026-10-14T09:00 »
  // se lit differemment selon le moteur, un tableau ne se lit que d'une facon.
  const quand = (t) => t ? new Date(t[0], t[1] - 1, t[2], t[3] || 0, t[4] || 0, 0) : null;
  // Heure LOCALE, pas UTC : « 2027-12-24T10:00 » cree a 10 h doit se relire
  // a 10 h. toISOString() renvoyait 09:00 en hiver, et l'ecran aurait affiche
  // une heure de moins que celle saisie.
  const deuxCh = (n) => (n < 10 ? "0" : "") + n;
  const local = (d) => d.getFullYear() + "-" + deuxCh(d.getMonth() + 1) + "-"
    + deuxCh(d.getDate()) + "T" + deuxCh(d.getHours()) + ":" + deuxCh(d.getMinutes());
  const fiche = (e) => ({
    uid: e.uid(), titre: e.summary(),
    debut: local(e.startDate()), fin: local(e.endDate()),
    journee: e.alldayEvent(), lieu: e.location() || "",
    notes: e.description() || "",
  });

  if (o.quoi === "lister") {
    // Lire propriete par propriete faisait un aller-retour Apple Event par
    // champ et par evenement — 46 fiches x 7 champs, quatorze secondes. En
    // demandant chaque colonne d'un coup, c'est sept allers-retours en tout,
    // et quatre secondes et demie.
    // Deux fausses bonnes idees, mesurees : events.properties() n'est pas
    // supporte par Calendrier (« Invalid key form »), et filtrer en amont
    // avec whose({endDate: {">": borne}}) est SIX FOIS PLUS LENT — trente
    // secondes — parce que le predicat est re-evalue a chaque colonne lue.
    // On lit donc tout, et on filtre ici.
    const col = cal.events;
    const uids = col.uid(), titres = col.summary();
    const debuts = col.startDate(), fins = col.endDate();
    const jours = col.alldayEvent(), lieux = col.location(), notes = col.description();
    const borne = quand(o.depuis);
    const out = [];
    for (let i = 0; i < uids.length; i++) {
      if (borne && fins[i] < borne) continue;
      out.push({uid: uids[i], titre: titres[i] || "",
                debut: local(debuts[i]), fin: local(fins[i]),
                journee: !!jours[i], lieu: lieux[i] || "", notes: notes[i] || ""});
    }
    out.sort((a, b) => a.debut < b.debut ? -1 : 1);
    return bilan(out);
  }

  if (o.quoi === "creer") {
    const champs = {summary: o.titre, startDate: quand(o.debut), endDate: quand(o.fin)};
    if (o.lieu) champs.location = o.lieu;
    if (o.notes) champs.description = o.notes;
    if (o.journee) champs.alldayEvent = true;
    const e = Cal.Event(champs);
    cal.events.push(e);
    return bilan(fiche(e));
  }

  const trouve = cal.events.whose({uid: o.uid})();
  if (!trouve.length) return bilan({erreur: "evenement introuvable"});
  const e = trouve[0];

  if (o.quoi === "supprimer") {
    const avant = fiche(e);
    e.delete();
    return bilan({supprime: true, etait: avant});
  }

  if (o.quoi === "modifier") {
    // L'ordre compte : poser un debut au-dela de la fin actuelle fait rejeter
    // l'enregistrement par Calendrier (« start date must be before end date »).
    const d = quand(o.debut), f = quand(o.fin);
    if (d && f) {
      if (d > e.endDate()) { e.endDate = f; e.startDate = d; }
      else                 { e.startDate = d; e.endDate = f; }
    } else if (d) { e.startDate = d; }
    else if (f)   { e.endDate = f; }
    if (o.titre !== undefined && o.titre !== null) e.summary = o.titre;
    if (o.lieu !== undefined && o.lieu !== null) e.location = o.lieu;
    if (o.notes !== undefined && o.notes !== null) e.description = o.notes;
    if (o.journee !== undefined && o.journee !== null) e.alldayEvent = !!o.journee;
    return bilan(fiche(cal.events.whose({uid: o.uid})()[0]));
  }

  return bilan({erreur: "ordre inconnu : " + o.quoi});
}
"""


class AgendaIndisponible(Exception):
    """Calendrier n'a pas repondu — on le dit, on ne devine pas."""


def _appeler(ordre):
    ordre.setdefault("calendrier", nom_calendrier())
    try:
        r = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", _JXA, json.dumps(ordre)],
            capture_output=True, text=True, timeout=DELAI)
    except subprocess.TimeoutExpired:
        raise AgendaIndisponible("Calendrier n'a pas repondu en %d s" % DELAI)
    except OSError as e:
        raise AgendaIndisponible("osascript indisponible : %s" % e)
    if r.returncode != 0:
        detail = (r.stderr or "").strip().splitlines()
        raise AgendaIndisponible(detail[-1] if detail else "erreur Calendrier")
    try:
        out = json.loads(r.stdout or "null")
    except ValueError:
        raise AgendaIndisponible("reponse illisible de Calendrier")
    if isinstance(out, dict) and out.get("erreur"):
        raise AgendaIndisponible(out["erreur"])
    return out


def _bornes(valeur):
    """ISO ou date -> [annee, mois, jour, heure, minute], sans ambiguite d'heure."""
    if not valeur:
        return None
    if isinstance(valeur, dt.datetime):
        d = valeur
    elif isinstance(valeur, dt.date):
        d = dt.datetime.combine(valeur, dt.time(0, 0))
    else:
        s = str(valeur).strip().replace("T", " ")
        for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                d = dt.datetime.strptime(s, f)
                break
            except ValueError:
                continue
        else:
            raise ValueError("date incomprise : %s" % valeur)
    return [d.year, d.month, d.day, d.hour, d.minute]


def calendriers():
    return _appeler({"quoi": "calendriers"})


def lister(depuis=None):
    """Les evenements du calendrier de travail, le passe proche compris."""
    if depuis is None:
        depuis = dt.date.today() - dt.timedelta(days=1)
    return _appeler({"quoi": "lister", "depuis": _bornes(depuis)})


# ---------------------------------------------------------------------------
# Le cache de lecture
# ---------------------------------------------------------------------------
# Lire le calendrier coute six secondes, mesurees : ce n'est pas la base qui
# est lente, c'est Apple. Chaque aller-retour avec Calendrier.app passe par les
# Apple Events, ~2,3 s piece, et il en faut sept pour ramener les colonnes.
# Rien ne peut rendre cet appel rapide — on peut seulement arreter de le
# refaire. Le serveur tourne en continu : on garde donc le resultat en memoire
# et la page s'ouvre instantanement tant qu'il est frais.
#
# L'ecran dit toujours de quand date la lecture, et un bouton force la
# relecture : on ne cache jamais au prix de montrer un calendrier perime sans
# le dire.
FRAICHEUR = 600          # dix minutes
_memoire = {}            # {cle: (horodatage, evenements)}
# Une panne aussi se retient. Sans cela, un Calendrier muet coute son plein
# delai d'attente A CHAQUE requete, et c'est l'ecran qui le paie.
PANNE_FRAICHEUR = 120    # deux minutes avant de retenter
_panne = {}              # {cle: (horodatage, motif)}


def lister_vite(depuis=None, frais=False, attendre=True):
    """Comme lister(), mais sans repayer les six secondes a chaque ouverture.

    Renvoie (evenements, lu_le, du_cache).

    `attendre=False` : on rend ce qu'on a sous la main et rien de plus. C'est
    ce qu'il faut pour un compteur de menu — un chiffre agreable n'a pas a
    bloquer l'ouverture d'une page pendant que les Apple Events se reveillent.
    Sans evenement en memoire, on leve AgendaIndisponible : l'appelant decide.
    """
    if depuis is None:
        depuis = dt.date.today() - dt.timedelta(days=1)
    cle = str(depuis)
    pose = _memoire.get(cle)
    maintenant = dt.datetime.now()
    if pose and not frais and (maintenant - pose[0]).total_seconds() < FRAICHEUR:
        return pose[1], pose[0].isoformat(timespec="seconds"), True
    if not attendre:
        if pose:
            return pose[1], pose[0].isoformat(timespec="seconds"), True
        raise AgendaIndisponible("calendrier pas encore lu")
    # Calendrier vient de ne pas repondre : inutile de repayer l'attente tout
    # de suite. On rend le dernier etat connu, meme perime, ou l'on renonce.
    recent = _panne.get(cle)
    if recent and not frais and (maintenant - recent[0]).total_seconds() < PANNE_FRAICHEUR:
        if pose:
            return pose[1], pose[0].isoformat(timespec="seconds"), True
        raise AgendaIndisponible(recent[1])
    try:
        evs = lister(depuis)
    except AgendaIndisponible as e:
        _panne[cle] = (maintenant, str(e))
        raise
    _panne.pop(cle, None)
    _memoire[cle] = (maintenant, evs)
    return evs, maintenant.isoformat(timespec="seconds"), False


def oublier():
    """Vide le cache : appele des qu'on ecrit dans le calendrier."""
    _memoire.clear()
    _panne.clear()


def creer(titre, debut, fin=None, lieu=None, notes=None, journee=False):
    titre = (titre or "").strip()
    if not titre:
        raise ValueError("il faut un titre")
    d = _bornes(debut)
    if not d:
        raise ValueError("il faut une date de debut")
    f = _bornes(fin) if fin else None
    if not f:
        # Sans fin donnee : une heure, ou la journee entiere si c'est un jour nu.
        base = dt.datetime(d[0], d[1], d[2], d[3], d[4])
        f = _bornes(base + dt.timedelta(hours=1))
    fait = _appeler({"quoi": "creer", "titre": titre, "debut": d, "fin": f,
                     "lieu": lieu, "notes": notes, "journee": bool(journee)})
    oublier()
    return fait


def modifier(uid, titre=None, debut=None, fin=None, lieu=None, notes=None,
             journee=None):
    if not (uid or "").strip():
        raise ValueError("quel evenement ?")
    fait = _appeler({"quoi": "modifier", "uid": uid.strip(),
                     "titre": titre, "debut": _bornes(debut), "fin": _bornes(fin),
                     "lieu": lieu, "notes": notes, "journee": journee})
    oublier()
    return fait


def supprimer(uid):
    if not (uid or "").strip():
        raise ValueError("quel evenement ?")
    fait = _appeler({"quoi": "supprimer", "uid": uid.strip()})
    oublier()
    return fait


# ---------------------------------------------------------------------------
# Le prechauffage
# ---------------------------------------------------------------------------
# Lire le calendrier coute desormais une quarantaine de secondes : Apple
# repond colonne par colonne, et il y en a sept. Aucun ecran ne peut payer ca
# a l'ouverture. On le lit donc A COTE, en continu, pour que le cache soit
# toujours chaud quand une page arrive. L'ecran ne paie plus jamais l'attente ;
# au pire il affiche un calendrier de huit minutes d'age, en le disant.
PRECHAUFFE = 480         # huit minutes, sous les dix du cache


def prechauffer(arret=None):
    """Boucle de fond : garde le calendrier frais pour tout le monde."""
    import threading
    import time

    def tourner():
        while arret is None or not arret.is_set():
            try:
                lister_vite(frais=True)
            except AgendaIndisponible:
                pass          # Calendrier muet : on reessaiera, sans bruit
            except Exception:
                pass
            for _ in range(PRECHAUFFE):
                if arret is not None and arret.is_set():
                    return
                time.sleep(1)

    fil = threading.Thread(target=tourner, name="agenda-prechauffe", daemon=True)
    fil.start()
    return fil
