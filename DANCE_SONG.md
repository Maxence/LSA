# Dance/Song ciblé (v2.4)

Cette option ajoute une troisième action indépendante, sans changer Attaquer et Suivre. Elle est désactivée par défaut sur le Main. Elle ne programme aucune répétition : un nouvel appui déclenche un seul envoi de la touche Box (par exemple la touche d'une macro déjà placée dans L2).

## Configuration sur les trois PC

Mettre à jour les scripts sur le PC Main et sur les deux PC Box, puis relancer les lanceurs habituels. Aucun changement du client L2, de la DLL, des ports ou de la clé d'appairage n'est nécessaire. Les fichiers de réglages existants ne sont pas remplacés par cette mise à jour.

Sur le **Main** :

1. Cocher **Activer Dance/Song ciblé (optionnel)**.
2. Renseigner **Touche Main - Dance/Song**, par exemple `F10`.
3. Renseigner **Pseudo exact Dance/Song**, par exemple `Muraki` si c'est le personnage à utiliser.
4. Cliquer **Enregistrer et redémarrer**.

Sur la **Box qui héberge le personnage** : renseigner **Touche Box - Dance/Song**, par exemple `F10`, puis cliquer **Enregistrer et reconnecter**. La touche Main et la touche Box peuvent être différentes. La valeur Box initiale est `F10`.

L'option multi-fenêtres peut rester activée sur les deux Box. Son libellé devient **Envoyer Attaquer et Suivre à tous les L2.exe ouverts sur cette Box**, pour préciser que Dance/Song n'est jamais envoyé à toutes les fenêtres.

Les changements s'appliquent après enregistrement, comme les autres réglages. **Tester Dance/Song** utilise la configuration déjà appliquée sur le Main, pas un pseudo modifié mais non enregistré.

## Sélection de la fenêtre

Le message réseau contient l'action `dance_song` et le champ `target_character`. Chaque Box recherche le **titre complet** dans les fenêtres du processus configuré (`L2.exe` par défaut). La casse et les espaces en début/fin sont ignorés ; `MurakiAlt` ne correspond pas à `Muraki`.

La Box qui ne possède pas ce titre ne change pas le focus et n'envoie aucune touche. Son accusé de réception indique **Ignorée : personnage absent sur cette Box, aucune touche envoyée**. Le nombre de Box affiché dans le journal du Main compte les messages transmis, pas les injections réalisées.

Sur la Box concernée, même si un autre client L2 ou une autre application est actif, le programme :

1. exige une seule fenêtre locale correspondant au pseudo ;
2. mémorise la fenêtre active et active le bon L2 ;
3. vérifie à nouveau l'unicité de la cible après stabilisation du focus ;
4. recontrôle le titre, le processus, l'identité de la fenêtre et le focus avant l'appui, y compris après l'initialisation du driver ;
5. envoie uniquement la touche Dance/Song configurée sur cette Box ;
6. tente de restaurer la fenêtre précédemment active et l'état minimisé éventuel du client ciblé.

Une cible absente, mal renseignée, fermée, remplacée, renommée ou ambiguë localement ne provoque jamais de repli sur la fenêtre active ni d'envoi Attaquer/Suivre. Si Windows refuse le focus ou si celui-ci est perdu avant l'envoi, la touche est annulée. Toutes les actions d'une même instance Box partagent un verrou afin d'éviter des changements de focus concurrents entre une commande réseau et un test local.

## Compatibilité et limites

Le protocole reste en version 2 pour conserver Attaquer et Suivre entre versions. Un nouveau Main n'envoie pas Dance/Song aux anciennes Box qui n'annoncent pas cette action ; le journal indique qu'une mise à jour est nécessaire. Mettre les trois PC à jour reste la procédure recommandée.

Cette fonction est prévue pour **une instance Box par PC** et un pseudo qui identifie un seul personnage parmi ces PC. Deux PC affichant exactement le même pseudo (par exemple sur des serveurs différents) pourraient tous deux agir. L'ambiguïté est contrôlée sur chaque PC, pas à l'échelle de plusieurs serveurs.

La touche Main reste une touche physique reçue normalement par le jeu du Main, comme les déclencheurs Attaquer/Suivre. Le programme ne la bloque pas. Le raccourci Dance/Song doit utiliser une touche physique différente des deux autres raccourcis Main.

Les changements de focus restent soumis à Windows et au client L2. Le contrôle est effectué juste avant l'appui ; il ne constitue pas un verrou système empêchant un utilisateur ou une autre application de changer le focus. L'envoi de la touche ne confirme pas que le jeu a effectivement lancé le sort ou la macro. Respecter les règles du serveur utilisé.

## Vérification manuelle

Avec deux clients sur la Box ciblée, mettre l'autre personnage au premier plan puis tester Dance/Song depuis le Main. Seul le personnage choisi doit recevoir la touche, puis la fenêtre précédente doit revenir. Refaire le test avec le client ciblé minimisé, puis fermé. La seconde Box doit rester sans injection ni changement de focus. Vérifier ensuite qu'Attaquer et Suivre fonctionnent toujours sur tous les clients.

Les tests automatisés couvrent le transport TCP Main/deux Box, quatre fenêtres simulées, les anciens réglages, les raccourcis, la sélection exacte, la perte de focus, les erreurs du driver et les vrais widgets Tk. Les fenêtres L2 et le driver d'injection sont simulés dans ces tests : la vérification en jeu reste nécessaire.
