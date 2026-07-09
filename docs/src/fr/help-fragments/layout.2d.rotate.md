<!-- review-status: pending -->

## Layout — Mode rotation (2D)

Vous êtes en mode **Rotation** sur une vue de disposition 2D (Face /
Dessus / Côté). Tirer l'anneau coloré du gizmo de rotation fait
pivoter le projecteur sélectionné autour de cet axe.

### Lire le gizmo

- La sphère englobante du projecteur sélectionné montre trois anneaux
  colorés :
  - **Rouge** = tangage/tilt (autour de X monde).
  - **Vert** = roulis (autour de Y monde, l'avant-scène).
  - **Bleu** = lacet / pan (autour de Z monde, la verticale de
    scène).
- La vue courante (Face / Dessus / Côté) détermine quel anneau est le
  plus ergonomique — les autres sont en raccourci de perspective.
  Changez de vue pour saisir un autre axe.
- Un petit anneau boussole sous le gizmo montre l'angle absolu
  courant pour l'anneau actif. Glissez au-delà pour lire le nouvel
  angle numériquement.

### Changer d'outil

- Appuyez sur **R** à tout moment pour entrer en Rotation.
- Appuyez sur **M** ou **G** pour revenir au Déplacement.
- Ou cliquez sur les boutons **Move** / **Rotate** en haut de la
  barre d'outils du Layout.

### Conventions de signe

Enregistré sous `rotation = [rx, ry, rz]` en degrés, repère de scène,
Z vers le haut (selon CLAUDE.md et #586/#600) :

- `rx > 0` vise **vers le bas** (l'axe avant du projecteur bascule
  vers `-Z` scène).
- `ry > 0` fait rouler l'image dans le sens horaire vue de derrière.
- `rz > 0` vise vers `+X`.

Le dialogue d'édition de projecteur de l'onglet Configuration utilise
des libellés opérateur (« Tilt, Roll, Pan ») avec **Tilt = -rx**, de
sorte qu'un tilt positif = au-dessus de l'horizon (#783, #788).

### Pièges

- Les vues 2D ne permettent pas d'orbiter ; si l'anneau du gizmo est
  vu par la tranche, passez à une autre vue 2D ou à la vue 3D pour le
  saisir.
- Pour de très petits ajustements d'angle, maintenez **Maj** pendant
  le glissement — le pas du gizmo est réduit 10× pour un réglage
  sous le degré.

**Plus d'infos →** chapitre 5, *Disposition du plateau*.
