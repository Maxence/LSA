# Mode multi-fenêtres L2

Le mode multi-fenêtres est une option **uniquement côté Box Assist**. Il est désactivé par défaut et ne change ni le Main Assist ni le protocole réseau.

Quand l'option **Envoyer chaque commande à tous les L2.exe ouverts sur cette Box** est activée, la Box :

1. repère les fenêtres visibles appartenant au processus configuré (`L2.exe` par défaut) ;
2. garde la fenêtre actuellement active en mémoire ;
3. donne brièvement le focus à chaque client Lineage 2 ;
4. envoie la touche Attaquer ou Suivre avec `IbInputSimulator.dll` et le backend Logitech ;
5. restaure la fenêtre qui était active avant la commande.

La sécurité **Ne jamais injecter une touche si Lineage 2 n'est pas au premier plan** continue de s'appliquer. Si elle reste cochée, le fan-out multi-fenêtres ne démarre que lorsqu'une fenêtre Lineage 2 est déjà active sur la Box.

## Lancement

Utiliser `START_BOX.bat` ou `START_BOX_ADMIN.bat`. Les deux lanceurs ouvrent désormais `box_assist_multi.py`.

## Limites

Windows doit accepter le changement de fenêtre au premier plan. Le script vérifie le focus avant chaque injection et n'envoie aucune touche à une fenêtre qu'il n'a pas réussi à activer. Selon le client Lineage 2, l'anti-cheat ou les règles de focus de Windows, le changement automatique peut être refusé.

Une fenêtre qui était minimisée est restaurée le temps de l'envoi puis minimisée à nouveau. Un léger clignotement de fenêtre peut donc être visible pendant la séquence.
