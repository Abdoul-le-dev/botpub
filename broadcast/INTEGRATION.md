# Intégration — refactor broadcast v2

## 1. Migration SQL (une seule fois)

```bash
mysql -u <user> -p <db_name> < migration.sql
```

Elle est **idempotente** (`CREATE TABLE IF NOT EXISTS`) et **additive** :
elle ne touche PAS aux tables `users`, `categories`, `broadcast_history`.
Elle crée uniquement :

- `broadcast_stats` — stats détaillées par diffusion
- `broadcast_media_cache` — cache persistant des file_id Telegram

## 2. Arborescence à déployer

```
<racine_projet>/
├── broadcast_engine.py           # remplace l'ancien fichier v1
└── broadcast/
    ├── __init__.py
    ├── config.py
    ├── error_classifier.py
    ├── rate_limiter.py
    ├── media_cache.py
    ├── recipients.py
    ├── reports.py
    ├── cleanup.py
    └── worker.py
```

## 3. Variables d'environnement (optionnelles — valeurs par défaut OK)

```bash
# Admins qui reçoivent les notifs + les CSV + les boutons de nettoyage
BROADCAST_ADMIN_IDS=6992809421,571718066

# Rate limiter adaptatif
BROADCAST_RATE_TARGET=29.0          # débit cible en croisière
BROADCAST_RATE_MIN=25.0             # débit plancher après RetryAfter
BROADCAST_RATE_MAX=30.0             # plafond dur (jamais dépassé)
BROADCAST_RATE_RECOVERY_STEP=100    # nb d'envois OK avant remontée d'un cran
BROADCAST_RATE_RECOVERY_INC=0.5     # taille du cran de remontée (msg/s)

# Workers & queue
BROADCAST_WORKERS=32
BROADCAST_QUEUE_MAXSIZE=2000

# Rapports CSV
BROADCAST_REPORTS_DIR=/tmp/broadcast_reports

# TTL des tokens de cleanup admin (secondes)
BROADCAST_CLEANUP_TTL=86400
```

## 4. UNE ligne à ajouter dans `main.py`

Pour que les boutons `✅ Supprimer` / `❌ Ignorer` fonctionnent, il faut
enregistrer les 2 CallbackQueryHandlers au démarrage. À placer près de tes
autres `register_*_handlers(app)` :

```python
from broadcast.cleanup import register_broadcast_admin_handlers
# ...
register_broadcast_admin_handlers(app)
```

**Aucun conflit** avec tes handlers existants : les patterns sont préfixés
`bcclean:del:` et `bcclean:ign:`, qui n'entrent pas en collision avec
`level:`, `resume_registration`, etc.

## 5. Compatibilité — rien d'autre à toucher

Les modules externes qui appellent :

```python
from broadcast_engine import broadcast_engine, ADMIN_ID
result = await broadcast_engine(bot, payload)
```

fonctionnent **sans aucune modification**. La signature du `payload` et du
dict retourné est identique à la v1. Les nouvelles clés (`blocked`, `deleted`,
`success_rate`, etc.) s'ajoutent au dict de retour sans casser les lectures
existantes.

Deux clés du payload sont **acceptées mais ignorées** (log info) :

- `retry` : le brief impose zéro retry utilisateur.
- `delay` : le rate limiter global gère la cadence.

## 6. Ce que fait le moteur pendant un broadcast

```
1. Résout les destinataires (SQL selon category/user_ids/filters)
2. Si +prenom dans le message → 1 seule requête batch pour tous les prénoms
   Sinon → ZÉRO requête SQL de personnalisation
3. Vérifie le cache file_id Telegram pour le média (RAM + DB)
4. Notifie les admins du démarrage
5. Lance 32 workers async qui consomment une queue
6. Rate limiter global adaptatif (AIMD) régule la cadence à 28-30 msg/s
7. Sur RetryAfter : pause globale de TOUS les workers, retry ce message
   uniquement, débit redescend à 25, puis remonte progressivement
8. Sur autre erreur : classification (blocked/deleted/network/unknown),
   collecte dans un CSV, aucune retry, passe au suivant
9. À 50% : notification admin
10. À la fin : rapport texte + envoi des CSV (puis suppression)
11. Persistance dans broadcast_history (compat) + broadcast_stats (nouveau)
12. Si blocked/deleted détectés → message admin avec boutons de nettoyage
```

## 7. Rate limiter global — comportement inter-broadcasts

Le limiteur est un **singleton par process**. Si plusieurs broadcasts tournent
en parallèle, ils **partagent** la même contrainte 28-30 msg/s globale.
Deux broadcasts simultanés se partagent la bande passante, on ne dépasse
jamais Telegram.

## 8. Test rapide (dry-run léger)

```python
import asyncio
from broadcast_engine import broadcast_engine

async def test():
    result = await broadcast_engine(bot, {
        "message":  "Bonjour +prenom, ceci est un test.",
        "format":   "text",
        "user_ids": [571718066],   # juste toi
        "tag":      "test_v2",
    })
    print(result)

asyncio.run(test())
```
