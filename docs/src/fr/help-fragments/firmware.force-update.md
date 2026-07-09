<!-- review-status: pending -->

## Mise à jour forcée (Force Update)

Le bouton **Force Update** lance un flash OTA par HTTP même quand la
carte est signalée hors ligne. Utilisez-le pour récupérer une carte
dont le battement PONG n'atteint plus l'orchestrateur mais dont le
serveur HTTP écoute toujours.

### Quand l'utiliser plutôt que le bouton Update normal

- **Update** (par défaut) — affiché seulement quand l'orchestrateur a
  vu un PONG récent et que la version de micrologiciel rapportée par
  la carte est plus ancienne que la version épinglée au registre.
  C'est le chemin normal.
- **Force Update** — affiché quand l'IP de la carte est connue mais
  que l'orchestrateur n'a pas reçu de PONG dans le délai hors-ligne.
  Force un POST HTTP vers `http://<ip>/ota` avec le binaire du
  registre, en contournant la vérification de fraîcheur dérivée de
  l'UDP.

### Pourquoi l'égalité de version est le défaut

L'OTA est toujours épinglé sur la version du `firmwareId` de l'entrée
de registre. Si la carte rapporte déjà la version épinglée, aucun
téléversement n'a lieu — l'orchestrateur renvoie
`{ok:true, alreadyAtVersion:true}` au lieu de reflasher. Les clics
répétés restent ainsi sans danger (impossible de reflasher par
accident une carte saine) et on évite la petite fenêtre de défaillance
où un redémarrage en plein flash brique l'appareil.

Force Update applique la même vérification SHA-256 + taille (#873)
sur le cache local avant de servir le binaire — un fichier en cache
corrompu ou discordant est refusé avec une erreur claire plutôt que
poussé sur l'appareil.

### Quand Force Update ne servira à rien

- L'IP de la carte est inconnue (aucune entrée ARP récente). Utilisez
  plutôt la carte **Flash USB**.
- La carte est bloquée à l'invite du chargeur d'amorçage sans
  application fonctionnelle. Le flash USB via `esptool` est la seule
  voie.
- Pour le palet gyro (ESP32-S3 USB-CDC), un build coincé signifie ni
  série ni route `/ota` — l'entrée manuelle en mode bootloader par le
  bouton BOOT est requise.

**Plus d'infos →** chapitre 15, *Firmware et mises à jour OTA*.
