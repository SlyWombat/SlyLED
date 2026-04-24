## 16. Depannage

| Probleme | Solution |
|----------|----------|
| **Vue d'execution vide** | Verifiez que les projecteurs sont positionnes dans la Mise en page. Les installations DMX uniquement s'affichent desormais (correctif v8.1). |
| **Cone de faisceau dans la mauvaise direction** | aimPoint[1] est la hauteur (Y), pas la profondeur (Z). Verifiez les valeurs du point de visee. |
| **Crash JSON sur Android** | Mettez a jour vers la v8.1 — aimPoint est passe de Int a Double. Reinitialisation d'usine : necessite desormais un en-tete de confirmation. |
| **Erreur de sauvegarde de spectacle** | Mettez a jour vers la v8.1 — le point de terminaison `/api/show/export` etait manquant. |
| **Echec de la verification du firmware** | Mettez a jour vers la v8.1 — bugs corriges pour le BOM UTF-8 et l'iteration de dictionnaire dans registry.json. |
| **Fenetre 3D ne s'affiche pas** | Utilisez Chrome/Firefox/Edge avec le support WebGL. |
| **Performers non synchronises** | Verifiez que tous les appareils sont sur le meme reseau WiFi. Actualisez dans l'onglet Configuration. |
| **Taille du canevas incorrecte** | Les dimensions de la scene (Parametres) determinent la taille du canevas : canvasW = scene.w x 1000. |

---

