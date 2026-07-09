<!-- review-status: pending -->

## Charger un spectacle — Préréglages

Le dialogue **Préréglages** génère un spectacle complet à la demande à
partir d'un thème de haut niveau. Le générateur inspecte votre rig
courant (projecteurs, disposition, objets suivis par caméra) et adapte
la longueur des pistes, les types d'action et les assignations de
projecteurs pour que le résultat ne soit pas générique.

### Choisir un préréglage

Chaque carte de préréglage montre un nom de thème et une courte
description. Cliquez sur **Load** pour générer le spectacle sur
place. La chronologie courante est remplacée — sauvegardez d'abord si
vous voulez la garder.

Préréglages courants :

- **Energetic** — stroboscopes rapides, balayages de spectre vifs ;
  plusieurs pistes superposées pour que le précalcul reste chargé.
- **Ambient** — fondus de couleur lents, arcs pan/tilt doux ; une ou
  deux longues pistes plutôt que beaucoup de courtes.
- **Vertical Bar Array** — gabarit spécial pour les rigs à colonne
  verticale de barres LED ; conçu pour exploiter les métadonnées de
  position verticale des projecteurs.
- **Sequenced catalog** / **Ribbon** / **Live-track** — gabarits à
  branche thématique qui se raccordent directement au générateur au
  lieu de passer par le chemin standard
  `_generate_spatial_effects`. Voir
  `feedback_show_template_branch_pattern` pour la règle structurelle.

### Ce que le générateur inspecte

- Le nombre et les types de projecteurs (lyres, barres LED, PAR).
- La disposition — les projecteurs en bordure reçoivent un traitement
  différent de ceux du centre (p. ex. poursuites vs wash).
- Les objets **mobiles** suivis par caméra — s'il en existe, le
  générateur insère une action Track pour que les lyres désignées les
  suivent.
- Les dimensions de la scène — la durée des clips s'ajuste à la
  profondeur du plateau, donc les grands rigs reçoivent des balayages
  plus longs.

### Après le chargement

La chronologie générée arrive dans l'éditeur exactement comme si vous
l'aviez écrite à la main. Modifiez, reprécalculez et démarrez comme
d'habitude. Le précalcul n'est **pas** automatique — Load vous dépose
dans la vue chronologie pour révision, pas dans un spectacle en
cours.

**Plus d'infos →** chapitre 13, *Spectacles préréglés*.
