<!-- review-status: pending -->
## 2. Guide des plateformes

### Bureau Windows (SPA)
L'interface principale de conception et de contrôle. SPA complète à 7 onglets avec mise en page 2D/3D, éditeur de Timeline, effets spatiaux, profils DMX et gestion du firmware.

**Lancement :** `powershell -File desktop\windows\run.ps1` ou exécutez `SlyLED.exe`
**Installation :** Exécutez `SlyLED-Setup.exe` (inclut l'icône de la barre système)

### Application Android
Outil opérateur en direct pour exécuter des spectacles depuis votre téléphone. Se connecte au serveur de bureau par Wi-Fi. Depuis la version 1.8.1, l'onglet Contrôle est refait en **Surface de commande** — voir #888 / `docs/design/mobile_ui_redesign.md`.

**Installation :** Transférez `slyled-android.apk` sur votre téléphone et installez-le (sideload).
**Connexion :** Numérisez le code QR de l'onglet Paramètres du bureau, ou entrez l'adresse IP du serveur et le port manuellement.

![Écran de connexion Android](screenshots/android/android-connection.png)

**Barre de navigation inférieure (3 onglets) :** Scène / Contrôle / État. Les Paramètres se trouvent dans l'engrenage ⚙ en haut à droite, pas dans la barre inférieure.

**Gestes de la barre supérieure :**
- **Appui long sur le logo SlyLED** → blackout instantané (master = 0). Double-haptique soutenu. Le seul geste « bouton rouge » ; les autres actions de sécurité vivent comme boutons par page.
- **Pastille de connexion** — point vert = Connecté ; pulsation orange lente = Reconnexion (dégradée) ; pulsation rouge rapide = Hors ligne. Tapez pour réessayer.
- **Engrenage ⚙ Paramètres** — nom du serveur, dimensions de la scène, calibration de la Luminosité automatique, export/import de la configuration, déconnexion.

**Onglet Scène** — viewport en direct affichant tous les projecteurs avec cônes de faisceau, marqueurs d'objets suivis, plancher quadrillé. Pincement pour zoomer + glissement pour panoramiquer.

![Vue Scène Android](screenshots/android/android-stage-idle.png)

**Onglet Contrôle (refait pour la v1.8.1) :** ancre Now Playing persistante au-dessus d'un pager à 4 pages.

- **Master** *(page par défaut)* — curseur de luminosité global avec pas de ±5 % + halo lors du glissement. Bascule Luminosité automatique (déplacée depuis Paramètres) + sélecteur de source (Micro / Lecture / USB) + indicateur d'enveloppe en direct.

  ![Contrôle · Master](screenshots/android/android-control-master.png)

- **Grab** — vignettes de têtes mobiles montrant la couleur courante + flèche de direction pan/tilt. Rangée de favoris en haut (étoilez pour ajouter). Tapez une vignette → Mode contrôleur (pan/tilt piloté par le gyroscope à 20 Hz). Bouton « Tous au repos » en haut à droite pour ramener toutes les têtes.

  ![Contrôle · Grab](screenshots/android/android-control-grab.png)

- **Fixtures** — projecteurs DMX non mobiles (machines à bulles, machines à fumée, washes, pars, stroboscopes) avec raccourcis pilotés par profil : 🫧 bulles, 💨 fumée faible/moyenne/forte, 🌀 ventilateur lent/moyen/rapide, 💡 nuanciers de couleurs, 🟣 UV, ⚡ stroboscope momentané, 🧼 maintien-pour-nettoyer. « Plus de contrôles → » ouvre une feuille par canal avec des curseurs de capacité. Bouton « Arrêter tous les effets » en haut à droite tue stroboscopes + bulles/fumée en parallèle.

  ![Contrôle · Fixtures](screenshots/android/android-control-fixtures.png)

- **Shows** — sections étoilés → récents → tous, classés par dernier lancement. Lancement en un toucher. Appui long pour étoiler.

  ![Contrôle · Shows](screenshots/android/android-control-shows.png)

**L'ancre Now Playing** se trouve au-dessus du pager — nom, pastille de boucle, temps écoulé / total, barre de progression, ARRÊT et Suivant.

**Onglet État** — surveillance des appareils (Performers en ligne/hors ligne, RSSI, firmware), nœuds caméra avec bouton Suivre pour démarrer/arrêter le suivi de personne, et état du moteur Art-Net/DMX.

![État Android](screenshots/android/android-status.png)

**Feuille Paramètres** (⚙ en haut à droite) — nom du système, unités, dimensions de la scène (L × H × P), mode sombre, journalisation, plus le bloc de configuration de la Luminosité automatique (activation, mode du modèle, curseurs sensibilité/plancher/plafond/attaque/relâchement).

![Paramètres Android](screenshots/android/android-settings.png)

**Mode contrôleur (Grab → tapez une tête mobile) :** Tenez le téléphone et pointez où vous voulez le faisceau — pan/tilt suit l'orientation du téléphone à 20 Hz. Tapez Recentrer pour calibrer, X pour quitter. Démarrage / arrêt par appui protégés par nonce+ACK (#825). À la première utilisation sur un nouveau téléphone, l'assistant d'axe (#869) mesure les axes du repère du téléphone ; environ 10 secondes.

### Configuration du firmware (ESP32/D1 Mini)
Chaque Performer propose une page de configuration à 3 onglets à l'adresse `http://<adresse-ip>/config` :
- **Tableau de bord** — nom d'hôte, version du firmware, statut de l'action active
- **Paramètres** — nom de l'appareil, description, nombre de chaînes
- **Configuration** — nombre de LED par chaîne, longueur, direction, broche GPIO (ESP32)

---

