<!-- review-status: pending -->

## Annexe D — Comportement au repos

Un rig « solide comme un roc » est un rig où, à tout moment où aucun
spectacle ne joue et aucun opérateur ne pilote activement une tête,
chaque projecteur motorisé se gare sur une pose connue avec la
lampe fermée. Les opérateurs attendent ce contrat parce que
l'alternative — une tête plantée sur la pose que le dernier
écrivain a laissée — paraît cassée même quand rien ne va mal. Cette
annexe est la liste canonique des moments où l'orchestrateur gare,
ne gare pas, et de ce que « garé » signifie en pratique.

### Ce que signifie « garé »

Un projecteur motorisé garé :

- Vise sa pose **Home** (la pose enregistrée dans l'assistant Set
  Home ; repli au centre mécanique en l'absence de Home).
- Tient son **gradateur à 0** pour que la lampe ne bave pas sur le
  rig.
- Ferme le **shutter** si le préréglage porte un canal strobe avec
  une capacité `Closed` — les appareils à shutter mécanique
  bénéficient de la fermeture explicite.
- Libère tout emplacement de roue de couleurs qu'il tenait, en le
  remettant à l'emplacement 0 (open / blanc), pour que la frame de
  spectacle suivante n'hérite pas d'un filtre périmé.

### Quand l'orchestrateur gare une tête

| Déclencheur | Chemin | Notes |
| --- | --- | --- |
| **Démarrage à froid** | Boot orchestrateur | Chaque appareil DMX se gare une fois que le moteur démarre. Évite la surprise « la tête a été laissée pointée vers le mur du fond hier soir ». |
| **Fin naturelle de chronologie** | Sortie de `_dmx_playback_loop` | Les têtes pilotées par le précalcul de la chronologie se garent à la fin du spectacle. Les têtes pilotées par action de suivi se garent aussi (#807) — avant le correctif, seules les têtes pilotées par précalcul se garaient, laissant les projecteurs revendiqués par un suiveur bloqués sur leur dernière pose. |
| **L'opérateur appuie sur Stop** | `_dmx_playback_stop` armé | Identique à la fin naturelle. Le balayage de blackout ne s'applique qu'à l'arrêt ou à la fin de l'itération finale (#840), pas entre les itérations de boucle. |
| **Relâchement de revendication** | Arbitre de revendication mover-control | Quand un téléphone Android ou un palet gyro relâche une revendication, la tête revient au spectacle si un spectacle est en cours, sinon elle se gare. Le relâchement est instantané — pas de lissage en v1.7.83+. |
| **Re-stabilisation après cycle d'alimentation** | Premier PONG d'un nœud après boot | Quand la carte enfant d'un appareil refait un cycle d'alimentation, l'orchestrateur renvoie la luminosité globale courante (#843) et la frame de spectacle suivante écrit une pose connue. Avant v1.7.83, le nœud pouvait apparaître à pleine luminosité pendant une frame ; le renvoi à la réception du PONG ferme cette fenêtre. |

### Ce qui ne déclenche PAS de garage

Ces actions ne garent volontairement pas les têtes — les
opérateurs s'y attendent parfois, mais garer à chaque coup ferait
voler la tête au spectacle.

- **Coups uniques `/api/mover/<fid>/aim`** — ces appels sont des
  pulsations de test opérateur direct ; le rig garde la pose
  écrite par la route jusqu'à la prochaine frame de spectacle.
- **Curseurs de test DMX dans l'onglet Paramètres** — même
  logique. Les curseurs surchargent la sortie du spectacle tant
  que l'opérateur les pilote.
- **Coupures brèves dans une revendication active** — un
  téléphone ou un palet qui perd brièvement le WiFi pendant une
  seconde ne libère pas la revendication. Le TTL de revendication
  est de 15 s ; une tête ne se gare que quand le TTL s'écoule
  sans battement de cœur (#813 §6.3 « silence total des
  communications »).
- **Sondages d'étalonnage** — les balayages de découverte de
  faisceau et de convergence tiennent la tête là où le sondage
  arrive. La session d'étalonnage gare la tête explicitement
  quand elle se termine (succès ou abandon).

### Comment vérifier sur votre rig

1. Garez un projecteur motorisé sur un mur connu (Set Home →
   sauvegarder).
2. Arrêtez tout spectacle en cours.
3. Visez la tête vers le sol avec `/api/mover/<fid>/aim`.
4. Lancez la lecture d'une chronologie blanche de 5 s. La tête NE
   doit PAS bouger (règle 3 : les coups uniques tiennent).
5. Arrêtez la chronologie. La tête doit revenir à Home avec la
   lampe fermée en moins de ~50 ms.

Si l'étape 5 ne se produit pas, vérifiez (a) que l'appareil a une
pose Home enregistrée, (b) que la chronologie s'est terminée
naturellement plutôt que par un crash, (c) qu'aucune revendication
n'est tenue sur l'appareil (l'onglet Configuration affiche l'état
de revendication par appareil).

---

