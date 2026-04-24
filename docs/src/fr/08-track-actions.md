## 7. Action Track

### Action Track (Type 18)
Fait suivre les objets mobiles par les lyres DMX en temps reel pendant la lecture.

**Fonctionnement :**
1. Creez des objets mobiles (accessoires/artistes) dans l'onglet Mise en page
2. Creez une action Track dans l'onglet Actions
3. Selectionnez les objets cibles et configurez l'assignation
4. Pendant la lecture, la boucle a 40 Hz calcule le pan/tilt pour chaque lyre

**Algorithme d'assignation :**
- Nombre egal de lyres et d'objets : correspondance 1:1
- Plus de lyres que d'objets : repartition uniforme entre les objets
- Plus d'objets que de lyres : cycle a travers les objets (2 s par cible par defaut)

**Champs :**
| Champ | Description |
|-------|-------------|
| trackObjectIds | ID des objets cibles (vide = tous les objets mobiles) |
| trackCycleMs | Temps de cycle lors du cyclage (par defaut 2000 ms) |
| trackOffset | Decalage global [x,y,z] en mm |
| trackFixtureIds | ID de projecteurs specifiques (vide = toutes les lyres) |
| trackFixtureOffsets | Surcharges [x,y,z] par projecteur |
| trackAutoSpread | Repartir plusieurs lyres sur la largeur de l'objet |

---

