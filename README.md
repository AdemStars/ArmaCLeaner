<p align="center"><img src="logo.png" width="180" alt="Arma Cleaner"></p>

<h1 align="center">Arma Cleaner</h1>

<p align="center">
Nettoie et allège tes mods <b>Arma 3</b> : treemap façon WinDirStat, filtres, favoris, listes HTML du launcher, compression NTFS.<br>
Un seul <code>.exe</code> portable pour Windows, sans installation.
</p>

---

## Pourquoi ?

Le launcher d'Arma 3 et le workshop n'offrent aucun moyen de supprimer efficacement plusieurs mods d'un coup,
ni de voir ce qui prend de la place. Arma Cleaner scanne tes mods et te laisse trier, cocher et nettoyer en quelques clics.

## Fonctionnalités

- **Treemap** (comme WinDirStat) : chaque rectangle est un mod, sa surface est proportionnelle à sa taille sur disque. Regroupés en Favoris / Workshop / Hors workshop.
- **Détection automatique** des bibliothèques Steam et du dossier Arma 3 (mods workshop `107410` et dossiers `@mod`).
- **Cocher, filtrer, trier** : recherche par nom ou ID, filtre par source, tri par taille, date, etc. Les mods cochés sont surlignés.
- **Bibliothèque de listes HTML** : importe tes presets du launcher (aucune action à l'import), puis « Cocher les mods de la liste » ou « Cocher tout **sauf** la liste ».
- **Favoris** ★ : protégés, jamais cochés ni supprimés.
- **Mods hors workshop** signalés (⚠, orange) car Steam ne pourra pas les retélécharger.
- **Dernier lancement** : lit les logs `.rpt` d'Arma 3 pour savoir quand chaque mod a été utilisé, et propose de filtrer « pas lancé depuis 30 / 90 / 180 jours / 1 an ».
- **Compression NTFS** transparente (`compact.exe`, algorithmes XPRESS4K/8K/16K ou LZX) avec fenêtre de progression, annulable. Le jeu reste jouable, réversible via « Décompresser ».
- **Clic droit** sur un mod : supprimer, compresser/décompresser, ouvrir le dossier, ouvrir la page Steam (via l'ID), copier l'ID, favori. Clic droit sur une liste : cocher, supprimer ou compresser ses mods, la retirer.
- **Thème clair / sombre**, réglages sauvegardés dans `%APPDATA%\ArmaCleaner\config.json`.

## Installation

1. Va dans les [**Releases**](../../releases) et télécharge `ArmaCleaner.exe`.
2. Lance-le. Rien à installer.

> L'exécutable n'est pas signé : Windows SmartScreen peut afficher un avertissement (« Informations complémentaires » puis « Exécuter quand même »).

## Utilisation rapide

1. Les mods sont détectés au lancement (sinon « Choisir dossier… »).
2. Coche des mods (clic sur la ligne, sur le rectangle, ou via un filtre / une liste HTML).
3. **Supprimer la sélection**, ou **Compresser** pour gagner de la place sans rien perdre.

Conseils :
- Ferme Steam et Arma 3 avant de supprimer ou compresser.
- Désabonne-toi des mods sur le workshop après suppression, sinon Steam les retélécharge.
- La compression NTFS rend peu sur les PBO (déjà compacts) et une mise à jour Steam peut l'annuler. Un mod « Inconnu » dans la colonne « Dernier lancement » n'est pas forcément jamais utilisé : Arma ne garde que quelques logs.

## Depuis les sources

```
python arma_cleaner.py
```

Python 3.8+ avec tkinter, aucune dépendance.

## Construire l'exécutable

```
build.bat
```

Produit `dist\ArmaCleaner.exe` (PyInstaller).

## Publier une release

```
git tag v1.0.0
git push --tags
```

GitHub Actions construit `ArmaCleaner.exe` et l'attache à la release.

## Avertissement

La suppression est **définitive** (pas de corbeille). Vérifie ta sélection avant de confirmer.
