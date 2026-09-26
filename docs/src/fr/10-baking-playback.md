## 10. Compilation et lecture

### Compiler
Compile une Timeline en instructions d'action minimales par Performer :
1. Cliquez sur **Compiler** — la progression affiche le nombre d'images et de segments
2. Cliquez sur **Synchroniser** pour envoyer les instructions aux Performers via UDP
3. Cliquez sur **Demarrer** pour une lecture synchronisee par NTP

### Sortie
- **Segments d'action** : Sequences des 19 types d'actions (14 classiques + 5 DMX/spatiales)
- **Fichiers LSQ** : Donnees RGB brutes par pixel a 40 Hz (telechargeables en ZIP)
- **Donnees de previsualisation** : 1 couleur par chaine par seconde pour l'emulateur

### Programmer des spectacles (coucher du soleil, semaine, saisons)
**Exécution → Schedule** lance les spectacles tout seul — « le spectacle de Noël de 15 minutes avant le coucher du soleil jusqu'à 23 h, chaque soir du 20 nov. au 6 janv. » — et joue le fond (wash) entre eux.

1. **D'abord la position.** Les heures de lever/coucher du soleil demandent votre latitude et longitude : **Réglages → Général → Location** (longitudes ouest négatives ; fuseau par défaut America/Toronto). SlyLED ne cherche jamais votre position.
2. **Entre les entrées.** Choisissez la timeline **wash** à jouer quand rien n'est prévu (par défaut — la scène n'est jamais noire sauf si vous le demandez), ou *garder la dernière image*, ou *éteint*. Les **heures calmes** imposent ce choix sur une plage horaire.
3. **+ Schedule**, puis des entrées : jours, **début** et **fin** (*à l'heure*, ou *coucher / lever du soleil / crépuscule / aube civils* ± minutes ; la fin peut aussi être *pendant N minutes*) et ce qu'il faut **jouer** : une timeline, la playlist, ou *éteint*. Une fin avant le début = le lendemain matin. Un programme peut avoir une **saison** (`11-20` à `01-06` se répète chaque année) et une **priorité** : en cas de chevauchement, la plus haute priorité gagne, puis la plus précise, puis le début le plus tardif — le panneau indique pourquoi.
4. **Enregistrez**, puis activez (**On**). L'en-tête indique ce qui joue et pourquoi, et la suite. La **grille de la semaine** dessine chaque fenêtre avec le coucher du soleil ; **Simuler une date** liste le plan d'un jour.

**Le contrôle manuel l'emporte.** Démarrer, Arrêter ou Suivant (SPA ou Android), ou un noir général, met le programme en pause : *Manual — schedule paused* avec **Resume**. Par défaut il reprend aussi à la prochaine limite de fenêtre. Un redémarrage n'annule pas un arrêt manuel.

**Redémarrages.** Si SlyLED redémarre au milieu d'une fenêtre, il reprend le spectacle là où il en serait. Les timelines sont compilées automatiquement avant de démarrer et le moteur DMX est lancé s'il était arrêté. Les transitions se font sans image noire.

**Fondus.** Une entrée peut apparaître en fondu (secondes) quand elle part du noir, et disparaître en fondu juste avant que le programme passe au noir (*off*). Jamais entre deux spectacles ni vers le fond : ceux-là s'enchaînent sans creux. Le fondu s'applique par-dessus le Master, sans déplacer le curseur.

**HinksPix autonome — spectacles qui n'utilisent que les lumières d'un contrôleur** *(pas encore vérifié sur le matériel)*. Un spectacle qui n'allume **que** les pixels d'un HinksPix peut aussi être joué par ce contrôleur depuis sa carte SD. Cochez **Offline** sur ces entrées : la case n'est proposée que pour elles (survolez ⓘ sur les autres pour la raison). **Un spectacle qui pilote aussi des projecteurs DMX, des performers ou les pixels d'un autre contrôleur exige que SlyLED tourne** — il ne peut pas être coché, et si un spectacle coché est ensuite modifié pour utiliser d'autres lumières, la compilation refuse l'entrée en donnant la raison au lieu d'en envoyer une partie. Dans **HinksPix standalone**, cochez le contrôleur puis **Preview compile** (les lignes de la semaine) ou **Compile & send** : SlyLED rend chaque timeline admissible, copie les spectacles et le fond sur la carte SD avec un programme par jour de semaine, et règle l'horloge. Le contrôleur n'a pas de calendrier : une compilation couvre la semaine à venir ; *recompile + send nightly* la refait à 03:30 pendant que SlyLED tourne (et corrige l'horloge aux changements d'heure). **Hand-off** choisit quand le contrôleur joue depuis la SD : copie seule (vous changez de mode vous-même), autonome quand SlyLED se ferme / direct au démarrage, ou autonome après chaque envoi. La configuration des ports doit d'abord avoir été envoyée (Configure → Apply). Ce que le contrôleur ne sait pas faire seul est signalé : fondus, *garder la dernière image* et exceptions au-delà de la semaine compilée. La même règle vaut pour l'écran **Standalone playback** du contrôleur (sa configuration des ports → **Standalone playback →**) : seuls les spectacles qui n'utilisent que ses pixels peuvent y être ajoutés.



---

