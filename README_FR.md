# L2 Simple Assist v2.3

Petit système Main / Box pour Lineage 2 sous Windows.

Le programme ne capture pas l'écran, ne lit aucun pixel, n'utilise pas d'OCR et ne contrôle pas la souris. Le Main observe uniquement les touches configurées. Chaque Box reçoit une action réseau simple, puis utilise `IbInputSimulator.dll` avec le backend Logitech pour jouer sa touche locale.

## Actions par défaut

| Action | Touche écoutée sur le Main | Touche jouée sur chaque Box |
|---|---:|---:|
| Attaquer | `F2` | `-` |
| Suivre | `F3` | `&` |

Les quatre touches restent modifiables dans les interfaces.

Sur un clavier français AZERTY :

- `-` correspond normalement à la touche physique `6` de la rangée supérieure ;
- `&` correspond normalement à la touche physique `1`.

Exemple d'utilisation : place la macro d'assist/attaque sur la touche `6` des Box et la macro de suivi sur la touche `1`.

Les anciennes installations qui utilisaient encore exactement la paire d'usine `&` / `VK_2` sont migrées automatiquement vers `-` / `&`. Les configurations personnalisées sont conservées.

## Mode multi-fenêtres L2

Le mode multi-fenêtres est disponible uniquement dans **Box Assist** et reste désactivé par défaut.

Active l'option :

> Envoyer chaque commande à tous les L2.exe ouverts sur cette Box

Pour chaque ordre reçu, la Box :

1. détecte les fenêtres visibles appartenant au processus configuré ;
2. garde la fenêtre actuellement active en mémoire ;
3. active chaque fenêtre Lineage 2 séparément ;
4. vérifie que Windows lui a réellement donné le focus ;
5. injecte la touche via le driver Logitech ;
6. restaure la fenêtre qui était active avant la commande.

Le mode fonctionne même si `python.exe`, Box Assist ou une autre application est au premier plan. La sécurité est assurée par la vérification du focus de chaque fenêtre juste avant l'injection. Si Windows refuse d'activer une fenêtre, aucune touche n'est envoyée à cette étape et l'échec est indiqué dans le journal.

La case historique **Ne jamais injecter une touche si Lineage 2 n'est pas au premier plan** reste appliquée au mode simple, lorsque le mode multi-fenêtres est désactivé.

Voir aussi [`MULTI_WINDOW.md`](MULTI_WINDOW.md).

## Installation rapide

### PC Main

1. Lance `START_MAIN_ADMIN.bat` si Lineage 2 fonctionne en administrateur, sinon `START_MAIN.bat`.
2. Autorise Python uniquement sur les réseaux privés si le pare-feu Windows le demande.
3. Vérifie les réglages du Main : processus `L2.exe`, TCP `45880`, UDP `45881`.
4. Laisse le programme ouvert.

### Chaque PC Box

1. Lance `START_BOX_ADMIN.bat` si le jeu fonctionne en administrateur, sinon `START_BOX.bat`.
2. Donne un nom différent à chaque Box, par exemple `Buffer` et `Healer`.
3. Laisse l'adresse du Main sur `AUTO`, ou indique manuellement son adresse IPv4.
4. Vérifie les touches : Attaquer `-`, Suivre `&`.
5. Active éventuellement le mode multi-fenêtres.
6. Clique sur **Enregistrer et reconnecter**.

Les lanceurs Box ouvrent `box_assist_multi.py`. Si la fenêtre affiche encore **v2.0**, le dépôt local n'a pas reçu les fichiers récents ou l'ancien `box_assist.py` a été lancé directement.

## Emplacement des réglages Box

À partir de la v2.3, les réglages modifiables de la Box sont enregistrés hors du dépôt Git :

```text
%LOCALAPPDATA%\LSA\box_settings.json
```

Au premier démarrage, l'application copie automatiquement l'ancien `box_settings.json` du dossier du projet si ce fichier existe. Cela conserve l'adresse IP, le nom de la Box et les autres réglages tout en évitant que les prochains `git pull` soient bloqués par un JSON modifié localement.

Le chemin réellement utilisé est affiché dans le bloc **Multi-fenêtres L2**.

## Réparer un `git pull` bloqué par `box_settings.json`

Sur un ancien clone, Git peut afficher que les modifications locales de `box_settings.json` seraient écrasées. Sauvegarde d'abord le fichier, puis remets uniquement ce fichier dans son état Git :

```bat
copy /Y box_settings.json "%TEMP%\lsa-box-settings.json"
git restore box_settings.json
git pull
copy /Y "%TEMP%\lsa-box-settings.json" box_settings.json
```

Lance ensuite une fois `START_BOX_ADMIN.bat`. La v2.3 copie et migre les réglages vers `%LOCALAPPDATA%\LSA\box_settings.json`. Après avoir vérifié qu'ils apparaissent correctement dans l'interface, nettoie le fichier du dépôt :

```bat
git restore box_settings.json
```

Les futurs `git pull` de cette Box ne seront plus bloqués par les réglages enregistrés depuis l'interface.

## Premier test conseillé

1. Démarre le Main et toutes les Box.
2. Vérifie que chaque Box porte un nom différent et que le Main affiche le bon nombre de connexions.
3. Sur chaque Box, utilise **Tester Attaquer**, puis **Tester Suivre**.
4. En mode multi-fenêtres, ouvre deux clients `L2.exe`, coche l'option, enregistre, puis relance les deux tests.
5. Teste enfin les touches depuis le Main.

Un succès multi-fenêtres ressemble à :

```text
Attaquer: touche '-' envoyée à 2/2 fenêtre(s) L2.
```

## Sécurité de focus en mode simple

Lorsque le mode multi-fenêtres est désactivé, une Box refuse l'injection si la case de sécurité est cochée et que `L2.exe` n'est pas au premier plan.

Le message suivant signifie que l'interface Python était active au moment de la commande :

```text
Injection annulée: fenêtre active python.exe, attendu L2.exe.
```

Ce message ne signale pas une erreur de touche. Il est normal en mode simple. En mode multi-fenêtres v2.3, `python.exe` peut être actif car le programme sélectionne et vérifie lui-même chaque fenêtre L2.

## Touches acceptées

Exemples :

- `F1` à `F24` ;
- `A` à `Z` ;
- `0` à `9` ;
- `SPACE`, `ENTER`, `TAB`, `ESC` ;
- `NUMPAD0` à `NUMPAD9` ;
- `VK_0` à `VK_9` ;
- des caractères dépendant de la disposition du clavier comme `-` ou `&` ;
- des combinaisons simples comme `CTRL+F2`, `SHIFT+1` ou `ALT+F3`.

Les deux actions doivent utiliser des touches différentes.

## Processus Lineage 2

Certains clients utilisent `L2.bin` ou un autre nom. Plusieurs processus peuvent être indiqués avec une virgule ou un point-virgule :

```text
L2.exe;L2.bin
```

## Réseau

- TCP `45880` : connexion persistante et diffusion des actions ;
- UDP `45881` : découverte automatique du Main ;
- clé d'appairage vérifiée avant l'ajout d'une Box ;
- reconnexion automatique après une coupure ;
- commandes sérialisées afin de conserver leur ordre.

Le protocole est prévu pour un réseau local privé et n'est pas chiffré. Ne redirige pas ses ports sur Internet.

## Dépannage Logitech

### `IbSendInit(Logitech) a échoué avec le code 6`

Le projet source recommandait Logitech Gaming Software `9.02.65`, puis un redémarrage de Windows et le backend `Logitech`. Avec certaines installations G Hub, essaie `LogitechGHubNew`.

### DLL impossible à charger

- vérifie que `IbInputSimulator.dll` est dans le dossier du projet ;
- utilise Python 3 en version 64 bits ;
- installe le Microsoft Visual C++ Redistributable 2015-2022 x64 si nécessaire.

### Une fenêtre L2 ne reçoit pas la touche

- lance le jeu et Box Assist avec le même niveau de privilèges ;
- lis le compteur `x/y` et le motif exact dans le journal ;
- vérifie le nom du processus configuré ;
- teste d'abord les touches localement depuis la Box.

## Fichiers principaux

- `main_assist.py` : interface du Main et écoute des touches ;
- `box_assist.py` : comportement historique à une fenêtre ;
- `box_assist_multi.py` : lanceur Box actuel et mode multi-fenêtres optionnel ;
- `window_targeting.py` : détection, activation et restauration des fenêtres ;
- `assist_network.py` : réseau, découverte, appairage et reconnexion ;
- `logitech_input.py` : wrapper minimal de `IbInputSimulator.dll` ;
- `tests/` : tests unitaires et réseau.

## Limites

Le programme ne reconnaît pas les mobs, ne lit pas leurs points de vie, ne choisit aucune cible et ne lance aucune action sans réception d'une commande configurée.

Windows, le client Lineage 2 ou son système anti-triche peuvent refuser certains changements de focus. Le programme vérifie le résultat avant chaque injection, mais le comportement réel doit être validé sur le client utilisé.

Vérifie les règles du serveur : le multiboxing et la diffusion de touches peuvent être réglementés différemment selon les serveurs.
