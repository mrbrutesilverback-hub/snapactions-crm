/* Dépôt de flyers — onglet Import.
   Autonome : ne touche à rien dans app.js, ne parle qu'à /api/depot. */
(function () {
  "use strict";

  var drop = document.getElementById("flyerDrop");
  var input = document.getElementById("flyerInput");
  var pick = document.getElementById("flyerPick");
  var etat = document.getElementById("flyerEtat");
  var liste = document.getElementById("flyerListe");
  if (!drop || !input || !liste) return;

  function puce(texte, encours) {
    var s = document.createElement("span");
    s.textContent = texte;
    s.style.cssText = "font-size:12.5px;border-radius:100px;padding:4px 11px;"
      + "background:var(--panel);border:1px solid var(--line);color:var(--mute);"
      + "max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap";
    if (encours) s.style.borderColor = "var(--accent)";
    return s;
  }

  function bruit(msg) {
    var t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(bruit._t);
    bruit._t = setTimeout(function () { t.hidden = true; }, 2600);
  }

  function retirer(nom, noeud) {
    return fetch("/api/depot/supprimer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nom: nom })
    }).then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
      .then(function (res) {
        if (!res.ok) throw new Error((res.d && res.d.error) || "refusé");
        noeud.remove();
        bruit(nom + " retiré");
        return rafraichir();
      })
      .catch(function (e) { bruit("Impossible de retirer : " + e.message); });
  }

  function rafraichir() {
    return fetch("/api/depot").then(function (r) { return r.json(); }).then(function (d) {
      var attente = (d && d.en_attente) || [];
      liste.innerHTML = "";
      attente.forEach(function (n) {
        var p = puce(n);
        p.style.display = "inline-flex";
        p.style.alignItems = "center";
        p.style.gap = "8px";
        var x = document.createElement("button");
        x.type = "button";
        x.textContent = "✕";
        x.title = "Retirer " + n;
        x.style.cssText = "border:0;background:none;color:var(--mute);cursor:pointer;"
          + "font-size:14px;line-height:1;padding:0";
        x.addEventListener("click", function (e) { e.stopPropagation(); retirer(n, p); });
        p.appendChild(x);
        liste.appendChild(p);
      });
      var traites = (d && d.traites) || 0;
      etat.textContent = (attente.length
        ? (attente.length === 1 ? "1 document en attente. " : attente.length + " documents en attente. ")
        : "")
        + (attente.length ? "Cliquez sur Analyser maintenant." : "Rien en attente.")
        + (traites ? " " + traites + " déjà traités." : "");
    }).catch(function () { /* la boîte n'est pas critique */ });
  }

  function envoyer(fichiers) {
    var f = Array.prototype.slice.call(fichiers || []);
    if (!f.length) return;
    var suite = Promise.resolve();
    f.forEach(function (fichier) {
      suite = suite.then(function () {
        var p = puce("Envoi de " + fichier.name + "…", true);
        liste.appendChild(p);
        var fd = new FormData();
        fd.append("file", fichier, fichier.name);
        return fetch("/api/depot", { method: "POST", body: fd })
          .then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); })
          .then(function (res) {
            if (!res.ok) throw new Error((res.d && res.d.error) || "refusé");
            p.textContent = (res.d && res.d.nom) || fichier.name;
            p.style.borderColor = "var(--line)";
          })
          .catch(function (e) {
            p.textContent = fichier.name + " — " + e.message;
            bruit(fichier.name + " : " + e.message);
          });
      });
    });
    return suite.then(function () {
      return rafraichir();
    }).then(function () {
      bruit(f.length === 1 ? "Document déposé" : f.length + " documents déposés");
    });
  }

  if (pick) pick.addEventListener("click", function (e) { e.preventDefault(); input.click(); });
  drop.addEventListener("click", function (e) {
    if (e.target === pick) return;
    input.click();
  });
  input.addEventListener("change", function () { envoyer(input.files); input.value = ""; });

  ["dragenter", "dragover"].forEach(function (ev) {
    drop.addEventListener(ev, function (e) { e.preventDefault(); drop.classList.add("hot"); });
  });
  drop.addEventListener("dragleave", function (e) {
    if (drop.contains(e.relatedTarget)) return;
    drop.classList.remove("hot");
  });
  drop.addEventListener("drop", function (e) {
    e.preventDefault();
    drop.classList.remove("hot");
    envoyer(e.dataTransfer && e.dataTransfer.files);
  });

  // Texte lu sur l'ecran d'accueil : on le depose dans l'import et on analyse.
  try {
    var venu = sessionStorage.getItem("sa_ocr");
    if (venu) {
      sessionStorage.removeItem("sa_ocr");
      var onglet = document.querySelector('#tabs [data-tab="import"]');
      if (onglet) onglet.click();
      var zone = document.getElementById("importText");
      if (zone) {
        zone.value = venu;
        zone.scrollIntoView({ block: "center" });
        var go = document.getElementById("importPreview");
        if (go) setTimeout(function () { go.click(); }, 400);
      }
    }
  } catch (e) { /* stockage indisponible : tant pis */ }

  // Bouton d'analyse immediate, aussi depuis l'onglet Import.
  var lire = document.createElement("button");
  lire.type = "button";
  lire.className = "primary";
  lire.textContent = "Analyser maintenant";
  lire.style.cssText = "margin-top:10px";
  lire.addEventListener("click", function (e) {
    e.stopPropagation();
    lire.disabled = true;
    var avant = lire.textContent;
    lire.textContent = "Lecture en cours…";
    fetch("/api/depot/analyser", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (d && d.items && d.items.length) {
          var zone = document.getElementById("importText");
          if (zone) {
            zone.value = d.texte;
            zone.scrollIntoView({ block: "center" });
            var go = document.getElementById("importPreview");
            if (go) go.click();
          }
          bruit(d.items.length + " document(s) lu(s)");
        } else {
          bruit((d && d.soucis || []).join(" · ") || "Rien à lire dans la boîte");
        }
      })
      .catch(function () { bruit("Lecture impossible sur ce Mac"); })
      .then(function () { lire.disabled = false; lire.textContent = avant; });
  });
  drop.appendChild(lire);

  rafraichir();
  setInterval(rafraichir, 60000);
})();
