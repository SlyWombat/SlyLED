<!-- review-status: pending -->

## Mises à jour OTA

Les mises à jour OTA (over-the-air, « par les airs ») poussent le
binaire épinglé au registre vers chaque carte Performer par HTTP, sans
câble USB.

### Fonctionnement

1. Chaque Performer rapporte sa version de micrologiciel courante à
   chaque cycle PING/PONG.
2. L'orchestrateur la compare à l'entrée de registre épinglée pour
   cette piste de carte (p. ex. `child-led-esp32 v7.5.11`,
   `esp32s3-gyro-firmware v1.2.6`).
3. Les rangées périmées affichent un bouton **Update** (v1.7.119 :
   badge orange « vX available »). Cliquez dessus ; l'orchestrateur
   sert `http://<orchestrateur>/api/firmware/serve/<carte>` et
   l'appareil récupère puis flashe via Arduino-OTA.
4. Après le flash, la carte redémarre et re-PONG avec la nouvelle
   version. La rangée repasse au badge vert « Up to date ».

### Changements v1.7.119

- **Actifs OTA application seule** (#870, #874). L'orchestrateur ne
  sert plus que le binaire de l'application — pas une image fusionnée
  bootloader/partitions/app. C'est ce que le gestionnaire OTA de
  chaque carte a toujours attendu ; l'image fusionnée était une
  erreur ponctuelle qui briquait des cartes en pleine mise à jour sur
  les flottes plus anciennes.
- **Épinglage otaSha256** (#873). Chaque entrée de registre porte le
  SHA-256 du binaire canonique. Le proxy vérifie l'empreinte du
  fichier en cache avant chaque service ; une discordance renvoie
  HTTP 502 au lieu de pousser une mise à jour corrompue.
- **Extraction START du gyro** (#874). Le point d'accès OTA extrait
  désormais exactement la partition application de l'artefact de
  build fusionné sur le serveur de build, de sorte que ce qui atterrit
  dans le cache du registre correspond à ce que le gestionnaire OTA
  du gyro peut accepter.

### Forcer la fraîcheur OTA

Cliquez sur **Check for Updates** en haut de l'onglet Firmware pour
forcer une récupération de la liste des releases — utile quand vous
venez de publier une nouvelle étiquette de firmware et voulez que
l'orchestrateur la voie sans attendre le rafraîchissement périodique.

**Plus d'infos →** chapitre 15, *Firmware et mises à jour OTA*.
