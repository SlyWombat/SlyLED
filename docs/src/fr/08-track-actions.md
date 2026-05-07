<!-- review-status: pending -->

## 8. Action de suivi

L'action de suivi (type 18) est le pont entre l'onglet Objets
(chapitre 6) et le rig de projecteurs motorisés : tant qu'un clip
contenant une action de suivi est en lecture, chaque projecteur
motorisé assigné calcule sa visée pan / tilt à partir de la
position en direct d'une ou plusieurs cibles, à chaque frame
(40 Hz). Quand le clip se termine — ou que le dernier clip
référençant l'action se termine — les projecteurs motorisés assignés
se garent sur leur pose Home (#807).

### Comment se déroule une action de suivi

1. Placez les objets mobiles à poursuivre dans l'onglet
   **Disposition / Objets** (chapitre 6) — accessoires, objets
   personnalisés mobiles, cibles ruban, ou personnes détectées par
   caméra arrivant comme objets temporels.
2. Créez une action de suivi dans l'onglet **Actions**. Définissez
   son nom, sa portée et sa liste de cibles.
3. Déposez un clip sur une piste de la **Chronologie** qui
   référence l'action de suivi.
4. Pendant l'exécution, la boucle DMX 40 Hz lit la position
   actuelle de chaque cible, choisit (ou assigne) un ou plusieurs
   projecteurs motorisés, exécute l'IK canonique du vecteur de
   visée (#806 / #809), et écrit pan / tilt dans le tampon
   d'univers. Le cône de la visualisation 3D est piloté depuis le
   même vecteur de visée — la visualisation correspond donc
   toujours au comportement physique.

### Algorithme d'assignation

L'assignation est recalculée à chaque frame depuis
`trackFixtureIds` (les projecteurs motorisés en portée) et la liste
de cibles résolue :

| Situation | Comportement |
| --- | --- |
| Nombre égal de projecteurs motorisés et d'objets | Correspondance 1 : 1 par index |
| Plus de projecteurs que d'objets, **trackAutoSpread = false** | Chaque objet est poursuivi par exactement un projecteur ; les projecteurs surnuméraires restent inactifs (allumés mais sans cible). |
| Plus de projecteurs que d'objets, **trackAutoSpread = true** | Répartit les projecteurs sur la largeur de chaque objet — utile pour laver une seule cible avec plusieurs projecteurs. |
| Plus d'objets que de projecteurs, défaut | Les projecteurs cyclent à travers les objets ; chacun saute sur une cible différente toutes les `trackCycleMs` (par défaut 2000 ms). |
| Plus d'objets que de projecteurs, **trackFixedAssignment = true** | Chaque projecteur s'accroche à une cible par index ; les surnuméraires sont ignorés jusqu'à ce qu'un projecteur se libère. |

Les décalages par projecteur (`trackFixtureOffsets`) permettent à
des rigs asymétriques de viser chaque projecteur sur un point
légèrement différent de la même cible — par exemple, un projecteur
côté cour vise la tête de l'artiste tandis qu'un projecteur côté
jardin vise les pieds.

### Éditeur d'action — section Avancé (#811)

Les volets DMX Scene de l'éditeur d'action regroupent les champs
simples (nom, portée, RVB, gradateur) en haut, avec une section
extensible **Avancé** en dessous contenant les contrôles fins par
action. Pour les actions de suivi, la section Avancé expose :

| Champ | Ce qu'il contrôle | Plage |
| --- | --- | --- |
| **Cycle Time (ms)** | `trackCycleMs` — utilisé seulement quand il y a plus d'objets que de projecteurs motorisés | 100 – 10 000 ms (borné) |
| **Offset X / Y / Z (mm)** | Décalage de visée global ajouté à chaque cible. Utile pour « viser 30 cm au-dessus du sol où se trouve le marqueur » ou « viser à hauteur de tête plutôt qu'au centre de masse ». | ± 10 000 mm |
| **Auto-spread** | Active `trackAutoSpread` pour le cas plus-de-projecteurs-que-d'objets | — |
| **Fixed assignment** | Active `trackFixedAssignment` — désactive le cyclage et accroche chaque projecteur à une cible | — |
| **Track dimmer** | `trackDimmer` — surcharge le gradateur des projecteurs pendant que l'action de suivi est active. Vaut 255 (plein) par défaut ; les opérateurs le baissent parfois pour que le faisceau suiveur n'écrase pas le reste du rig. | 0 – 255 |

### Sélection des objets cibles

| Champ | Effet |
| --- | --- |
| **`trackObjectIds`** | Liste explicite d'ID d'objets à suivre. L'emporte sur `trackObjectType` quand renseigné. Cherche dans l'**ensemble** des objets mobiles (y compris les accessoires de patrouille) ; un id d'accessoire de patrouille `on-demand` se résout donc proprement ici. |
| **`trackObjectType`** | Filtre les objets mobiles par type — par exemple `"prop"`, `"ribbon-target"`, `"custom"`. Utile quand l'ensemble cible est « toutes les cibles ruban » sans lister les ID. |
| **`trackMode`** | Consulté seulement quand `trackObjectIds` et `trackObjectType` sont tous deux vides. `"camera-moving"` *(défaut)* — suivre seulement les personnes détectées par caméra (objets temporels). `"all-moving"` — suivre les accessoires de patrouille **et** les détections caméra ensemble. |

Si une action de suivi a un `trackObjectIds` explicite mais que tous
les objets listés ont été supprimés, l'action ignore la frame
entièrement plutôt que d'éteindre les projecteurs assignés —
avant le correctif (v1.7.78 et plus anciennes) le cas cible-
manquante mettait à zéro le gradateur de chaque projecteur en
portée.

### Coopération avec les spectacles et les télécommandes (#763 / #835)

Les actions de suivi sont des citoyens à part entière de la
chronologie de spectacle :

- Une action de suivi orpheline (qui vit dans `_actions` mais
  qu'aucun clip de la chronologie en cours ne référence)
  **n'évalue pas** (#835). Cela évite qu'une action préréglée
  laissée éteigne des projecteurs sur des chronologies sans
  rapport.
- Quand une télécommande (téléphone Android ou palet gyro)
  revendique un projecteur via l'arbitre de revendication
  mover-control (chapitre Télécommande), le projecteur revendiqué
  est silencié de l'action de suivi pour la durée de la
  revendication — les gestes opérateur priment sur le spectacle.
  Le relâchement de la revendication remet le projecteur dans le
  spectacle sans précalcul.
- Les actions de suivi écrivent le vecteur de visée canonique via
  le même chemin `_set_canonical_aim_stage` que le reste de
  l'orchestrateur ; un étalonnage en cours pendant un spectacle
  piloté par suivi observe donc la vraie direction du projecteur
  sans aller-retour d'IK inverse.

---

