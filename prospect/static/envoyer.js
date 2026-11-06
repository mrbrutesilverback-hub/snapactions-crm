/* Les boutons qui envoient vraiment, partout pareil.
   Chaque ecran avait ses propres boutons « Ouvrir dans WhatsApp » et
   « Envoyer par mail » qui ne faisaient qu'ouvrir. Ici on pose les trois
   memes gestes a cote : envoyer sur WhatsApp, envoyer le mail, ou noter
   qu'on l'a fait soi-meme.

   Rien ne part sans un clic, sur un texte affiche. */
(function (global) {
  "use strict";

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
    dire._t = setTimeout(() => { t.hidden = true; }, 3000);
  }

  function mobile(tel) {
    const d = String(tel || "").replace(/\D/g, "").replace(/^00/, "");
    const n = /^377/.test(d) ? d : d.replace(/^0/, "33");
    if (/^377/.test(n)) return !/^3779/.test(n);     /* Monaco : 9 = fixe */
    return /^33[67]/.test(n) && n.length === 11;
  }

  async function poster(url, charge) {
    const r = await fetch(url, {method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify(charge)});
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(d.error || "refus du serveur");
    return d;
  }

  /* --- Avant d'appuyer : a-t-on deja ecrit a ce numero ? ----------------
     L'envoi part pour de vrai au clic. S'en apercevoir apres coup oblige a
     aller supprimer le message dans la conversation, et il est parfois deja
     lu. Le meme club revient souvent : deux tournois dans la saison, un
     numero deja dans le carnet depuis des mois.

     Si la verification echoue, on laisse passer : elle rend service, elle
     n'a pas a empecher un envoi. */
  async function dejaEcrit(opts, tel) {
    try {
      const p = new URLSearchParams();
      if (tel) p.set("tel", tel);
      if (opts.club_id) p.set("club_id", opts.club_id);
      if (opts.email) p.set("email", opts.email);
      if (![...p.keys()].length) return null;
      const r = await fetch("/api/deja-ecrit?" + p.toString());
      if (!r.ok) return null;
      const d = await r.json();
      return d && d.deja ? d : null;
    } catch (e) { return null; }
  }

  /* Rend true si on y va. La question est posee une fois par envoi. */
  async function feuVert(opts, tel) {
    const d = await dejaEcrit(opts, tel);
    if (!d) return true;
    const lignes = [d.phrase, ""];
    (d.traces || []).slice(0, 3).forEach(t => {
      lignes.push("· " + (t.quand_court || "") + "  " + (t.canal || "")
                  + " " + (t.sens || "")
                  + (t.quoi ? "\n    " + t.quoi : ""));
    });
    lignes.push("", "Envoyer quand même ?");
    return global.confirm(lignes.join("\n"));
  }

  /* Ce que le serveur a fait APRES l'envoi : la fiche a avance, la date est
     tranchee, la relance est posee. On le dit en une phrase, sinon personne
     ne sait que le travail a ete fait. */
  function _suite(d) {
    if (!d || (!d.range && !d.prochaine)) return "";
    const bouts = [];
    if (d.range) bouts.push("date rangée");
    if (d.prochaine) bouts.push("relance le " + String(d.prochaine).slice(8, 10)
                                 + "/" + String(d.prochaine).slice(5, 7));
    return " — " + bouts.join(", ");
  }

  /* opts : {club_id, tel, email, objet, draft_id, tournament_id,
             note, onEnvoi, texte: () => string} */
  function poser(conteneur, opts) {
    if (!conteneur || !opts || typeof opts.texte !== "function") return;
    if (conteneur.querySelector(".env-direct")) return;   /* deja pose */

    const lire = () => (opts.texte() || "").trim();

    /* L'ecran qui nous a poses veut souvent se rafraichir : la carte vient de
       quitter la pile, elle n'a plus rien a faire a l'ecran. */
    const fini = d => {
      if (typeof opts.onEnvoi === "function") {
        try { opts.onEnvoi(d); } catch (e) { /* l'envoi est fait, lui */ }
      }
    };

    /* Le numero peut changer : celui qu'on a trouve ne marche pas
       toujours sur WhatsApp, et Julien en a souvent un autre sous la
       main. Il le remplace ici, et il reessaie tout de suite. */
    let tel = opts.tel || "";

    if (tel && mobile(tel)) {
      const w = el("button", "env-direct env-wa", "▶ Envoyer sur WhatsApp");
      w.type = "button";
      w.title = "Ouvre la conversation et envoie. Le message part vraiment.";
      w.addEventListener("click", async () => {
        const avant = w.textContent;
        w.disabled = true; w.textContent = "Je vérifie…";
        if (!await feuVert(opts, tel)) {
          w.disabled = false; w.textContent = avant;
          dire("Envoi annulé — rien n’est parti");
          return;
        }
        w.textContent = "Envoi…";
        try {
          const d = await poster("/api/envoyer/whatsapp", {
            club_id: opts.club_id, tel: tel, corps: lire(),
            draft_id: opts.draft_id, tournament_id: opts.tournament_id});
          w.textContent = "✓ Envoyé";
          w.classList.add("fait");
          conteneur.dataset.parti = "1";
          dire("Parti sur WhatsApp à " + d.a + _suite(d));
          fini(d);
        } catch (e) {
          w.disabled = false; w.textContent = avant;
          dire("Échec : " + e.message);
        }
      });
      conteneur.appendChild(w);

      /* Changer de numero sans quitter la carte. */
      const chg = el("button", "env-chg", "✎ numéro");
      chg.type = "button";
      chg.title = "Essayer un autre numéro sur WhatsApp";
      chg.addEventListener("click", () => {
        if (conteneur.querySelector(".env-autre")) return;
        const zone = el("span", "env-autre");
        const champ = el("input");
        champ.type = "text";
        champ.value = tel;
        champ.placeholder = "06…";
        zone.appendChild(champ);
        const ok = el("button", null, "Utiliser");
        ok.type = "button";
        ok.addEventListener("click", () => {
          const v = champ.value.trim();
          if (!v) { champ.focus(); return; }
          if (!mobile(v)) { dire("Ce numéro ne passe pas sur WhatsApp"); return; }
          tel = v;
          w.disabled = false;
          w.classList.remove("fait");
          w.textContent = "▶ Envoyer sur WhatsApp";
          zone.remove();
          dire("Numéro remplacé : " + v);
        });
        champ.addEventListener("keydown", e => {
          if (e.key === "Enter") { e.preventDefault(); ok.click(); }
        });
        zone.appendChild(ok);
        conteneur.appendChild(zone);
        champ.focus(); champ.select();
      });
      conteneur.appendChild(chg);
    }

    if (opts.email) {
      const m = el("button", "env-direct env-mail",
        "✉ Envoyer à " + opts.email);
      m.type = "button";
      m.addEventListener("click", async () => {
        const avant = m.textContent;
        m.disabled = true; m.textContent = "Je vérifie…";
        if (!await feuVert(opts, null)) {
          m.disabled = false; m.textContent = avant;
          dire("Envoi annulé — rien n’est parti");
          return;
        }
        m.textContent = "Envoi…";
        try {
          const d = await poster("/api/envoyer/mail", {
            club_id: opts.club_id, a: opts.email, objet: opts.objet || "",
            corps: lire(), draft_id: opts.draft_id,
            tournament_id: opts.tournament_id});
          m.textContent = "✓ Envoyé à " + d.a;
          m.classList.add("fait");
          conteneur.dataset.parti = "1";
          dire("Mail parti" + _suite(d));
          fini(d);
        } catch (e) {
          m.disabled = false; m.textContent = avant;
          dire("Échec : " + e.message);
        }
      });
      conteneur.appendChild(m);
    }

    if (opts.club_id && opts.note !== false) {
      const f = el("button", "env-direct", "✓ C’est envoyé");
      f.type = "button";
      f.title = "Vous l’avez envoyé vous-même : on le note sur la fiche";
      f.addEventListener("click", async () => {
        f.disabled = true;
        try {
          const d = await poster("/api/envoyer/note", {
            club_id: opts.club_id, canal: opts.email ? "mail" : "whatsapp",
            a: opts.email || opts.tel || "", objet: opts.objet || "",
            corps: lire(), draft_id: opts.draft_id,
            tournament_id: opts.tournament_id});
          f.textContent = "✓ Noté";
          conteneur.dataset.parti = "1";
          dire("Noté sur la fiche" + _suite(d));
          fini(d);
        } catch (e) { f.disabled = false; dire("Échec : " + e.message); }
      });
      conteneur.appendChild(f);
    }
  }

  /* L'envoi reel, sans la rangee de boutons : pour les ecrans qui ont deja
     leur propre bouton. Ils ouvraient tous « wa.me » dans le navigateur, ce
     qui n'envoie rien — il fallait encore appuyer sur Entree dans WhatsApp.
     Ici le message part pour de bon, sur le clic. */
  async function direct(opts, bouton) {
    const corps = ((opts && opts.corps) || "").trim();
    if (!corps) { dire("Le message est vide"); return null; }
    if (!mobile(opts.tel)) {
      dire("Ce numéro ne passe pas sur WhatsApp");
      return null;
    }
    const avant = bouton ? bouton.textContent : null;
    if (bouton) { bouton.disabled = true; bouton.textContent = "Envoi…"; }
    try {
      const d = await poster("/api/envoyer/whatsapp", {
        club_id: opts.club_id, tel: opts.tel, corps: corps,
        draft_id: opts.draft_id, tournament_id: opts.tournament_id});
      if (bouton) { bouton.textContent = "✓ Envoyé"; bouton.classList.add("fait"); }
      dire("Parti sur WhatsApp à " + d.a);
      return d;
    } catch (e) {
      if (bouton) { bouton.disabled = false; bouton.textContent = avant; }
      dire("Échec : " + e.message);
      return null;
    }
  }

  global.Envoyer = {poser: poser, mobile: mobile, direct: direct};
})(window);
