<!-- review-status: pending -->

## Moniteur DMX

Le **Moniteur DMX** est une grille en direct des 512 canaux de
l'univers sélectionné. Chaque cellule est un canal ; la valeur
s'affiche à la fois en nombre (0–255) et en intensité de couleur. Le
rafraîchissement automatique maintient la vue à ~250 ms de la sortie
de précalcul en direct.

### Lire la grille

- **Rangées** = plages d'adresses de 32 canaux chacune. La colonne la
  plus à gauche est l'adresse de départ de la rangée.
- **Colonnes** = le décalage dans la rangée (1–32). Ainsi
  `rangée 17, col 5` correspond à l'adresse `17 + 5 - 1 = 21`.
- La couleur de la cellule suit la valeur — plus clair = plus haut.
  Les cellules > 128 passent le texte en sombre pour la lisibilité.
- Le moniteur reflète le **tampon d'univers DMX post-précalcul** — ce
  qu'Art-Net s'apprête à diffuser. La mise à l'échelle Master,
  l'Auto-luminosité et la luminosité par projecteur y sont déjà
  appliquées.

### Cliquer-pour-définir

Cliquez sur n'importe quelle cellule pour définir sa valeur à la
main. Utile pour prouver un mappage de canal (mettez le canal 5 à
255, regardez le projecteur réagir, confirmez que canal 5 =
gradateur) sans créer d'action temporaire.

> Les réglages manuels sont écrasés au prochain tick de précalcul —
> c'est un banc d'essai en direct, pas un moyen de piloter un
> spectacle. Utilisez le modal de contrôle de groupe ou une action
> enregistrée pour une sortie soutenue.

### Sélecteur d'univers

Basculez entre les univers 1–4 avec la liste déroulante. Chaque
univers est son propre tampon de 512 canaux ; ils ne sont pas
aliasés. Si l'univers voulu n'apparaît pas, augmentez le nombre
d'univers dans **Paramètres → DMX**.

### Rafraîchissement automatique

La case à cocher active une boucle d'interrogation de 250 ms.
Désactivez-la quand vous parcourez des valeurs à la main et ne voulez
pas qu'elles clignotent sous vos yeux.

**Plus d'infos →** chapitre 12, *Profils d'appareils DMX* ;
chapitre 17, *Dépannage* — pour la recette « aucun canal ne
s'allume ».
