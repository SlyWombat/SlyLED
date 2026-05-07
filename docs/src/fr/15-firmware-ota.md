<!-- review-status: pending -->

## 15. Firmware et mises à jour OTA

L'onglet **Firmware** est la fenêtre unique de l'opérateur sur tous
les périphériques flashables du rig : nœuds exécutants LED, pont
DMX, palet gyro, et nœuds caméra. Chaque périphérique flashable
remonte sa version de micrologiciel courante à l'orchestrateur sur
chaque cycle PING/PONG ; un périphérique périmé apparaît donc comme
« obsolète » dans les secondes qui suivent le démarrage de
l'orchestrateur.

### Versions de production courantes (orchestrateur v1.7.83)

| Périphérique | Piste | Courante | Canal |
| --- | --- | --- | --- |
| Orchestrateur (Windows / macOS) | app | **v1.7.83** | installateur (`SlyLED-Setup.exe`) |
| Application Android opérateur | app | suit la piste de l'orchestrateur | sideload de l'APK depuis `dist/slyled-android.apk` |
| Nœud exécutant LED (ESP32) | `child-led-esp32` | **v7.5.11** | OTA |
| Nœud exécutant LED (D1 Mini) | `child-led-d1mini` | **v7.5.10** | OTA |
| Nœud exécutant LED (Giga enfant) | `child-led-giga` | **v7.5.2** | OTA |
| Pont DMX (ESP32) | `dmx-bridge-esp32` | **v7.5.20** | OTA |
| Pont DMX (Giga R1) | `dmx-bridge-giga` | **v7.5.20** | OTA |
| Firmware parent (Giga R1) | `parent-giga` | **v7.5.24** *(en attente — l'orchestrateur de bureau est l'exécution recommandée)* | USB seulement |
| Contrôleur Gyro (ESP32-S3) | `gyro-esp32s3` | **v1.2.8** | OTA |
| Nœud caméra (Linux SBC) | `camera-node` | **v1.6.3** | déploiement SSH depuis l'onglet Firmware |

L'onglet Firmware interroge `firmware/registry.json` pour connaître
la version « courante » ; ce tableau est donc régénéré
automatiquement à chaque release et l'opérateur n'a jamais à le
garder en tête.

### Flash USB

1. Ouvrez l'onglet **Firmware**.
2. Cliquez sur la carte **Flash USB**. La liste déroulante affiche
   chaque binaire connu du registre pour les cartes flashables par
   USB (LED ESP32 / D1 Mini / Giga enfant / variantes du pont DMX /
   Contrôleur Gyro).
3. Branchez la carte cible et choisissez son port COM dans la
   seconde liste déroulante.
4. Cliquez sur **Flasher** — la progression montre un pourcentage
   et un « vérification OK » final avant que la carte redémarre
   sous le nouveau micrologiciel.

Le Contrôleur Gyro (ESP32-S3) embarque un port série USB-CDC dans
son firmware. Si une compilation défaillante laisse le palet
incapable d'énumérer en USB, maintenez le bouton **BOOT** appuyé en
branchant le câble pour entrer dans le bootloader ROM manuel ;
l'onglet Firmware re-flashe alors via le chemin de récupération
esptool.

### OTA (Over-the-Air)

1. Configurez les identifiants WiFi sur l'onglet Firmware — ils
   sont poussés sur chaque périphérique nouvellement flashé.
2. Cliquez sur **Vérifier les mises à jour**. L'onglet affiche une
   comparaison par périphérique : version remontée → version du
   registre, avec un bouton **Mettre à jour** sur tout ce qui est
   obsolète.
3. Cliquez sur **Mettre à jour** sur tout exécutant obsolète. Le
   statut en cours de flash revient en direct ; le périphérique
   redémarre automatiquement après vérification.
4. Nouveau depuis v1.7.83 : lorsqu'un SHA-256 du registre ne
   correspond pas au binaire sur disque (téléchargement
   interrompu ou registre édité à la main), l'orchestrateur retombe
   sur la release GitHub correspondant au `releaseTag` plutôt que
   de refuser le flash.

Les builds de diagnostic / développement du gyro
(`esp32s3-gyro-test-firmware.bin`) sont volontairement masqués de
l'UI OTA opérateur — l'onglet Firmware ne propose que les builds de
production. Le binaire de diagnostic reste présent dans `dist/` pour
les ingénieurs effectuant un débogage en cartes appairées.

### Registre Firmware

`firmware/registry.json` est la source de vérité unique de la
version que l'orchestrateur croit livrée à chaque release. Chaque
entrée porte :

- `id` et `name` pour le libellé de l'UI OTA.
- `version` (semver 3 segments) — ce que doit exécuter le
  périphérique de l'opérateur.
- `releaseTag` et `releaseAsset` — le tag de release GitHub et le
  nom de fichier de l'asset à l'intérieur, utilisés par le repli
  OTA.
- `sha256` — hachage de vérification que l'orchestrateur valide
  avant et après flashage.

L'édition manuelle de `registry.json` n'est pas recommandée ;
`build_release.ps1` le maintient en synchronisation avec les
empreintes des binaires à chaque release. L'onglet Firmware
rafraîchit le registre depuis GitHub à la demande via le bouton
**Rafraîchir** ; un installateur fraîchement récupéré voit donc
immédiatement les versions correspondant à son tag de release.

---

