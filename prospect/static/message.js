/* Le panneau de message, partage par tous les ecrans de cartes.
   Il affiche le texte, laisse Julien le corriger, et lui donne une zone ou
   dire ce qu'il veut changer. Il n'envoie JAMAIS : il ouvre WhatsApp ou le
   mail deja ecrits, et c'est Julien qui appuie. */
(function (global) {
  function el(b, c, t) {
    const e = document.createElement(b);
    if (c) e.className = c;
    if (t != null) e.textContent = t;
    return e;
  }

  function dire(m) {
    if (typeof global.toast === "function") { global.toast(m); return; }
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = m; t.hidden = false;
    clearTimeout(dire._t);
    dire._t = setTimeout(() => { t.hidden = true; }, 2600);
  }

  function ouvrir(url) {
    fetch("/api/ouvrir", {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({url: url})})
      .then(r => { if (!r.ok) throw new Error("refus"); })
      .catch(() => window.open(url, "_blank", "noopener"));
  }

  function numeroWa(tel) {
    const brut = String(tel || "").replace(/\D/g, "").replace(/^00/, "");
    const n = /^377/.test(brut) ? brut : brut.replace(/^0/, "33");
    return n.length >= 10 ? n : "";
  }

  const RACCOURCIS = ["Plus court", "Plus chaleureux", "Plus direct",
    "Moins commercial"];

  /* La zone ou Julien dit ce qu'il veut changer. On retouche toujours le
     texte qu'il a sous les yeux : il peut donc enchainer les demandes. */
  function causerie(zone, contexte) {
    const t = zone.querySelector("textarea");
    const c = el("div", "causerie");
    const dits = el("div", "dits");
    c.appendChild(dits);

    const ligne = el("div", "ligne");
    const champ = el("input");
    champ.type = "text";
    champ.placeholder = "Dites ce qu’il faut changer…";
    ligne.appendChild(champ);
    const go = el("button", null, "Refaire");
    go.type = "button";
    ligne.appendChild(go);
    c.appendChild(ligne);

    const r = el("div", "raccourcis");
    RACCOURCIS.forEach(mot => {
      const b = el("button", null, mot);
      b.type = "button";
      b.addEventListener("click", () => lancer(mot));
      r.appendChild(b);
    });
    c.appendChild(r);
    zone.appendChild(c);

    async function lancer(consigne) {
      consigne = (consigne || champ.value).trim();
      if (!consigne) { champ.focus(); return; }
      go.disabled = true; champ.disabled = true;
      const avant = go.textContent;
      go.textContent = "…";
      try {
        const rep = await fetch("/api/message/retoucher", {
          method: "POST", headers: {"Content-Type": "application/json"},
          body: JSON.stringify({texte: t.value, consigne: consigne,
                                contexte: contexte || ""})});
        const d = await rep.json().catch(() => ({}));
        if (!rep.ok) throw new Error(d.error || "refus du serveur");
        t.value = d.texte;
        dits.appendChild(el("div", "dit", consigne));
        champ.value = "";
      } catch (e) {
        dire("Échec : " + e.message);
      } finally {
        go.disabled = false; champ.disabled = false;
        go.textContent = avant;
        champ.focus();
      }
    }

    go.addEventListener("click", () => lancer());
    champ.addEventListener("keydown", e => {
      if (e.key === "Enter") { e.preventDefault(); lancer(); }
    });
  }

  /* opts : {texte, objet, tel, email, contexte} */
  function panneau(carteDom, opts) {
    let z = carteDom.querySelector(".brouillon");
    if (z) { z.querySelector("textarea").value = opts.texte || ""; return z; }

    z = el("div", "brouillon");
    if (opts.objet) {
      const o = el("div", "objet");
      o.appendChild(el("b", null, "Objet : "));
      o.appendChild(document.createTextNode(opts.objet));
      z.appendChild(o);
    }
    const t = el("textarea");
    t.rows = 10;
    t.value = opts.texte || "";
    z.appendChild(t);

    const b = el("div", "faire");
    /* Les boutons d'envoi ne sont plus ecrits ici : c'est envoyer.js qui
       les pose, les memes sur tous les ecrans. Il y en avait deux de chaque
       — « Envoyer sur WhatsApp » et « Ouvrir dans WhatsApp », « Envoyer a »
       et « Ouvrir le mail » — et on ne savait plus lequel envoyait. */
    if (global.Envoyer) {
      global.Envoyer.poser(b, {club_id: opts.club_id, tel: opts.tel,
                               email: opts.email, objet: opts.objet,
                               draft_id: opts.draft_id,
                               texte: () => t.value});
    }

    const cp = el("button", null, "Copier");
    cp.type = "button";
    cp.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(t.value); dire("Copié"); }
      catch (e) { t.select(); dire("Sélectionné — Cmd+C"); }
    });
    b.appendChild(cp);
    z.appendChild(b);

    /* Ni numero ni mail : un message sans destinataire ne sert a rien.
       On laisse Julien en coller un — il le voit souvent sur la page du
       club avant que le veilleur ne passe. Il est retenu sur la fiche,
       pour ne pas avoir a le retaper. */
    if (!numeroWa(opts.tel) && !opts.email) {
      const manque = el("div", "manque");
      manque.appendChild(el("small", null,
        "Pas de num\u00e9ro ni de mail : collez-en un pour pouvoir envoyer."));
      const ligne = el("div", "ligne");
      const champ = el("input");
      champ.type = "text";
      champ.placeholder = "06\u2026 ou contact@club.fr";
      ligne.appendChild(champ);
      const ok = el("button", "primaire", "Utiliser");
      ok.type = "button";
      ok.addEventListener("click", async () => {
        const v = champ.value.trim();
        if (!v) { champ.focus(); return; }
        ok.disabled = true;
        try {
          if (opts.club_id) {
            await fetch("/api/clubs/" + opts.club_id + "/coordonnee", {
              method: "POST", headers: {"Content-Type": "application/json"},
              body: JSON.stringify({valeur: v})});
          }
          /* On refait le panneau avec la coordonnee : les boutons
             d'envoi apparaissent alors d'eux-memes. */
          const garde = t.value;
          z.remove();
          const neuf = Object.assign({}, opts, garde ? {texte: garde} : {});
          if (v.indexOf("@") >= 0) neuf.email = v; else neuf.tel = v;
          panneau(carteDom, neuf);
          dire("Retenu \u2014 vous pouvez envoyer");
        } catch (e) {
          ok.disabled = false;
          dire("\u00c9chec : " + e.message);
        }
      });
      champ.addEventListener("keydown", e => {
        if (e.key === "Enter") { e.preventDefault(); ok.click(); }
      });
      ligne.appendChild(ok);
      manque.appendChild(ligne);
      z.appendChild(manque);
    }

    z.appendChild(el("small", null, opts.email
      ? "Relisez avant d\u2019envoyer : le bouton envoie vraiment, et un mail "
        + "ne se rattrape pas."
      : "Relisez-le : rien ne part tout seul."));
    carteDom.appendChild(z);
    causerie(z, opts.contexte);
    return z;
  }

  global.Message = {panneau: panneau, ouvrir: ouvrir, numeroWa: numeroWa,
                    dire: dire, el: el};
})(window);
