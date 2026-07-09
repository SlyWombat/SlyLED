<!-- review-status: pending -->

## Contrôle de groupe de projecteurs

Le modal **Contrôle de groupe** expose des curseurs en direct pour
chaque groupe de projecteurs du rig. Déplacez un curseur et chaque
projecteur du groupe répond instantanément — pas de précalcul, pas de
chronologie.

### Qu'est-ce qu'un groupe ?

Un **groupe de projecteurs** est un enregistrement de projecteur avec
`type=group`, qui tient une liste de membres `childIds`. Créez-en un
depuis **Configuration → + Ajouter un projecteur → Groupe de
projecteurs**. Les groupes peuvent mélanger membres LED et DMX ; le
moteur transmet à chaque membre le sous-ensemble de canaux pertinent.

### Contrôles par groupe

Chaque carte affiche :

- **Gradateur** (0–255) — facteur maître pour tous les membres. Se
  multiplie avec les valeurs de gradateur / RGB propres au membre ;
  ne les remplace pas.
- **R / V / B** (0–255 chacun) — couleur globale. Régler l'un d'eux
  force le gradateur à 255 (pour que la couleur soit visible sans un
  coup de fader séparé). Glissez les trois à 0 pour mettre le groupe
  au noir.
- **Warm / Cool / Red / Off** — boutons de préréglage rapides. Utiles
  pour des ambiances d'avant-spectacle avant le lancement de la
  chronologie.

### Contrôle de groupe ou Action ?

- Utilisez le **Contrôle de groupe** pour les ajustements ponctuels
  en direct — ambiance d'avant-spectacle, répétitions, dépannage
  « ce projecteur est-il bien câblé ? ».
- Utilisez une **Action enregistrée + chronologie** pour le contenu
  de spectacle répétable. Les curseurs du Contrôle de groupe ne sont
  enregistrés nulle part ; fermer le modal laisse la dernière valeur
  sur le fil, mais le prochain précalcul l'écrase.

### Pièges

- Un groupe vide (aucun `childIds`) affiche la carte mais chaque
  curseur est sans effet. Ajoutez d'abord des membres.
- Les boutons de préréglage de couleur règlent le RGB mais
  **laissent le gradateur à 255**. Si votre groupe est au milieu
  d'un fondu et que vous tapez Red, vous pouvez écraser la rampe de
  gradation du fondu. Tapez Off pour revenir à une sortie nulle.

**Plus d'infos →** chapitre 4, *Configuration des appareils* —
« Groupes ».
