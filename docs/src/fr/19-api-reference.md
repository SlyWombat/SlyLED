## 17. Reference rapide API

### Scene et mise en page
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/layout` | Mise en page avec projecteurs et positions |
| GET/POST | `/api/stage` | Dimensions de la scene (l, h, p en metres) |
| GET/POST | `/api/objects` | Objets de scene (murs, sols, ponts, accessoires) |
| POST | `/api/objects/temporal` | Creer des objets temporels (bases sur le TTL) |

### Projecteurs
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/fixtures` | Lister / creer |
| GET/PUT/DELETE | `/api/fixtures/:id` | CRUD |
| PUT | `/api/fixtures/:id/aim` | Definir le point de visee |

### Spectacles et Timelines
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/timelines` | Lister / creer |
| POST | `/api/timelines/:id/bake` | Demarrer la compilation |
| POST | `/api/timelines/:id/start` | Demarrer la lecture |
| GET | `/api/show/presets` | Lister les spectacles predefinis |
| GET/POST | `/api/show/export`, `/api/show/import` | Sauvegarder/charger un fichier de spectacle |

### DMX
| Methode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET | `/api/dmx-profiles` | Lister les profils |
| GET | `/api/dmx/patch` | Carte d'adresses des univers |
| POST | `/api/dmx/start`, `/api/dmx/stop` | Controle du moteur |

---

