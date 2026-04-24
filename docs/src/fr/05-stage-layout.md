## 4. Mise en page de la scene

### Canevas 2D
L'onglet Mise en page affiche une vue frontale 2D de la scene. Les dimensions de la scene (largeur x hauteur) sont definies dans les Parametres.

La barre d'outils de mise en page fournit : Enregistrer, bascule de mode 2D/3D (affiche le mode actuel en texte), Recentrer, Vue du dessus, Vue de face, Disposition automatique DMX, Afficher/masquer les chaines LED. Les bascules actives sont surlignees en vert.

Utilisez le parametre URL `?tab=` pour un lien direct vers n'importe quel onglet (p. ex. `?tab=layout`).

| Action | Bureau | Android |
|--------|--------|---------|
| **Placer un projecteur** | Glisser depuis la barre laterale | Toucher le projecteur puis toucher le canevas |
| **Deplacer un projecteur** | Glisser sur le canevas | Glisser sur le canevas |
| **Supprimer un projecteur** | Double-clic puis Supprimer | Toucher puis Modifier puis Supprimer |
| **Zoom** | Molette de la souris | Geste de pincement |
| **Panoramique** | — | Glissement a deux doigts |
| **Modifier les coordonnees** | Double-clic | Toucher le projecteur place |
| **Modifier un objet** | Double-clic | Toucher l'objet dans la liste |

**Elements affiches :**
- Lignes de grille a espacement de 1 metre
- Etiquettes des dimensions de la scene
- **Projecteurs LED** : noeuds verts avec lignes de chaines colorees (fleches de direction)
- **Projecteurs DMX** : noeuds violets avec triangles de cone de faisceau vers le point de visee
- **Objets** : rectangles semi-transparents avec etiquettes de nom (rognes aux limites de la scene)
- **Points de visee** : cercles rouges aux points de visee DMX

### Fenetre 3D (bureau uniquement)
Basculez en mode 3D pour une scene Three.js interactive :
- Camera orbitale avec glissement de la souris
- Cones de faisceau en geometrie 3D
- Spheres de visee deplacables pour les projecteurs DMX
- Plans/boites d'objets avec transparence

### Systeme de coordonnees
- **aimPoint[0]** = X (position horizontale, mm)
- **aimPoint[1]** = Y (hauteur depuis le sol, mm) — utilise pour l'axe vertical du canevas 2D
- **aimPoint[2]** = Z (profondeur, mm) — utilise uniquement dans la fenetre 3D
- **canvasW** = largeur de la scene x 1000 (mm)
- **canvasH** = hauteur de la scene x 1000 (mm)

---

