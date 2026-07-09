<!-- review-status: pending -->

## Action de piste (Track) — Avancé

Le volet **Avancé** d'une action Track expose les réglages de
synchronisation et de répartition que le flux simple garde masqués.
La plupart des opérateurs n'ont jamais à y toucher — les valeurs par
défaut conviennent au cas standard « suivre la personne X ».

### Temps de cycle (ms)

À quelle fréquence la lyre re-vise sa cible assignée.

- **Plus bas** (500–1000 ms) — suivi plus vif, plus nerveux. Bon pour
  les sujets rapides ou les ambiances lumineuses agressives.
- **Par défaut** (2000 ms) — fluide sur une personne qui marche ; ne
  « pompe » pas sur la gigue du détecteur de la caméra.
- **Plus haut** (5000 ms et plus) — en retard mais très doux. Utile
  pour les looks lents et ambiants où le public ne doit pas voir la
  lyre changer de cible.

### Décalage X / Y / Z (mm)

Point de visée par rapport au centroïde de la cible, en millimètres
de scène.

- Utilisez `Z = +800` pour viser la **tête** d'une personne dont le
  centroïde se trouve à hauteur de hanche (`Z ≈ 1000` pour un adulte
  debout — ajustez selon l'échelle de profondeur de votre caméra).
- Utilisez `Y = +500` pour anticiper une cible qui se déplace vers
  `+Y` (la lyre vise devant elle, ce qui paraît naturel dans un plan
  de poursuite).
- Tous les décalages sont dans le repère de la **scène**, pas celui
  du projecteur — `+X` est donc du même côté pour tous les
  projecteurs.

### Répartition automatique entre les cibles

Lorsque coché, plusieurs lyres sur la même action se répartissent
entre toutes les cibles détectées (une lyre par personne, en
rotation). Décoché = chaque lyre vise la même cible principale.

### Assignation fixe (1:1 — les cibles en surplus sont ignorées)

Lorsque coché, chaque lyre reste sur l'index de cible qui lui a été
assigné au départ et refuse de changer. Si une cible sort du champ,
la lyre garde sa dernière visée au lieu de sauter sur une autre
personne.

> Mutuellement exclusif avec la **répartition automatique** — cocher
> Assignation fixe désactive la répartition (et inversement).
> L'interface ne l'impose pas ; le moteur de précalcul traite
> l'assignation fixe comme la contrainte la plus stricte.

**Plus d'infos →** chapitre 8, *Action de piste*.
