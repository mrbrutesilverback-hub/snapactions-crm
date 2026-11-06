/* La barre de reformulation, partout ou il y a un message qu'on peut corriger.
   Le modele tourne sur ce Mac, dans Ollama : rien ne sort de la machine, rien
   n'est facture, ca marche sans internet. Et rien ne part : le texte reformule
   revient dans la case, Julien relit, et c'est lui qui envoie.

   Usage dans n'importe quelle page :
     html += Reform.barre('mon-textarea', 'US Brignoles · Tournoi U11');
     Reform.brancher(element);          // apres insertion dans le DOM  */
window.Reform = (function () {
  const VITE = [
    ['Plus court', 'Rends-le nettement plus court. Trois phrases au maximum, '
      + 'en gardant la demande et la signature.'],
    ['Plus chaleureux', 'Rends-le plus chaleureux et plus humain, comme quelqu’un '
      + 'du coin qui connaît le milieu. Sans familiarité excessive.'],
    ['Plus direct', 'Va droit au but : une seule demande claire, pas de préambule, '
      + 'pas de formule de politesse longue.'],
    ['En relance', 'Transforme-le en relance brève : on a déjà écrit une fois et '
      + 'on n’a pas eu de réponse. Courtois, sans reproche, sans tout réexpliquer.'],
  ];

  let etat = null;               // ce que le serveur dit du modele
  const avant = {};              // le texte d'avant, par case

  function ech(t) {
    return String(t == null ? '' : t).replace(/[&<>"']/g,
      c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c]));
  }

  function barre(cible, contexte) {
    const vite = VITE.map(([mot, consigne]) =>
      `<button type="button" class="ia-vite" data-ia-cible="${ech(cible)}"
        data-ia-consigne="${ech(consigne)}">${ech(mot)}</button>`).join('');
    return `<div class="ia" data-ia-boite="${ech(cible)}"
        data-ia-contexte="${ech(contexte || '')}">
      <div class="ia-boutons">${vite}</div>
      <div class="ia-ligne">
        <input type="text" class="ia-dire" data-ia-dire="${ech(cible)}"
          placeholder="ou dites comment le changer…">
        <button type="button" class="ia-go" data-ia-cible="${ech(cible)}"
          data-ia-libre="1">Reformuler</button>
        <button type="button" class="ia-annuler" data-ia-annuler="${ech(cible)}"
          hidden>Annuler</button>
      </div>
      <p class="ia-etat" data-ia-etat="${ech(cible)}"></p>
    </div>`;
  }

  async function reformuler(cible, consigne) {
    const champ = document.getElementById(cible);
    const boite = document.querySelector(`[data-ia-boite="${cible}"]`);
    if (!champ || !boite) return;
    const dit = boite.querySelector('.ia-etat');
    if (!(champ.value || '').trim()) return;
    if (!consigne) { dit.textContent = 'Dites ce qu’il faut changer.'; return; }

    boite.querySelectorAll('button').forEach(b => b.disabled = true);
    dit.className = 'ia-etat';
    dit.textContent = 'Reformulation sur votre Mac…';
    try {
      const r = await fetch('/api/mot/reformuler', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({texte: champ.value, consigne: consigne,
                              contexte: boite.dataset.iaContexte || null})});
      const d = await r.json();
      if (!r.ok) throw new Error(d.error || 'refus');
      avant[cible] = champ.value;
      champ.value = d.texte;
      dit.textContent = 'Reformulé. Relisez : rien n’est parti.';
      boite.querySelector('.ia-annuler').hidden = false;
    } catch (e) {
      dit.className = 'ia-etat souci';
      dit.textContent = e.message || 'Reformulation impossible';
    }
    boite.querySelectorAll('button').forEach(b => b.disabled = false);
  }

  function brancher(racine) {
    const r = racine || document;
    r.querySelectorAll('[data-ia-consigne]').forEach(b => b.onclick = e => {
      e.preventDefault();
      reformuler(b.dataset.iaCible, b.dataset.iaConsigne);
    });
    r.querySelectorAll('[data-ia-libre]').forEach(b => b.onclick = e => {
      e.preventDefault();
      const champ = r.querySelector(`[data-ia-dire="${b.dataset.iaCible}"]`)
        || document.querySelector(`[data-ia-dire="${b.dataset.iaCible}"]`);
      reformuler(b.dataset.iaCible, (champ && champ.value || '').trim());
    });
    r.querySelectorAll('[data-ia-annuler]').forEach(b => b.onclick = e => {
      e.preventDefault();
      const cible = b.dataset.iaAnnuler, champ = document.getElementById(cible);
      if (champ && avant[cible] != null) champ.value = avant[cible];
      b.hidden = true;
      const dit = document.querySelector(`[data-ia-etat="${cible}"]`);
      if (dit) dit.textContent = 'Message d’origine remis.';
    });
    majEtat(r);
  }

  /* Si Ollama est ferme, les boutons le disent une fois au lieu d'echouer a
     chaque clic. */
  async function majEtat(racine) {
    if (etat === null) {
      try { etat = await (await fetch('/api/mot/reformuler')).json(); }
      catch (e) { etat = {disponible: false, dit: 'état inconnu'}; }
    }
    if (etat.disponible) return;
    (racine || document).querySelectorAll('.ia').forEach(boite => {
      boite.classList.add('dormante');
      boite.querySelectorAll('button, input').forEach(el => el.disabled = true);
      const dit = boite.querySelector('.ia-etat');
      if (dit && !dit.textContent) {
        dit.textContent = (etat.dit || 'Reformulation indisponible')
          + ' Ouvrez l’application Ollama, puis rechargez la page.';
      }
    });
  }

  return {barre: barre, brancher: brancher};
})();
