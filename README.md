# Cellarium

Gestionnaire de cave à vin **libre (MIT)**, **auto-hébergeable** et **pensé pour le mobile**, avec une
**intégration IA via MCP**. Inspiré de la gestion de cave de Vitiplace : sobre, rapide, efficace.

*English version: [README.en.md](README.en.md).*

## Fonctionnalités

- **Synthèse** : bouteilles par type (rouge, blanc, rosé, effervescent, liquoreux, cidre — types
  personnalisables), valeur d'achat, échéancier de l'année, vins à boire en priorité, remplissage des rangements.
- **Ma cave**, quatre modes :
  - **Liste** triable et filtrable : Appellation, Nom du vin, Millésime, Type, Volume, Prix, Stock
    (cartes sur téléphone) ;
  - **Échéancier** : à conserver, à boire, à l'apogée, en décroissance (+ « dépassé », « non renseigné ») ;
  - **Plan** : vos armoires dessinées en grille (lignes × colonnes, colonnes en chiffres romains), bouteilles
    colorées par type, recherche qui met les cases en surbrillance, déplacement au doigt ou par glisser-déposer,
    échange de deux bouteilles, **cartons** (rangement en vrac) et bouteilles non rangées ;
  - **Historique** : entrées, sorties (bue, offerte, perdue), déplacements, avec auteur et possibilité de
    remettre une bouteille en cave.
- **Fiche vin** : nom, appellation, région, pays, producteur, type, cépages, température, description,
  accords, notes personnelles, image (photo ou **génération IA** optionnelle), dates de garde par millésime,
  historique des achats (date, millésime, volume, quantité, prix, commentaire), bouteilles et emplacements.
- **Rangements** configurables : armoires (lignes, colonnes) et cartons ; ajout, redimensionnement (avec
  contrôle des bouteilles hors grille), suppression, ordre d'affichage.
- **Import** du CSV « liste de cave » de Vitiplace (et de tout CSV/tableur : colonnes reconnues en français
  ou en anglais, séparateur et encodage détectés), avec aperçu et import sans doublon ; **export** CSV et
  sauvegarde/restauration JSON.
- **Comptes** : plusieurs **propriétaires** (modification) et des comptes en **lecture seule** pour partager
  la cave. Tout le site exige une connexion.
- **MCP** (Model Context Protocol) : un assistant IA peut consulter la cave, retrouver une bouteille,
  enregistrer une sortie… et **compléter les fiches** selon un guide strict (aucune invention, sources
  obligatoires, données saisies par l'utilisateur protégées).
- **Multilingue** : français par défaut, anglais ; ajouter une langue = ajouter un fichier JSON.

## Démarrage rapide (local)

```bash
git clone https://github.com/dukyduke/cellarium.git
cd cellarium
./launch.sh                 # crée .venv au premier lancement, puis sert http://127.0.0.1:8770
```

Au premier démarrage, le terminal affiche un **code d'installation** : ouvrez http://127.0.0.1:8770, saisissez
ce code et créez le compte propriétaire. (Ou définissez `CELLARIUM_ADMIN_USER` / `CELLARIUM_ADMIN_PASSWORD`
dans `.env`.) Ensuite : *Réglages › Rangements* pour décrire vos armoires et cartons, puis
*Réglages › Import / export* pour importer l'export Vitiplace.

### Reprendre sa cave Vitiplace

Récupérez la liste de votre cave Vitiplace sous forme de tableau (colonnes
`Appellation, Nom, Millésime, Robe, Quantité`, par exemple en copiant le tableau « liste de cave » dans un
tableur) et enregistrez-la en **CSV** ou **XLSX**. L'import accepte aussi tout autre tableau dont les colonnes
portent des noms usuels (Nom, Type/Robe/Couleur, Millésime/Année, Quantité/Stock, Volume, Prix, Date d'achat,
Région, Producteur, Cépages, Commentaire…). Les bouteilles importées peuvent être rangées automatiquement dans
une armoire (premières cases libres) ou laissées « non rangées » ; placez-les ensuite depuis le plan.

## Hébergement sur un serveur

### Docker

```bash
cp .env.example .env        # ajustez (CELLARIUM_SECURE_COOKIES=true derrière HTTPS)
docker compose up -d --build
docker compose logs cellarium | grep "setup code"
```

Les données (base SQLite + images) sont dans `./data` : c'est le seul dossier à sauvegarder.

### systemd (sans Docker)

Voir [`deploy/cellarium.service`](deploy/cellarium.service) (instructions en tête de fichier) et l'exemple
de reverse proxy [`deploy/nginx.conf`](deploy/nginx.conf).

### Commandes d'administration

```bash
python -m cellarium create-user claire --role owner      # ou --role reader
python -m cellarium set-password claire
python -m cellarium list-users
python -m cellarium create-token claire hermes --scope write
python -m cellarium list-tokens
```

### Configuration

Toutes les variables sont décrites dans [`.env.example`](.env.example) : dossier de données, compte initial,
cookies sécurisés, et API de génération d'images (compatible OpenAI, facultative).

## Intégration IA (MCP)

1. *Réglages › Jetons API & MCP* : créez un jeton (`lecture` ou `écriture`).
2. Déclarez le serveur dans votre client MCP (transport *Streamable HTTP*) :

```yaml
# Hermes Agent (~/.hermes/config.yaml)
mcp_servers:
  cellarium:
    url: https://cave.example.org/mcp
    headers:
      Authorization: Bearer cel_xxxxxxxx
```

```json
{ "mcpServers": { "cellarium": { "url": "https://cave.example.org/mcp",
  "headers": { "Authorization": "Bearer cel_xxxxxxxx" } } } }
```

Outils exposés : `cellar_summary`, `search_wines`, `get_wine`, `find_bottles`, `drinking_schedule`,
`list_storages`, `get_history`, `list_wines_to_enrich`, `get_naming_conventions`, `get_enrichment_guide`,
`create_wine`, `update_wine_info`, `set_drinking_window`, `add_wine_comment`, `add_bottles`, `remove_bottles`,
`move_bottle` ; prompt `enrich_wine` ; ressource `cellarium://guides/wine-enrichment`.

- **Nommage** : avant de créer un vin, l'IA consulte `get_naming_conventions`, qui analyse les noms déjà
  présents dans la cave (formes « Nom - Cuvée », « Nom - Cuvée (domaine de) »…, désignations utilisées,
  exemples les plus proches) pour en déduire la règle. `create_wine` refuse un doublon et signale un nom
  qui s'écarte des habitudes de la cave.
- **Commentaires signés** : tout commentaire écrit par une IA (sur la fiche, sur un achat, à la sortie d'une
  bouteille) est préfixé `[IA date]` par le serveur et affiché avec un badge « IA » ; les notes existantes
  ne sont jamais modifiées.

Le **guide d'enrichissement** ([`cellarium/guides/wine-enrichment.md`](cellarium/guides/wine-enrichment.md),
au format skill, aussi disponible dans [`skills/cellarium-wine-enrichment/`](skills/cellarium-wine-enrichment/SKILL.md))
est servi par le MCP (outil `get_enrichment_guide`, prompt `enrich_wine`, ressource). Il impose à l'IA de ne remplir que des informations confirmées par des sources
citées, de laisser vide en cas de doute, et de ne jamais écraser une donnée saisie par l'utilisateur ;
le serveur le fait respecter (sources obligatoires, champs utilisateur protégés, champs « IA » signalés
avec leurs sources dans la fiche). Un jeton `lecture` ne peut rien modifier.

## Pour les IA (maintenance « vibe coding »)

Le dépôt contient [`AGENTS.md`](AGENTS.md) (architecture, conventions, règles) et une skill prête à l'emploi
[`skills/cellarium-maintenance/SKILL.md`](skills/cellarium-maintenance/SKILL.md) décrivant la procédure
pour faire évoluer le site sans le casser : où placer la logique, ajouter un champ, une langue, un outil MCP,
lancer les tests et vérifier dans un navigateur avant de committer.

## Développement

```bash
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
```

Pile : Python 3.11+ / FastAPI / SQLite (aucun service externe), interface HTML/CSS/JS en modules ES natifs
**sans étape de build**, SDK MCP officiel. API documentée sur `/api/docs`.

## Feuille de route

- Ajout d'un vin depuis le téléphone en photographiant l'étiquette (reconnaissance par IA).
- Notes de dégustation enrichies, statistiques de consommation.

## Licence

[MIT](LICENSE) © Guillaume Chevillot
