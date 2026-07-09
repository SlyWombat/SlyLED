<!-- review-status: pending -->

## Panneau de précalcul (Bake)

Le **précalcul** compile une chronologie d'actions en tampons d'étapes
par projecteur et les pousse à chaque Performer. Tant que le spectacle
n'est pas précalculé, le lecteur n'a rien à jouer.

### Ce que fait le précalcul

1. Parcourt la chronologie temps par temps, en évaluant quels clips
   sont actifs sur chaque projecteur.
2. Résout l'action de chaque clip via le moteur spatial / spectre /
   mode faisceau, produisant la sortie par étape (RGB pour les LED,
   canaux DMX 0–255 pour les projecteurs DMX).
3. Emballe les étapes de chaque projecteur au format du protocole
   filaire — pour les enfants ESP32, l'en-tête de 8 octets + les
   trames LOAD_STEP de 48 octets ; pour le DMX, les tampons de canaux
   Art-Net par tick.
4. Diffuse les paquets LOAD_STEP à chaque Performer, avec accusé
   (ACK) pour chaque trame.

### L'étape Synchroniser

Une fois le précalcul terminé, le panneau affiche un badge
**Synchroniser** vert pour chaque Performer qui a accusé réception de
toutes ses étapes. Un badge rouge avec un index d'étape signifie que
ce Performer a perdu une trame (généralement un accroc WiFi) —
relancez le précalcul pour récupérer.

### Démarrer

Quand chaque Performer est au vert, **Démarrer** émet un `RUNNER_GO`
avec l'époque de départ convenue. Chaque Performer joue le tampon
chargé en parfaite synchronie — l'orchestrateur ne cadence pas les
trames, il ne fournit que l'horloge globale.

### Pièges courants

- Un projecteur ajouté depuis le dernier précalcul **doit être
  reprécalculé** avant Démarrer, sinon il reste éteint.
- L'action `track` (#812) contourne l'exigence de précalcul — elle
  s'exécute en direct depuis la boucle principale de l'orchestrateur,
  donc modifier une action Track ne force pas de reprécalcul. Tous
  les autres types d'action, oui.
- La durée du précalcul croît avec longueur de chronologie × nombre
  de projecteurs. Une chronologie de 10 minutes sur 24 projecteurs
  prend ~5 s sur un CPU moderne ; les machines plus anciennes peuvent
  prendre plus de temps.

**Plus d'infos →** chapitre 10, *Précalcul et lecture*.
