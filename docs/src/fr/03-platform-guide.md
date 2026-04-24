## 2. Guide des plateformes

### Bureau Windows (SPA)
L'interface principale de conception et de controle. SPA complete a 7 onglets avec mise en page 2D/3D, editeur de Timeline, effets spatiaux, profils DMX et gestion du firmware.

**Lancement :** `powershell -File desktop\windows\run.ps1` ou executez `SlyLED.exe`
**Installation :** Executez `SlyLED-Setup.exe` (inclut l'icone de la barre systeme)

### Application Android
Compagnon mobile pour la surveillance et le controle de lecture. Disponible sur le meme reseau WiFi que le serveur de bureau.

**Installation :** Transferez `SlyLED.apk` sur votre telephone et installez-le.
**Connexion :** Entrez l'adresse IP du serveur et le port (affiches dans l'onglet Parametres du bureau).

**Fonctionnalites Android :**
- **Tableau de bord** — statut des Performers, indicateurs en ligne/hors ligne
- **Configuration** — visualiser les projecteurs, decouvrir les Performers
- **Mise en page** — canevas 2D avec zoom par pincement, repositionnement par glissement, placement par toucher, cones de faisceau DMX, visualisation des objets, boutons d'acces rapide a la mise en page, affichage de la patrouille pour les objets mobiles
- **Actions** — parcourir et creer des effets LED
- **Execution** — emulateur de spectacle avec points LED et cones de faisceau DMX, compilation/synchronisation/lecture de Timeline, spectacles predefinis
- **Parametres** — nom du serveur, luminosite, reinitialisation d'usine

### Configuration du firmware (ESP32/D1 Mini)
Chaque Performer propose une page de configuration a 3 onglets a l'adresse `http://<adresse-ip>/config` :
- **Tableau de bord** — nom d'hote, version du firmware, statut de l'action active
- **Parametres** — nom de l'appareil, description, nombre de chaines
- **Configuration** — nombre de LED par chaine, longueur, direction, broche GPIO (ESP32)

---

