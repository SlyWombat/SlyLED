## 4. Configuration des projecteurs

### Que sont les projecteurs ?
Un projecteur est l'entite principale sur la scene. Il encapsule le materiel physique et ajoute des attributs au niveau de la scene :
- **Projecteurs LED** — lies a un Performer enfant, avec des chaines LED
- **Projecteurs DMX** — lies a un univers/adresse DMX, avec un profil et un point de visee

### Ajout de projecteurs LED
1. Allez dans l'onglet **Configuration**, cliquez sur **Decouvrir** pour trouver les Performers
2. Cliquez sur **Ajouter un projecteur** puis selectionnez le type "LED"
3. Liez a un Performer et configurez les chaines (nombre de LED, longueur, direction)

### Ajout de projecteurs DMX (assistant)
Cliquez sur **+ Projecteur DMX** dans l'onglet Configuration pour lancer l'assistant en 3 etapes :
1. **Choisir le projecteur** : Recherchez dans l'Open Fixture Library (700+ projecteurs) ou creez un projecteur personnalise
2. **Definir l'adresse** : Univers, adresse de depart et nom — avec detection de conflits en temps reel
3. **Confirmer** : Verifiez tous les parametres, cliquez sur "Creer le projecteur"

### Configurer un contrôleur HinksPix
Un HinksPix PRO est un **contrôleur de pixels** : SlyLED lui envoie les couleurs par le réseau (sACN ou Art-Net) et il pilote les guirlandes branchées sur ses ports. Le contrôleur lui-même est du **matériel, pas un projecteur** — il apparaît dans **Configuration → Matériel**. Chaque guirlande sur un de ses ports est un projecteur LED que vous placez, ciblez et programmez comme les autres.

1. **Ajoutez-le.** Configuration → **Découvrir** trouve les contrôleurs HinksPix du réseau (ou **+ Ajouter** avec son IP). Il apparaît comme une ligne de **Matériel** avec son état, son micrologiciel et un résumé comme *1 port configuré · 2 univers (1–2) · 200px*. **Renommer** lui donne un nom.
2. **Set up** (le bouton vert de cette ligne) ouvre le guide de première configuration :
   - **Votre contrôleur** — ce que SlyLED a trouvé, en clair : le modèle, le micrologiciel et le rôle de chaque carte. Les cartes *Long-Range* demandent un récepteur au bout de chaque câble ; les cartes *Local SPI* acceptent les pixels directement. Il avertit aussi si le protocole DMX de SlyLED (Réglages → DMX) et le protocole d'entrée du contrôleur diffèrent — ils doivent correspondre, sinon rien ne s'allume.
   - **Votre implantation** — **Importer depuis mon dossier de spectacle xLights** (le plus rapide si vous utilisez xLights), ou **Configurer à la main**.
   - **Vos guirlandes** — pour chaque port occupé, saisissez le nombre de pixels (ou la longueur et les pixels par mètre, puis **=**) et un nom, par exemple *Avant-toit du garage*. Un port accepte jusqu'à 680 pixels RVB sur un PRO V1/V2. L'enregistrement crée un projecteur LED par port. Rien n'est encore envoyé au contrôleur.
   - **Envoyer** — un résumé en clair puis **Envoyer au contrôleur…** : SlyLED sauvegarde d'abord le contrôleur (pour pouvoir toujours revenir en arrière), écrit l'implantation, le redémarre et relit pour vérifier.
   - **Vérifier** — **Identifier** allume un port en rouge pendant 8 secondes. **Test des couleurs** l'allume en rouge puis en vert et demande ce que vous avez vu ; SlyLED en déduit l'ordre des couleurs de la guirlande. **Tout allumer** lance une poursuite lente sur chaque guirlande.
3. **Rien ne s'allume ?** Vérifiez que l'implantation a été envoyée, que le protocole DMX correspond, et que le moteur DMX tourne. Sinon la guirlande est peut-être sur un autre port — ou utilisez la page de test du contrôleur (**Web UI** sur sa ligne de Matériel).

**Configure** sur la ligne de Matériel ouvre la vue complète en cinq étapes (Lire · Modifier · Vérifier · Appliquer · Contrôler) pour les renvois, sauvegardes et restaurations.

### Moniteur DMX
Parametres puis DMX puis **Moniteur DMX** ouvre une grille en temps reel de 512 canaux par univers. Cliquez sur n'importe quelle cellule pour definir une valeur. Code couleur par intensite.

### Controle de groupe de projecteurs
Parametres puis DMX puis **Controle de groupe** ouvre un panneau de controle pour les groupes de projecteurs. Curseur de variateur principal, curseurs R/V/B, et boutons de preselection rapide des couleurs (Chaud, Froid, Rouge, Eteint).

### Test des canaux DMX
Dans l'onglet Configuration, cliquez sur **Details** sur n'importe quel projecteur DMX pour ouvrir le panneau de test des canaux :
- **Curseurs** pour chaque canal avec sortie DMX en direct
- **Boutons rapides** : Tout allume, Noir, Blanc, Rouge, Vert, Bleu
- **Etiquettes de capacite** indiquant ce que fait chaque plage de valeurs (p. ex. "Stroboscope lent vers rapide")
- Les modifications prennent effet immediatement sur le projecteur physique via Art-Net/sACN

### Types de projecteurs
| Type | Description |
|------|-------------|
| **Lineaire** | Bande LED. Pixels le long d'un chemin. |
| **Point** | Source lumineuse DMX avec cone de faisceau. |
| **Groupe** | Collection de projecteurs cibles comme un seul. |

---

