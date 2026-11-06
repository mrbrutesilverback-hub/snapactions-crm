# -*- coding: utf-8 -*-
"""Lecture du texte d'une image, avec le moteur integre a macOS.

Aucune dependance a installer : on passe par JavaScript for Automation, qui
donne acces au framework Vision livre avec le systeme. C'est le meme moteur
que le « texte en direct » de l'Apercu.

Si ca echoue (macOS trop ancien, script refuse), on essaie un raccourci
nomme « OCR » s'il existe, puis on abandonne proprement : l'appelant doit
savoir se passer du texte plutot que de planter.
"""

import os
import subprocess

JXA = r'''
ObjC.import('Vision');
ObjC.import('Foundation');

function lire(chemin) {
  var url = $.NSURL.fileURLWithPath(chemin);
  var handler = $.VNImageRequestHandler.alloc.initWithURLOptions(url, $.NSDictionary.dictionary);
  var req = $.VNRecognizeTextRequest.alloc.init;
  req.recognitionLevel = 0;                       // 0 = precis, 1 = rapide
  req.recognitionLanguages = $(['fr-FR', 'en-US']);
  req.usesLanguageCorrection = true;
  handler.performRequestsError($([req]), $());
  var res = req.results;
  var lignes = [];
  for (var i = 0; i < res.count; i++) {
    var obs = res.objectAtIndex(i);
    var cand = obs.topCandidates(1);
    if (cand.count > 0) lignes.push(ObjC.unwrap(cand.objectAtIndex(0).string));
  }
  return lignes.join('\n');
}

function run(argv) {
  try { return lire(argv[0]); }
  catch (e) { return '__ERREUR__' + e; }
}
'''

IMAGES = (".jpg", ".jpeg", ".png", ".heic", ".heif", ".webp", ".tiff", ".gif")


class OcrIndisponible(Exception):
    pass


def _via_jxa(chemin):
    try:
        p = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA, chemin],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrIndisponible("osascript indisponible : %s" % exc)
    sortie = p.stdout.decode("utf-8", "replace").strip()
    erreur = p.stderr.decode("utf-8", "replace").strip()
    if p.returncode != 0 or sortie.startswith("__ERREUR__"):
        detail = (sortie.replace("__ERREUR__", "") + " " + erreur).strip()
        raise OcrIndisponible("code %s : %s" % (p.returncode, detail[:400] or "reponse vide"))
    return sortie


def _via_raccourci(chemin):
    """Repli : un raccourci macOS nomme « OCR » qui prend un fichier et rend du texte."""
    try:
        p = subprocess.run(["shortcuts", "run", "OCR", "-i", chemin],
                           capture_output=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise OcrIndisponible("raccourci indisponible : %s" % exc)
    if p.returncode != 0:
        raise OcrIndisponible(p.stderr.decode("utf-8", "replace")[:200])
    return p.stdout.decode("utf-8", "replace").strip()


def texte(chemin):
    """Rend le texte lu, ou leve OcrIndisponible avec la raison."""
    if not os.path.exists(chemin):
        raise OcrIndisponible("fichier introuvable")
    if not chemin.lower().endswith(IMAGES):
        raise OcrIndisponible("les PDF ne passent pas par ce lecteur ; "
                              "exportez la page en image, ou laissez le passage automatique le lire")
    try:
        return _via_jxa(chemin)
    except OcrIndisponible as premier:
        try:
            return _via_raccourci(chemin)
        except OcrIndisponible:
            raise premier


def lire_dossier(dossier):
    """Lit toutes les images d'un dossier. Rend [(fichier, texte, erreur)]."""
    sortie = []
    try:
        noms = sorted(n for n in os.listdir(dossier)
                      if not n.startswith(".") and n != "LISEZ-MOI.txt"
                      and os.path.isfile(os.path.join(dossier, n)))
    except OSError as exc:
        return [(None, None, str(exc))]
    for n in noms:
        try:
            sortie.append((n, texte(os.path.join(dossier, n)), None))
        except OcrIndisponible as exc:
            sortie.append((n, None, str(exc)))
    return sortie


if __name__ == "__main__":
    import sys
    for f, t, e in lire_dossier(sys.argv[1] if len(sys.argv) > 1 else "."):
        print("=== %s ===" % f)
        print(e if e else t)
