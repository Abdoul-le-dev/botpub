"""
broadcast/recipients.py — résolution des destinataires + personnalisation.

Deux préoccupations :

1. Résoudre la liste finale des telegram_id à toucher, à partir du payload
   (mêmes règles qu'avant : `user_ids` explicite, ou `category`, ou "all",
   avec filtres `created_after` / `created_before` et exclusions).

2. Pré-charger les prénoms EN UNE SEULE FOIS si le message contient `+prenom`.
   Sinon, ZÉRO requête SQL de personnalisation — c'est l'optimisation majeure
   pour 100k users : on passe de 100 000 requêtes SELECT à 20 (chunks).
"""

from __future__ import annotations

import logging
from typing import Iterable, Optional

from db import get_db

from . import config

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# RÉSOLUTION DES DESTINATAIRES
# ══════════════════════════════════════════════════════════════════════════════

async def resolve_user_ids(
    category: Optional[str],
    user_ids: Optional[Iterable[int]],
    exclude_user_ids: Optional[Iterable[int]],
    filters: Optional[dict],
) -> list[int]:
    """
    Résout la liste finale des telegram_id destinataires.

    Règles (identiques à v1 pour compat) :
      - Si `user_ids` fourni, on l'utilise tel quel (moins les exclusions).
      - Sinon si `category == "all"` → tous les users avec telegram_id.
      - Sinon si `category` fourni → users de cette catégorie, avec filtres
        optionnels sur categories.created_at.
      - Sinon → liste vide.
    """
    exclude = set(exclude_user_ids or [])

    if user_ids:
        return [int(uid) for uid in user_ids if int(uid) not in exclude]

    async with get_db() as cur:
        if category == "all":
            await cur.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            )
            rows = await cur.fetchall()
            return [int(r["telegram_id"]) for r in rows if int(r["telegram_id"]) not in exclude]

        if category:
            query = "SELECT id_user FROM categories WHERE name_categorie = %s"
            params: list = [category]

            if filters:
                if filters.get("created_after"):
                    query += " AND created_at >= %s"
                    params.append(filters["created_after"])
                if filters.get("created_before"):
                    query += " AND created_at <= %s"
                    params.append(filters["created_before"])

            await cur.execute(query, params)
            rows = await cur.fetchall()
            return [int(r["id_user"]) for r in rows if int(r["id_user"]) not in exclude]

    return []


# ══════════════════════════════════════════════════════════════════════════════
# PERSONNALISATION — chargement groupé
# ══════════════════════════════════════════════════════════════════════════════

def needs_prenom_lookup(text: str) -> bool:
    """True si le message contient le placeholder +prenom (donc nécessite la DB)."""
    return bool(text) and config.PLACEHOLDER_PRENOM in text


async def batch_fetch_prenoms(user_ids: list[int]) -> dict[int, str]:
    """
    Récupère les prénoms de TOUS les user_ids en une poignée de requêtes
    (chunks de PRENOM_BATCH_SIZE). Retourne un dict {telegram_id: prénom_valide}.

    Un prénom n'est retenu que s'il fait entre PRENOM_MIN_LEN et PRENOM_MAX_LEN
    caractères une fois strippé — sinon il sera remplacé par le fallback.
    """
    if not user_ids:
        return {}

    result: dict[int, str] = {}
    ids = list({int(u) for u in user_ids})  # dédupe

    for start in range(0, len(ids), config.PRENOM_BATCH_SIZE):
        chunk = ids[start:start + config.PRENOM_BATCH_SIZE]
        placeholders = ",".join(["%s"] * len(chunk))
        query = f"SELECT telegram_id, name FROM users WHERE telegram_id IN ({placeholders})"
        try:
            async with get_db() as cur:
                await cur.execute(query, chunk)
                rows = await cur.fetchall()
        except Exception as e:
            logger.warning(f"[prenoms] échec chunk {start} : {e}")
            continue

        for r in rows:
            name = (r.get("name") or "").strip()
            if config.PRENOM_MIN_LEN <= len(name) <= config.PRENOM_MAX_LEN:
                result[int(r["telegram_id"])] = name

    logger.info(f"[prenoms] {len(result)}/{len(ids)} prénoms valides chargés")
    return result


def inject_variables(
    text: str,
    telegram_id: int,
    prenoms: dict[int, str],
    variables: Optional[dict],
) -> str:
    """
    Injecte +prenom et variables custom dans le texte. Pas d'accès DB —
    tout vient de `prenoms` qui a été rempli en amont par batch_fetch_prenoms.
    """
    if not text:
        return text
    if config.PLACEHOLDER_PRENOM in text:
        prenom = prenoms.get(telegram_id, config.PRENOM_FALLBACK)
        text = text.replace(config.PLACEHOLDER_PRENOM, prenom)
    if variables:
        for key, value in variables.items():
            text = text.replace(key, str(value))
    return text
