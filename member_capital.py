"""
member_capital.py — Capital personnel sauvegardé par le membre (opt-in, permanent).

Différent de Money management (calcul à la demande dans
interactive_tools.py, rien n'est stocké) : ici le membre choisit
EXPLICITEMENT de sauvegarder son capital (bouton "💾 Sauvegarder") pour
recevoir des notifications de gestion du trade à chaque TP1/TP2/TP3
atteint sur les signaux suivants.

PERMANENT : pas d'expiration hebdomadaire (contrairement à l'ancien
capital hebdo ou au disclaimer). Reste valable jusqu'à ce que le
membre sauvegarde une nouvelle valeur.

Le SL reste TOUJOURS silencieux — aucune notification membre, même
pour ceux qui ont sauvegardé leur capital (comportement conservé de
l'ancien tp_notifier.py).

TABLE
    CREATE TABLE IF NOT EXISTS member_capital (
        user_id    BIGINT PRIMARY KEY,
        capital    DECIMAL(12,2) NOT NULL,
        updated_at DATETIME NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""

from __future__ import annotations

import logging

from db import get_db

logger = logging.getLogger(__name__)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS member_capital (
    user_id    BIGINT PRIMARY KEY,
    capital    DECIMAL(12,2) NOT NULL,
    updated_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def ensure_schema():
    async with get_db() as cur:
        await cur.execute(SCHEMA_SQL)
    logger.info("[member_capital] schéma member_capital OK")


async def save_capital(user_id: int, capital: float):
    async with get_db() as cur:
        await cur.execute("""
            INSERT INTO member_capital (user_id, capital, updated_at)
            VALUES (%s, %s, NOW())
            AS new_vals
            ON DUPLICATE KEY UPDATE
                capital    = new_vals.capital,
                updated_at = new_vals.updated_at
        """, (user_id, capital))


async def get_capital(user_id: int) -> float | None:
    async with get_db() as cur:
        await cur.execute("SELECT capital FROM member_capital WHERE user_id = %s", (user_id,))
        row = await cur.fetchone()
    return float(row["capital"]) if row else None


async def delete_capital(user_id: int):
    async with get_db() as cur:
        await cur.execute("DELETE FROM member_capital WHERE user_id = %s", (user_id,))


async def get_all_capitals() -> dict[int, float]:
    """Un seul SELECT pour tous les membres opt-in — utilisé en batch
    par trade_management_notifs.py à chaque TP touché."""
    async with get_db() as cur:
        await cur.execute("SELECT user_id, capital FROM member_capital")
        rows = await cur.fetchall()
    return {int(r["user_id"]): float(r["capital"]) for r in rows}