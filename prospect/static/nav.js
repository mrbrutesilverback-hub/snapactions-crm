/* Le menu de gauche, pose par la page elle-meme.
   Le meme fichier sert tous les ecrans : Reperage, Chercher, Studio, Semaines
   et la plateforme. Sur la plateforme, les entrees changent d'onglet sans recharger ;
   ailleurs, ce sont des liens vers la plateforme. L'entree de la page courante
   est marquee, pour qu'on sache toujours ou l'on est. */
(function () {
  /* Des traits, pas des emojis : un pictogramme en couleur dans un menu, c'est
     une barre d'outils de jeu video. Tout est en currentColor, donc tout suit
     la couleur du texte — y compris quand l'entree est active. */
  function svg(d) {
    return '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" '
      + 'stroke="currentColor" stroke-width="1.4" stroke-linecap="round" '
      + 'stroke-linejoin="round" aria-hidden="true">' + d + '</svg>';
  }
  const I = {
    loupe:     svg('<circle cx="7" cy="7" r="4.3"/><path d="M10.2 10.2 14 14"/>'),
    appareil:  svg('<path d="M1.8 5.2h2.6l1-1.6h5.2l1 1.6h2.6v7.4H1.8z"/>'
                   + '<circle cx="8" cy="8.6" r="2.4"/>'),
    calendrier: svg('<rect x="1.8" y="3" width="12.4" height="11.2" rx="1.6"/>'
                   + '<path d="M1.8 6.4h12.4M5 1.6v2.6M11 1.6v2.6"/>'),
    liste:     svg('<path d="M2.4 4h11.2M2.4 8h11.2M2.4 12h7.2"/>'),
    jour:      svg('<rect x="1.8" y="3" width="12.4" height="11.2" rx="1.6"/>'
                   + '<path d="M1.8 6.4h12.4M5 1.6v2.6M11 1.6v2.6"/>'
                   + '<circle cx="8" cy="10.2" r="1.3" fill="currentColor" stroke="none"/>'),
    bulle:     svg('<path d="M14 7.6c0 3-2.7 5.4-6 5.4-.8 0-1.6-.1-2.3-.4L2.4 13.8l1-2.7'
                   + 'A5.1 5.1 0 0 1 2 7.6C2 4.6 4.7 2.2 8 2.2s6 2.4 6 5.4z"/>'),
    ecusson:   svg('<path d="M8 1.8 13.4 4v4c0 3.2-2.3 5.3-5.4 6.2C4.9 13.3 2.6 11.2 2.6 8V4z"/>'),
    coupe:     svg('<path d="M4.4 2.2h7.2v3.6a3.6 3.6 0 0 1-7.2 0z"/>'
                   + '<path d="M4.4 3.4H2.6v1a2 2 0 0 0 1.8 2M11.6 3.4h1.8v1a2 2 0 0 1-1.8 2"/>'
                   + '<path d="M8 9.4v2.8M5.6 13.8h4.8"/>'),
    gens:      svg('<circle cx="6" cy="6" r="2.3"/><path d="M2.2 13.4c0-2.2 1.7-3.6 3.8-3.6'
                   + 's3.8 1.4 3.8 3.6"/><path d="M10.6 4.2a2.3 2.3 0 0 1 0 4.4'
                   + 'M11.4 10.2c1.4.4 2.4 1.6 2.4 3.2"/>'),
    porte_voix: svg('<path d="M2.2 6.4v3.2a1 1 0 0 0 1 1h1.6L10.4 14V2L4.8 5.4H3.2'
                   + 'a1 1 0 0 0-1 1z"/><path d="M12.6 5.6a3.4 3.4 0 0 1 0 4.8"/>'),
    maillon:   svg('<path d="M6.6 9.4a2.8 2.8 0 0 0 4 0l2-2a2.8 2.8 0 0 0-4-4l-1 1"/>'
                   + '<path d="M9.4 6.6a2.8 2.8 0 0 0-4 0l-2 2a2.8 2.8 0 0 0 4 4l1-1"/>'),
    etincelle: svg('<path d="M8 1.8 9.4 6.6 14.2 8 9.4 9.4 8 14.2 6.6 9.4 1.8 8 6.6 6.6z"/>'),
    affiche:   svg('<rect x="3" y="1.8" width="10" height="12.4" rx="1.2"/>'
                   + '<path d="M5.4 5h5.2M5.4 7.6h5.2M5.4 10.2h3"/>'),
    roue:      svg('<circle cx="8" cy="8" r="2.3"/><path d="M8 1.6v1.8M8 12.6v1.8'
                   + 'M1.6 8h1.8M12.6 8h1.8M3.5 3.5l1.3 1.3M11.2 11.2l1.3 1.3'
                   + 'M12.5 3.5l-1.3 1.3M4.8 11.2l-1.3 1.3"/>'),
  };

  /* Reperage reste en tete quoi qu'il arrive : c'est la ou Julien commence
     sa journee. Les autres se rangent ensuite par usage. */
  const EPINGLES = ["simple.html"];
  const PAGES = [
    ["/", I.loupe, "Repérage"],
    ["/repondre.html", I.bulle, "Répondre", "repondre", "repondreBadge"],
    ["/rencontres.html", I.gens, "Rencontres", "rencontres", "rencontresBadge"],
    ["/chercher.html", I.etincelle, "Chercher"],
    ["/studio.html", I.appareil, "Studio"],
    ["/creux.html", I.calendrier, "Semaines à remplir"],
    ["/equipe.html", I.gens, "Dispatch"],
  ];
  const ONGLETS = [
    ["today", I.liste, "Aujourd’hui",  null, null],
    ["dates", I.jour, "Mes dates",   "dates", "datesBadge"],
    ["clubs", I.ecusson, "Clubs",       null, null],
    ["tournois", I.coupe, "Tournois",    null, null],
  ];

  const surPlateforme = /index\.html$/.test(location.pathname)
    || location.pathname === "/index.html";
  const ici = location.pathname === "/" ? "/" : location.pathname;

  function el(balise, classe, texte) {
    const e = document.createElement(balise);
    if (classe) e.className = classe;
    if (texte != null) e.textContent = texte;
    return e;
  }

  function ligne(ico, mot, cleBadge, idBadge) {
    const a = document.createElement(surPlateforme ? "button" : "a");
    if (surPlateforme) a.type = "button";
    const p = el("span", "m-ico"); p.innerHTML = ico; a.appendChild(p);
    a.appendChild(el("span", "m-mot", mot));
    if (cleBadge) {
      const n = el("span", "m-n", "0");
      n.hidden = true;
      n.dataset.badge = cleBadge;
      /* On reprend les identifiants que le reste de l'application connait
         deja : ainsi les ecrans continuent de mettre leurs compteurs a jour
         sans qu'une seule ligne d'app.js ait a changer. */
      if (idBadge) n.id = idBadge;
      a.appendChild(n);
    }
    return a;
  }

  const menu = el("nav", null);
  menu.id = "menu";
  menu.setAttribute("aria-label", "Navigation");

  const marque = document.createElement("a");
  marque.className = "marque-menu";
  marque.href = "/";
  /* Deux logos, un par theme : l'encre noire disparait sur fond sombre.
     Theme bitchat : la meme marque, recoloree en vert (fonce en clair, vif en
     sombre). Les originaux restent dans img/marque-clair.png et -sombre.png. */
  ["clair", "sombre"].forEach(ton => {
    const logo = document.createElement("img");
    logo.src = "/img/marque-vert-" + ton + ".png";
    logo.alt = "";
    logo.className = "logo-" + ton;
    marque.appendChild(logo);
  });
  marque.appendChild(el("b", null, "SnapActions"));
  menu.appendChild(marque);

  menu.appendChild(el("h3", null, "Les écrans"));
  PAGES.forEach(([href, ico, mot, badge, idBadge]) => {
    const a = document.createElement("a");
    a.href = href;
    const p = el("span", "m-ico"); p.innerHTML = ico; a.appendChild(p);
    a.appendChild(el("span", "m-mot", mot));
    /* « Répondre » porte un compteur : c'est la seule entree dont le chiffre
       represente de l'argent deja gagne et pas encore encaisse. */
    if (badge) {
      const n = el("span", "m-n", "0");
      n.hidden = true;
      n.dataset.badge = badge;
      if (idBadge) n.id = idBadge;
      a.appendChild(n);
    }
    if (href === ici) a.classList.add("on");
    a.dataset.section = "ecrans";
    /* « Repérage » a pour adresse « / », qui sert simple.html : c'est ce
       nom-la que le serveur compte. Sans cette ligne il restait a zero. */
    a.dataset.ecran = href === "/" ? "simple.html"
                                   : (href.replace(/^\//, "") || "index.html");
    menu.appendChild(a);
  });

  menu.appendChild(el("h3", null, "Le suivi"));
  ONGLETS.forEach(([cle, ico, mot, badge, idBadge]) => {
    const n = ligne(ico, mot, badge, surPlateforme ? idBadge : null);
    n.dataset.tab = cle;
    if (surPlateforme) {
      n.addEventListener("click", () => {
        if (typeof show === "function") show(cle);
        marquer(cle);
      });
    } else {
      n.href = "/index.html#/" + cle;
    }
    n.dataset.section = "suivi";
    n.dataset.ecran = cle;
    /* Un onglet ne charge pas de page : il s'annonce lui-meme, sinon il ne
       serait jamais compte et finirait en bas du menu par defaut. */
    n.addEventListener("click", () => {
      try {
        fetch("/api/visites", {method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({ecran: cle})});
      } catch (e) { /* un compteur ne doit jamais gener un clic */ }
    });
    menu.appendChild(n);
  });

  /* Les gestes rares : ils n'occupent plus le haut du Reperage, ils attendent
     ici. Sur le Reperage, l'entree ouvre la boite sur place ; ailleurs, elle y
     mene et l'ouvre a l'arrivee. */
  menu.appendChild(el("h3", null, "Rapide"));
  /* Son propre ecran, avec la boite et rien d'autre. Avant, l'entree ouvrait
     une boite au milieu du Reperage : tout le reste de la page restait
     dessous et la noyait. */
  const flyer = document.createElement("a");
  flyer.href = "/flyer.html";
  const fico = el("span", "m-ico"); fico.innerHTML = I.affiche; flyer.appendChild(fico);
  flyer.appendChild(el("span", "m-mot", "Déposer un flyer"));
  if (ici === "/flyer.html") flyer.classList.add("on");
  menu.appendChild(flyer);

  /* Relire le journal WhatsApp. C'est le geste qui fait entrer les nouvelles
     conversations et les nouveaux numeros : il tourne deja au scan du matin,
     mais Julien rentre souvent d'un tournoi en pleine journee et ne veut pas
     attendre demain. Il lit, il n'envoie rien. */
  const sync = document.createElement("button");
  sync.type = "button";
  sync.id = "menuSyncWa";
  const sico = el("span", "m-ico"); sico.innerHTML = I.bulle; sync.appendChild(sico);
  const smot = el("span", "m-mot", "Sync WhatsApp"); sync.appendChild(smot);
  sync.addEventListener("click", async () => {
    if (sync.disabled) return;
    sync.disabled = true;
    const avant = smot.textContent;
    smot.textContent = "Je relis\u2026";
    try {
      const r = await fetch("/api/whatsapp/relire",
        {method: "POST", headers: {"Content-Type": "application/json"},
         body: "{}"});
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d.error || ("le serveur a refus\u00e9 (" + r.status + ")"));
      const neufs = d.crees || 0;
      smot.textContent = d.vus != null
        ? (neufs ? d.vus + " lues \u00b7 " + neufs + " neuve"
                     + (neufs > 1 ? "s" : "")
                 : d.vus + " lues \u00b7 rien de neuf")
        : "Relu";
      /* Les ecrans qui montrent ces donnees se remettent a jour tout seuls. */
      setTimeout(() => { window.location.reload(); }, 1200);
    } catch (e) {
      smot.textContent = "\u00c9chec \u2014 " + String(e.message).slice(0, 40);
      setTimeout(() => { smot.textContent = avant; sync.disabled = false; }, 4000);
    }
  });
  menu.appendChild(sync);

  const pied = el("div", "pied");
  const reg = document.createElement(surPlateforme ? "button" : "a");
  if (surPlateforme) {
    reg.type = "button";
    reg.addEventListener("click", () => { if (typeof show === "function") show("reglages"); marquer("reglages"); });
  } else {
    reg.href = "/index.html#/reglages";
  }
  reg.dataset.tab = "reglages";
  const rico = el("span", "m-ico"); rico.innerHTML = I.roue; reg.appendChild(rico);
  reg.appendChild(el("span", "m-mot", "Réglages"));
  pied.appendChild(reg);

  const theme = document.createElement("button");
  theme.type = "button";
  theme.id = "themeBtn";
  theme.setAttribute("aria-label", "Thème");
  pied.appendChild(theme);
  menu.appendChild(pied);

  /* Le theme. Sur la plateforme, app.js etiquette et branche ce bouton ;
     ailleurs il restait vide, et le choix fait la-bas ne suivait pas : les
     autres ecrans n'obeissaient qu'au reglage du Mac. On voyait une page en
     sombre, la suivante en clair. Meme cycle, meme memoire (« theme » dans
     localStorage) : le meme theme partout. */
  const THEMES = [["system", "Système", "◐"], ["light", "Clair", "☀"],
                  ["dark", "Sombre", "☾"]];
  function themeLu() {
    try { return localStorage.getItem("theme") || "system"; } catch (e) { return "system"; }
  }
  function themePose(mode) {
    const racine = document.documentElement;
    if (mode === "system") delete racine.dataset.theme;
    else racine.dataset.theme = mode;
    const choix = THEMES.find(t => t[0] === mode) || THEMES[0];
    theme.textContent = choix[2];
    theme.title = "Thème : " + choix[1] + " — cliquez pour changer";
  }
  if (!surPlateforme) {
    theme.addEventListener("click", () => {
      const i = THEMES.findIndex(t => t[0] === themeLu());
      const mode = THEMES[(i + 1) % THEMES.length][0];
      try {
        if (mode === "system") localStorage.removeItem("theme");
        else localStorage.setItem("theme", mode);
      } catch (e) { /* navigation privee : le theme vaut pour cette page */ }
      themePose(mode);
    });
    themePose(themeLu());
  }
  /* Un autre onglet a change le theme : celui-ci suit sans attendre d'etre
     recharge. */
  window.addEventListener("storage", e => {
    if (e.key === "theme" || e.key === null) themePose(themeLu());
  });

  function marquer(cle) {
    menu.querySelectorAll("[data-tab]").forEach(
      b => b.classList.toggle("on", b.dataset.tab === cle));
  }

  /* On pose le menu TOUT DE SUITE si le corps de page existe deja : app.js
     s'execute a la suite et cherche #themeBtn, #suiviBadge et les autres des
     sa premiere ligne. Attendre DOMContentLoaded les lui aurait fait trouver
     vides, et la page partait en erreur avant d'avoir affiche quoi que ce
     soit. */
  var pose = false;
  function poser() {
    if (pose) return;
    pose = true;
    document.body.classList.add("a-menu");
    document.body.insertBefore(menu, document.body.firstChild);
    if (surPlateforme) {
      marquer((location.hash.match(/^#\/(\w+)/) || [])[1] || "today");
      window.addEventListener("hashchange", () =>
        marquer((location.hash.match(/^#\/(\w+)/) || [])[1] || "today"));
    }
    compter();
  }

  /* Les compteurs viennent du serveur, pas de l'ecran : ils doivent etre les
     memes depuis le Reperage, le Studio ou la plateforme. */
  async function compter() {
    try {
      const r = await fetch("/api/badges");
      if (!r.ok) return;
      const d = await r.json();
      menu.querySelectorAll("[data-badge]").forEach(n => {
        const v = d[n.dataset.badge] || 0;
        n.hidden = !v;
        n.textContent = v;
      });
    } catch (e) { /* serveur muet : le menu reste, sans chiffres */ }
  }

  /* Le menu range par usage. On le batit d'abord dans l'ordre ecrit — la
     page ne doit pas attendre un appel reseau — puis on le reordonne quand
     les chiffres arrivent.

     Deux precautions pour qu'il ne danse pas sous les doigts :
     le tri est STABLE (a egalite, l'ordre ecrit tient), et il ne s'applique
     qu'au-dela de quelques ouvertures — sinon un seul clic ferait remonter
     un ecran au sommet. */
  const SEUIL_TRI = 3;

  async function ranger() {
    let comptes = {};
    try {
      const r = await fetch("/api/visites");
      if (!r.ok) return;
      comptes = (await r.json()).comptes || {};
    } catch (e) { return; }

    const total = Object.keys(comptes).reduce((s, k) => s + comptes[k], 0);
    if (total < SEUIL_TRI) return;        /* pas encore de quoi juger */

    ["ecrans", "suivi"].forEach(section => {
      const liens = Array.from(
        menu.querySelectorAll('[data-section="' + section + '"]'));
      if (liens.length < 2) return;
      const ancre = liens[liens.length - 1].nextSibling;
      const rang = new Map(liens.map((n, i) => [n, i]));
      liens.sort((a, b) => {
        const ea = EPINGLES.indexOf(a.dataset.ecran);
        const eb = EPINGLES.indexOf(b.dataset.ecran);
        if (ea !== eb) {                   /* un epingle passe devant tout */
          if (ea === -1) return 1;
          if (eb === -1) return -1;
          return ea - eb;
        }
        const diff = (comptes[b.dataset.ecran] || 0) - (comptes[a.dataset.ecran] || 0);
        return diff !== 0 ? diff : rang.get(a) - rang.get(b);
      });
      liens.forEach(n => menu.insertBefore(n, ancre));
    });
  }

  if (document.body) poser();
  else document.addEventListener("DOMContentLoaded", poser);

  ranger();

  window.__menuCompter = compter;
})();
