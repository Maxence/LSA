# Mode multi-fenêtres L2

Le mode multi-fenêtres est une option **uniquement côté Box Assist**. Il est désactivé par défaut et ne change ni le Main Assist ni le protocole réseau.

Quand l'option **Envoyer chaque commande à tous les L2.exe ouverts sur cette Box** est activée, la Box :

1. repère les fenêtres visibles appartenant au processus configuré (`L2.exe` par défaut) ;
2. garde la fenêtre actuellement active en mémoire, même si c'est Box Assist ou une autre application ;
3. donne brièvement le focus à chaque client Lineage 2 ;
4. vérifie que la fenêtre ciblée possède réellement le focus ;
5. envoie la touche Attaquer ou Suivre avec `IbInputSimulator.dll` et le backend Logitech ;
6. restaure la fenêtre qui était active avant la commande.

Le mode multi-fenêtres ne demande donc plus qu'un client L2 soit déjà au premier plan. Un message comme `fenêtre active python.exe` concernait l'ancien comportement à une seule fenêtre et n'indique pas une erreur de touche.

La case **Ne jamais injecter une touche si Lineage 2 n'est pas au premier plan** continue de protéger le mode simple, lorsque l'option multi-fenêtres est désactivée.

## Touches Box par défaut

Sur un clavier français AZERTY :

- Attaquer : `-`, la touche physique `6` ;
- Suivre : `&`, la touche physique `1`.

Les valeurs restent modifiables dans l'interface.

## Lancement

Utiliser `START_BOX.bat` ou `START_BOX_ADMIN.bat`. Les deux lanceurs ouvrent `box_assist_multi.py`.

Les réglages de la Box sont enregistrés dans :

```text
%LOCALAPPDATA%\LSA\box_settings.json
```

L'ancien fichier présent dans le dépôt est importé automatiquement au premier lancement afin de conserver l'adresse du Main et les autres paramètres.

## Limites

Windows doit accepter le changement de fenêtre au premier plan. Le script vérifie le focus avant chaque injection et n'envoie aucune touche à une fenêtre qu'il n'a pas réussi à activer. Selon le client Lineage 2, l'anti-triche ou les règles de focus de Windows, le changement automatique peut être refusé.

Une fenêtre qui était minimisée est restaurée le temps de l'envoi puis minimisée à nouveau. Un léger clignotement peut donc être visible pendant la séquence.
