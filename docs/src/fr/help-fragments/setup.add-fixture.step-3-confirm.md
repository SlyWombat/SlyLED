<!-- review-status: pending -->

## Ajouter un projecteur — Étape 3 : Confirmer

L'écran de révision final avant la création du projecteur. Tout ce qui
figure sur cette carte est écrit dans `fixtures.json` au clic sur
**Create Fixture**.

### Ce que vous confirmez

- **Nom** — libellé opérateur définitif.
- **Univers** + **Adresse de départ** — le patch DMX. Revérifiez avec
  le détecteur de conflits si vous êtes arrivé ici via
  Précédent/Suivant.
- **Canaux** — nombre de canaux occupés par ce projecteur.
- **Profil** — l'identifiant du profil de bibliothèque (Local /
  Communauté / OFL). Vide signifie une disposition de canaux
  générique — utile pour un projecteur unique, mais le moteur de
  précalcul ne peut pas piloter intelligemment pan/tilt/gobo/etc.
  sans métadonnées de type de canal.

### Ce qui se passe au clic sur Create Fixture

1. L'orchestrateur écrit un nouvel enregistrement de projecteur dans
   `fixtures.json`.
2. Si le profil porte des canaux **pan + tilt** (c'est une lyre),
   l'assistant propose **Set Home now** (définir la position
   d'origine). Home est la valeur DMX où le faisceau vise le long du
   vecteur de rotation du projecteur ; l'étalonnage SMART, la
   télécommande gyro / Android et la visée par XYZ l'exigent tous
   avant de démarrer.
3. L'onglet Configuration se rafraîchit — votre nouveau projecteur
   apparaît dans la liste et dans la scène 3D de l'onglet Layout à
   l'origine (glissez-le en position sur le canevas de disposition).

### Pièges

- Le projecteur démarre à l'**origine** `(0, 0, 0)` jusqu'à ce que
  vous le placiez sur le Layout. Les actions spatiales et les visées
  XYZ viseront toutes vers l'origine d'ici là. Les sorties de
  précalcul joueront quand même, mais la géométrie paraîtra fausse
  dans la prévisualisation 3D.
- Si vous sautez l'invite Home, la carte d'étalonnage de l'onglet
  Configuration continuera d'afficher « Home not set » jusqu'à ce que
  vous passiez l'assistant. Définissez Home tôt — chaque fonction
  pilotée par la visée (gyro, SMART, actions Track sur les lyres) en
  dépend.

**Plus d'infos →** chapitre 4, *Configuration des appareils* ;
annexe B, *Étalonnage des lyres*.
