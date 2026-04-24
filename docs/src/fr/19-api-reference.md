## 19. Référence rapide de l'API

### Plateau et disposition
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/layout` | Disposition avec appareils et positions |
| GET/POST | `/api/stage` | Dimensions de la scène (l, h, p en mètres) |
| GET/POST | `/api/objects` | Objets de scène (murs, sols, ponts, accessoires) |
| POST | `/api/objects/temporal` | Créer des objets temporels (basés sur TTL) |

### Appareils
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/fixtures` | Lister / créer |
| GET/PUT/DELETE | `/api/fixtures/:id` | CRUD |
| PUT | `/api/fixtures/:id/aim` | Définir le point de visée |

### Spectacles et chronologies
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET/POST | `/api/timelines` | Lister / créer |
| POST | `/api/timelines/:id/bake` | Démarrer le précalcul |
| POST | `/api/timelines/:id/start` | Démarrer la lecture |
| GET | `/api/show/presets` | Lister les spectacles préréglés |
| GET/POST | `/api/show/export`, `/api/show/import` | Sauvegarder / charger un fichier de spectacle |

### DMX
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET | `/api/dmx-profiles` | Lister les profils |
| GET | `/api/dmx/patch` | Plan d'adresses des univers |
| POST | `/api/dmx/start`, `/api/dmx/stop` | Contrôle du moteur |

### Caméras
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET | `/api/cameras` | Lister les caméras enregistrées |
| POST | `/api/cameras` | Enregistrer un nœud caméra comme appareil |
| DELETE | `/api/cameras/:id` | Retirer un appareil caméra |
| GET | `/api/cameras/:id/snapshot` | Capture JPEG en relais |
| GET | `/api/cameras/:id/status` | État en direct du nœud caméra |
| POST | `/api/cameras/:id/scan` | Détection d'objets (relais vers le `/scan` du nœud) |
| GET | `/api/cameras/discover` | Découvrir les nœuds caméra sur le réseau |
| GET/POST | `/api/cameras/ssh` | Identifiants SSH pour le déploiement |
| POST | `/api/cameras/deploy` | Déployer le firmware sur un nœud caméra via SSH+SCP |
| GET | `/api/cameras/deploy/status` | Suivre la progression du déploiement |

### API locale des nœuds caméra (port 5000)
| Méthode | Point de terminaison | Description |
|---------|----------------------|-------------|
| GET | `/status` | État du nœud, capacités, liste des caméras |
| GET | `/config` | Page de configuration HTML avec interface de détection |
| GET | `/snapshot?cam=N` | Capture JPEG depuis la caméra N |
| POST | `/scan` | Détection d'objets (JSON : cam, threshold, resolution, classes) |
| GET | `/health` | Vérification de l'état |

---

<a id="glossary"></a>
