<!-- review-status: pending -->

## Annexe E — Télécommande : téléphone Android et palet gyro

Deux télécommandes peuvent piloter des projecteurs motorisés en
direct en parallèle d'un spectacle en cours : un téléphone Android
exécutant l'application opérateur SlyLED et un palet gyro Waveshare
ESP32-S3 à écran rond. Tous deux passent par le même arbitre de
revendication sur l'orchestrateur, suivent le même protocole de
poignée de main et coopèrent avec la chronologie de spectacle via
l'arbitre de revendication mover-control. Cette annexe décrit le
cycle de vie complet, les gestes, et l'interaction entre arbitrage
de revendication et spectacles préréglés.

### Cycle de vie d'une revendication

La revendication est le verrou « cette télécommande possède
actuellement le projecteur N » de l'orchestrateur. Elle porte un
nonce 16 bits, un TTL et une pose courante, ce qui permet au
système de réconcilier l'état UI du palet avec l'état arbitre de
l'orchestrateur quand l'un ou l'autre redémarre ou perd un paquet.

```
1. IDLE sur la télécommande.
2. L'opérateur appuie sur Démarrer (palet) ou Revendiquer (Android).
3. La télécommande émet CMD_GYRO_START / une requête de
   revendication avec un nouveau nonce 16 bits.
4. L'orchestrateur alloue un projecteur motorisé, répond par un
   CLAIM_ACK avec le nonce + le moverId assigné. La télécommande
   avance l'UI vers ACTIVE seulement sur un ACK correspondant ;
   CLAIM_DENIED revient en arrière ; un timeout d'environ 1,5 s
   revient avec « PAS DE RÉPONSE ».
5. La télécommande envoie des quaternions d'orientation à ~50 Hz ;
   l'orchestrateur les convertit en aim-stage et écrit pan / tilt
   sur la tête.
6. Les deux extrémités échangent des battements de cœur 2 s
   (HB_REP porte uiState + claimNonce + seq) pour réconcilier les
   états divergents.
7. L'opérateur appuie sur Stop / Relâcher ; la télécommande envoie
   le nonce ; l'orchestrateur répond par STOP_ACK et relâche la
   revendication.
```

La spec complète de la machine d'état vit dans
`docs/gyro-claim-lifecycle.md` et fait foi pour toute modification
du protocole.

#### Ce que voit l'opérateur

- **Appui sur Démarrer sur le palet** — la page avance vers
  « ACTIVE » en ~150 ms. Si l'orchestrateur ne peut revendiquer
  aucun projecteur (aucun en ligne, aucun disponible), la page
  revient à IDLE avec une raison de refus.
- **Appui sur Stop sur le palet** — la page revient à IDLE ; la
  tête revient à ce que pilotait le spectacle (ou se gare si
  aucun spectacle ne joue).
- **Étalonnage** — maintenez le bouton **Étalonner** (palet ou
  Android) aussi longtemps que nécessaire ; relâchez pour
  capturer la nouvelle pose de référence. L'écran avance vers la
  page sélecteur de couleurs sur le palet ; l'application
  Android avance vers la page gestes.
- **Connexion perdue** — les deux télécommandes affichent un
  badge stale-reason si l'orchestrateur cesse d'entendre les
  battements de cœur. Le palet s'auto-efface quand il reprend
  l'envoi (#812 / #821 / #823) ; l'opérateur peut aussi forcer
  l'effacement via `POST /api/remotes/<id>/clear-stale`.

### Gestes

Une fois actives, les deux télécommandes pilotent la même
sémantique `aim_stage` — le faisceau de la tête vise un point en
coordonnées plateau calculé à partir de l'orientation de la
télécommande.

#### Téléphone (Android)

- **Pitch** (incliner le téléphone vers l'avant / arrière) — le
  faisceau monte / descend sur la tête.
- **Roll** (incliner le téléphone gauche / droite) — le faisceau
  pan à travers le plateau.
- **Yaw** (tourner le téléphone autour de la verticale) — le
  faisceau pan à travers le plateau.
- **Boutons de volume** — gradateur fin haut / bas (configurable
  dans l'app opérateur Android).
- **Auto-luminosité** (chapitre Luminosité) — l'app peut piloter
  la luminosité maître de l'orchestrateur depuis l'enveloppe du
  micro local à ~20 Hz, avec mise à l'échelle gamma sur le rig
  (#820, #843).

L'axe yaw spécifique au téléphone est inversé par rapport au palet
(#824) parce que l'orientation portrait naturelle du téléphone
place la « gauche » de l'opérateur à 90 ° du repère du palet.
L'opérateur n'a jamais à y penser ; le `_apply_quat` de
l'orchestrateur pour `KIND_PHONE` gère la négation.

#### Palet gyro

- **Pitch** (incliner le palet vers l'avant / arrière) — le
  faisceau monte / descend.
- **Yaw** (tourner autour de l'axe vertical du palet) — le
  faisceau pan.
- **Roll** (incliner gauche / droite) — sélection d'emplacement
  de roue de couleurs sur les préréglages avec roue ; ignoré sur
  les préréglages RVB seuls.
- **Appui sur Démarrer** — revendiquer un projecteur motorisé et
  démarrer l'envoi.
- **Appui sur Stop** — relâcher la revendication.
- **Appui sur Étalonner** — capturer une nouvelle pose de
  référence.

### Arbitrage de revendication avec les spectacles (#763)

Les revendications priment sur la chronologie de spectacle :

- Une tête revendiquée est **silenciée** de
  `_evaluate_track_actions` et des écritures
  `set_fixture_dimmer` / `set_fixture_pan_tilt` pilotées par le
  précalcul. L'écrivain de revendication possède la tête jusqu'au
  relâchement.
- Les autres têtes du rig continuent à jouer le spectacle
  normalement — la revendication n'affecte que le projecteur
  assigné.
- Au relâchement, la tête **rejoint le spectacle en une frame** :
  pas de lissage, pas de fondu. Si le spectacle a avancé, la
  tête saute là où le spectacle est actuellement. C'est un choix
  délibéré de #763 — le lissage en retour sortait l'opérateur du
  moment ; le retour instantané est ce que font les vraies
  consoles.
- Les actions de suivi évaluent à chaque frame ; une tête qui
  était revendiquée pendant un balayage retourne donc là où le
  balayage est **en ce moment**, pas là où il aurait été en cours
  de revendication.

### Couleur et gradateur pendant une revendication (#814)

Une revendication ne reprend pas la couleur ni le gradateur :

- Les gestes de la télécommande pilotent **uniquement pan / tilt**
  (et la roue de couleurs pour l'axe roll du palet sur les
  préréglages qui le supportent).
- Le gradateur et le RVB de la tête restent sous le contrôle du
  spectacle. Si le spectacle est sombre, la tête revendiquée
  reste sombre — l'opérateur choisit pan et tilt ; le spectacle
  peint la couleur et l'intensité.
- C'est valable pour la luminosité globale (#843) de la même
  manière : une revendication pendant l'Auto-luminosité hérite du
  maître piloté en automatique.

### Récupération depuis un état divergent

Les battements de cœur du protocole incluent l'état des deux
extrémités, les combinaisons divergentes sont donc réconciliées :

| UI palet | Orchestrateur | Comportement |
| --- | --- | --- |
| ACTIVE | revendication tenue | Normal — les battements de cœur gardent le TTL en vie. |
| ACTIVE | aucune revendication | L'orchestrateur reconstruit la revendication (chemin de bootstrap après redémarrage de l'orchestrateur). |
| IDLE | revendication tenue | Revendication orpheline — l'orchestrateur la relâche. |
| IDLE | aucune revendication | Repos normal. |

La garde anti-revendication-orpheline se déclenche 1,5 s après
CLAIM_ACK si aucune orientation n'arrive, relâchant la
revendication pour qu'une télécommande figée ne puisse pas
squatter un projecteur motorisé indéfiniment.

---

