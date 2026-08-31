# L2 Simple Assist v2

Petit système Main / Box pour Lineage 2 sous Windows.

Cette version ne capture pas l'écran, ne lit aucun pixel, n'utilise pas d'OCR et ne contrôle pas la souris. Le Main observe uniquement deux touches clavier. Chaque Box reçoit une action réseau simple, puis utilise `IbInputSimulator.dll` avec le backend Logitech pour jouer sa propre touche locale.

## Les deux actions

| Action | Touche écoutée sur le Main | Touche jouée sur chaque Box |
|---|---:|---:|
| Attaquer | `F2` | `&` |
| Suivre | `F3` | `VK_2` |

Les quatre touches sont modifiables dans les interfaces.

Exemple d'utilisation dans Lineage 2:

1. Sur chaque Box, place une macro d'assist et d'attaque sur la touche physique `1`.
2. Place une macro de suivi sur la touche physique `2`.
3. Quand tu presses `F2` sur le Main, toutes les Box jouent leur touche Attaquer.
4. Quand tu presses `F3` sur le Main, toutes les Box jouent leur touche Suivre.

Sur un clavier français AZERTY:

- `&` correspond normalement à la touche physique `1`.
- `VK_2` force la touche virtuelle de la rangée numérique `2`, qui porte généralement le caractère `é` sur un clavier français.

## Sécurité de focus

Par défaut, une action n'est diffusée que si `L2.exe` est au premier plan sur le PC Main.

Chaque Box effectue ensuite sa propre vérification. Si `L2.exe` n'est pas au premier plan sur cette Box, aucune touche n'est injectée et un message d'échec est renvoyé au Main.

Le bouton de test du Main ignore seulement le focus du Main. Le contrôle de focus reste actif sur chaque Box.

## Ce que les interfaces affichent

Le Main affiche:

- le nombre de Box connectées;
- le nom et l'adresse IP de chaque Box;
- ses touches Attaquer et Suivre;
- le résultat de sa dernière action.

Chaque Box affiche:

- le Main auquel elle est connectée;
- les autres Box visibles via le Main;
- les touches Attaquer et Suivre de chaque Box;
- les commandes reçues et leur résultat.

## Installation rapide

### PC Main

1. Extrais le dossier complet.
2. Lance `START_MAIN_ADMIN.bat` si Lineage 2 fonctionne en administrateur. Sinon, lance `START_MAIN.bat`.
3. Autorise Python uniquement sur les réseaux privés si le pare-feu Windows le demande.
4. Vérifie les réglages par défaut:
   - Attaquer: `F2`
   - Suivre: `F3`
   - Processus: `L2.exe`
   - TCP: `45880`
   - Découverte UDP: `45881`
5. Laisse le programme ouvert.

### Chaque PC Box

1. Copie le dossier complet sur le PC.
2. Lance `START_BOX_ADMIN.bat` si le jeu fonctionne en administrateur.
3. Donne un nom différent à chaque Box, par exemple `Buffer`, `Healer` et `Box 3`.
4. Laisse `Adresse du Main` sur `AUTO` pour la découverte locale.
5. Vérifie les touches:
   - Attaquer: `&`
   - Suivre: `VK_2`
6. Clique sur `Enregistrer et reconnecter`.
7. Garde Lineage 2 au premier plan sur cette machine.

La clé d'appairage fournie est identique dans `main_settings.json` et `box_settings.json`.

Si `AUTO` ne trouve pas le Main, utilise le bouton `Détecter le Main` ou saisis manuellement l'adresse IP affichée dans l'interface du Main.

## Premier test conseillé

1. Démarre le Main et toutes les Box.
2. Vérifie que le Main affiche le bon nombre de Box.
3. Mets Lineage 2 au premier plan sur chaque Box.
4. Sur chaque Box, utilise `Tester Attaquer`, puis `Tester Suivre`.
5. Sur le Main, utilise `Tester Attaquer`, puis `Tester Suivre`.
6. Mets Lineage 2 au premier plan sur le Main et presse réellement `F2`, puis `F3`.

Les journaux indiquent précisément quelle Box a réussi, quelle action a été jouée et pourquoi une injection a éventuellement été annulée.

## Touches acceptées

Les touches nommées comprennent notamment:

- `F1` à `F24`
- `A` à `Z`
- `0` à `9`
- `SPACE`, `ENTER`, `TAB`, `ESC`
- `LEFT`, `RIGHT`, `UP`, `DOWN`
- `NUMPAD0` à `NUMPAD9`
- `VK_0` à `VK_9`
- des combinaisons simples comme `CTRL+F2`, `SHIFT+1` ou `ALT+F3`

Pour les touches écoutées sur le Main, les modificateurs sont vérifiés exactement. Par exemple, `F2` ne déclenche pas l'action si `CTRL` est également maintenu, sauf si la configuration indique explicitement `CTRL+F2`.

Les deux actions Main doivent utiliser des touches physiques de base différentes. Par exemple, `F2` et `F3` sont acceptées, mais `F2` et `CTRL+F2` sont refusées afin d'éviter un déclenchement ambigu lors du relâchement de `CTRL`. Les deux touches Box doivent également être différentes.

Une touche déjà maintenue au démarrage ou après un changement de configuration ne déclenche rien. Elle doit d'abord être relâchée, puis pressée à nouveau.

## Processus Lineage 2

Certains clients utilisent `L2.bin` ou un autre nom au lieu de `L2.exe`.

Tu peux saisir plusieurs processus séparés par une virgule ou un point-virgule:

```text
L2.exe;L2.bin
```

Le programme affiche en permanence le processus actuellement au premier plan.

## Réseau

- TCP `45880`: connexion persistante et diffusion des actions.
- UDP `45881`: découverte automatique du Main sur le réseau local.
- Une clé d'appairage est vérifiée avant qu'une Box soit ajoutée à la liste des machines connectées.
- Les commandes sont sérialisées afin que toutes les Box reçoivent Attaquer et Suivre dans le même ordre.
- Les écritures réseau ont un timeout pour éviter qu'une Box bloquée ne fige le Main.
- Les Box se reconnectent automatiquement après une coupure.

Le protocole n'est pas chiffré. Il est prévu pour un réseau local privé. Ne redirige pas les ports sur Internet.

## Mise à jour depuis la v1

La v2 utilise un nouveau protocole pour distinguer Attaquer et Suivre. Mets à jour le Main et toutes les Box avec les fichiers de la même archive.

Si tu remplaces les scripts dans un ancien dossier, les anciens réglages sont migrés automatiquement:

- l'ancien `trigger_key` devient la touche Main Attaquer;
- l'ancien `output_key` devient la touche Box Attaquer;
- la nouvelle action Suivre reçoit les valeurs par défaut `F3` et `VK_2`.

## Dépannage Logitech

### `IbSendInit(Logitech) a échoué avec le code 6`

Le projet source recommandait Logitech Gaming Software `9.02.65`, puis un redémarrage de Windows et le backend `Logitech`.

Avec certaines installations G Hub, essaie le backend `LogitechGHubNew`.

### DLL impossible à charger

- Vérifie que `IbInputSimulator.dll` est à côté de `box_assist.py`.
- Utilise Python 3 en version 64 bits.
- Installe le Microsoft Visual C++ Redistributable 2015-2022 x64 si nécessaire.

### Le Main voit la Box, mais la touche ne part pas

- Mets Lineage 2 au premier plan sur la Box.
- Vérifie le processus affiché dans la barre d'état.
- Lance le jeu et le script avec le même niveau de privilèges.
- Teste les deux touches localement depuis l'interface de la Box.
- Lis le message exact dans le journal.

### La découverte automatique ne fonctionne pas

- Autorise Python sur les réseaux privés dans le pare-feu Windows.
- Vérifie que les machines sont sur le même LAN ou sur des VLAN autorisant le broadcast UDP.
- Saisis manuellement l'IP du Main.
- Vérifie TCP `45880` et UDP `45881` sur le PC Main.

## Fichiers principaux

- `main_assist.py`: interface du Main et écoute de `F2` / `F3`.
- `box_assist.py`: interface de la Box, choix des deux touches et injection Logitech.
- `assist_network.py`: protocole v2, découverte, appairage, reconnexion et ordre des commandes.
- `logitech_input.py`: wrapper minimal de `IbInputSimulator.dll`.
- `assist_common.py`: configuration, focus Windows et résolution des touches.
- `main_settings.json`: réglages du Main.
- `box_settings.json`: réglages d'une Box.
- `tests/test_core.py`: tests unitaires et tests réseau.

## Limites volontaires

Le programme ne reconnaît pas les mobs, ne lit pas les points de vie, ne choisit aucune cible, ne lance aucune action de lui-même et ne fonctionne pas lorsque tu ne presses aucune touche configurée.

Il est prévu pour un client Lineage 2 actif par PC Box. Il peut vérifier le processus au premier plan, mais il ne peut pas sélectionner une fenêtre inactive parmi plusieurs clients ouverts sur le même PC.

Vérifie les règles du serveur utilisé. Le multiboxing et la diffusion de touches peuvent être réglementés différemment selon les serveurs.
