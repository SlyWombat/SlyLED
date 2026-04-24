## 5. Disposition du plateau

### Canevas 2D
L'onglet Disposition affiche une vue frontale 2D du plateau. Les dimensions du plateau (largeur × hauteur) sont définies dans les Paramètres.

La barre d'outils de disposition fournit : Enregistrer, bascule 2D/3D (affiche le mode actuel en texte), Recentrer, Vue du dessus, Vue de face, Disposition DMX automatique, Afficher/masquer les chaînes LED. Les bascules actives sont surlignées en vert.

Utilisez le paramètre d'URL `?tab=` pour un lien direct vers n'importe quel onglet (par exemple `?tab=layout`).

| Action | Bureau | Android |
|--------|--------|---------|
| **Placer un appareil** | Glisser depuis la barre latérale | Toucher l'appareil puis toucher le canevas |
| **Déplacer un appareil** | Glisser sur le canevas | Glisser sur le canevas |
| **Retirer un appareil** | Double-clic → Retirer | Toucher → Modifier → Retirer |
| **Zoom** | Molette de la souris | Geste de pincement |
| **Panoramique** | — | Glissement à deux doigts |
| **Modifier les coordonnées** | Double-clic | Toucher l'appareil placé |
| **Modifier un objet** | Double-clic | Toucher l'objet dans la liste |

**Éléments affichés :**
- Lignes de grille à espacement de 1 mètre
- Étiquettes des dimensions du plateau
- **Appareils LED** : nœuds verts avec lignes de chaînes colorées (flèches de direction)
- **Appareils DMX** : nœuds violets avec triangles de cône de faisceau pointant vers le point de visée
- **Objets** : rectangles semi-transparents avec étiquettes de nom (rognés aux limites du plateau)
- **Points de visée** : cercles rouges aux points de visée DMX

### Fenêtre 3D (bureau uniquement)
Basculez en mode 3D pour une scène Three.js interactive :
- Caméra orbitale avec glissement de la souris
- Cônes de faisceau en géométrie 3D
- Sphères de visée déplaçables pour les appareils DMX
- Plans et boîtes d'objets avec transparence

### Mode Déplacer / Pivoter

Le canevas de disposition possède deux modes d'interaction, basculés avec des raccourcis clavier ou le bouton de la barre d'outils (qui affiche **M** ou **R** pour indiquer le mode actif) :

| Touche | Mode | Description |
|--------|------|-------------|
| **M** | Déplacer | Glissez n'importe quel appareil placé pour le repositionner (mode par défaut) |
| **R** | Pivoter | Cliquez sur un appareil DMX ou une caméra pour afficher un anneau-boussole ; glissez autour de l'anneau pour viser l'appareil |

**Détails du mode Pivoter :**
- Un anneau-boussole apparaît autour de l'appareil sélectionné dès que vous entrez en mode Pivoter
- Glissez dans le sens horaire ou anti-horaire pour définir la direction de visée horizontale
- Le cône de faisceau se met à jour en temps réel pendant le glissement
- Dans la fenêtre 3D, le mode Pivoter active les **TransformControls** Three.js en mode rotation — glissez les arcs colorés pour pivoter sur n'importe quel axe
- Appuyez sur **Ctrl+Z** pour annuler le dernier déplacement ou la dernière rotation

**Flux de travail typique :** placez tous les appareils en mode Déplacer, puis passez en mode Pivoter pour viser les projecteurs motorisés DMX et les caméras vers leurs zones de focalisation prévues avant de lancer l'étalonnage.

### Système de coordonnées
- **aimPoint[0]** = X (position horizontale, mm)
- **aimPoint[1]** = Y (hauteur depuis le sol, mm) — utilisé pour l'axe vertical du canevas 2D
- **aimPoint[2]** = Z (profondeur, mm) — utilisé uniquement dans la fenêtre 3D
- **canvasW** = largeur du plateau × 1000 (mm)
- **canvasH** = hauteur du plateau × 1000 (mm)

---
