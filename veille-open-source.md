# Ce qui existe déjà — veille open source

Note du 9 septembre 2026. Question posée : est-ce qu'il y a, sur GitHub, des
choses mieux faites que ce qu'on a, et qu'on pourrait reprendre ?

Réponse courte : **pas pour le CRM. Oui pour le produit.**

---

## 1. Remplacer le CRM ? Non.

### Twenty — 56 200 étoiles, TypeScript

L'alternative open source à Salesforce, très active (15 000 commits). Objets et
vues personnalisables, workflows, agents IA. Sérieux, moderne, bien fait.

Ce qu'il faudrait pour y passer : PostgreSQL, Redis, BullMQ, Docker — sur le Mac
mini, ou un serveur à louer. Et surtout : il ne sait rien faire de ce qui fait la
valeur de notre outil. Pas de veille tournois, pas de rapprochement
tournoi → club par département, pas de fenêtre J-75/J-10, pas de missions vers
les groupes WhatsApp par discipline, pas de garde-fou d'envoi. Il faudrait tout
réécrire en TypeScript dans un système générique de vente B2B.

**EspoCRM, SuiteCRM, Krayin** : même verdict, en PHP et en plus lourd.

### Ce que ça dit de notre app

Elle tourne sur le Python livré avec macOS, sans une seule dépendance à
installer, sur un fichier SQLite de 180 Ko. Ce n'est pas un manque de moyens,
c'est ce qui fait qu'elle démarre en deux secondes et qu'elle ne tombera jamais
en panne pour une histoire de version de Node. Le jour où on sera cinq à
travailler dessus en même temps, la question se reposera. Pas avant.

---

## 2. Ce qui vaut le coup d'être repris

### Chatwoot — 33 900 étoiles, licence MIT

Boîte de réception unifiée : WhatsApp, email, Instagram, Facebook, SMS,
plusieurs personnes qui traitent les conversations sans se marcher dessus.
C'est exactement le problème qu'on aura quand Jimmy et moi traiterons les
réponses en parallèle.

Deux réserves : il faut un serveur (Docker), et côté WhatsApp il se branche sur
l'API officielle de Meta — donc la migration du numéro dont on a déjà parlé,
avec la perte de l'app WhatsApp Business sur ce numéro. **À garder pour le jour
où le volume de conversations devient ingérable à deux.** Pas avant.

### Immich — 110 000 étoiles, licence AGPL-3.0

Le plus intéressant de tous, et pas pour le CRM : **pour le produit**.
Gestionnaire de photos auto-hébergé, très actif, avec reconnaissance faciale et
regroupement par personne, albums, et partage par lien public.

C'est littéralement le moteur de la « galerie intelligente » : les parents
retrouvent les photos de leur enfant. Ce serait des mois de travail économisés
sur la partie technique.

Deux points à regarder avant de s'engager :

- **La licence AGPL.** Si on construit un service en ligne dessus et qu'on
  modifie le code, on doit publier nos modifications. Ce n'est pas rédhibitoire
  — beaucoup de boîtes vivent très bien avec — mais ça se décide en connaissance
  de cause, pas après coup.
- **La reconnaissance faciale sur des mineurs.** En Europe, un gabarit facial
  est une donnée biométrique, catégorie particulière au sens du RGPD. Sur des
  enfants, c'est le terrain le plus surveillé qui soit. Le modèle actuel — la
  famille reçoit un lien privé vers la galerie de son enfant — peut très bien
  fonctionner sur le dossard, l'équipe, la séance ou l'horaire, sans jamais
  calculer d'empreinte faciale. **Ça vaut un vrai avis juridique avant
  d'activer quoi que ce soit de biométrique.** Je ne suis pas juriste.

### Les briques, si on construit la galerie nous-mêmes

- **deepface** et **InsightFace** — recherche par visage en Python, la référence.
  Mêmes réserves que ci-dessus.
- **bibnumber** — 123 étoiles, C++, lecture des numéros de dossard sur des photos
  de course. Ancien, mais l'approche est documentée : détection de texte puis
  OCR. Aujourd'hui on referait ça avec un détecteur récent plutôt qu'avec
  Tesseract. **C'est la piste pour le segment adulte** — triathlons, trails, le
  MADROQ : le dossard identifie le coureur sans toucher à son visage. Bien plus
  simple juridiquement que la reconnaissance faciale.
- **Piwigo**, **Photoview**, **LibrePhotos** — galeries auto-hébergées classiques.
  Moins avancées qu'Immich sur la recherche par personne.

---

## 3. Le constat qui compte

Il n'existe **aucun équivalent open source** du produit SnapActions : galerie
d'événement sportif + retrouver son enfant + achat par les familles. Tout ce qui
fait ça est commercial — MyRun, PhotoDay, Sportografen, Pixieset, Sunshine Photo
Cart côté WordPress.

C'est une bonne nouvelle. Ça veut dire que la valeur est dans le produit qu'on
construit, pas dans quelque chose qu'il suffirait d'installer. Et ça veut dire
que l'énergie doit aller là — pas à refaire un CRM qui marche déjà.

---

## Sources

- Twenty — https://github.com/twentyhq/twenty
- Chatwoot — https://github.com/chatwoot/chatwoot
- Immich — https://github.com/immich-app/immich
- bibnumber — https://github.com/gheinrich/bibnumber
- deepface — https://github.com/serengil/deepface
- Piwigo — https://github.com/Piwigo/Piwigo
- Photoview — https://github.com/photoview/photoview
- LibrePhotos — https://github.com/LibrePhotos/librephotos
- MyRun, service commercial équivalent — https://myrun.eu/
