"""
db.py — Connexion MySQL ASYNC avec aiomysql.

Usage :
    from db import get_db

    async with get_db() as cur:
        await cur.execute("SELECT ...")
        rows = await cur.fetchall()

- Pool de 10 connexions simultanées
- Compatible FastAPI async (ne bloque PAS l'event loop)
- Auto-commit / rollback garanti
- DictCursor : rows retournés comme dicts (identique à l'ancienne version SQLite)
"""

import aiomysql
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":        "localhost",
    "port":        3306,
    "user":        "fiacrefdksignal",
    "password":    "Fiacre2026@#",
    "db":          "fdkvip_db",       # ← "db" et non "database" avec aiomysql
    "charset":     "utf8mb4",
    "autocommit":  False,
    "cursorclass": aiomysql.DictCursor,
}

POOL_SIZE = 10

# ─────────────────────────────────────────────
#  POOL (initialisé au démarrage via init_pool)
# ─────────────────────────────────────────────
_pool = None


async def _post_init(app):
    try:
        print("[post_init] Démarrage...")
        await init_pool()
        print("[post_init] Pool OK ✓")
        await setup_background_worker(app)
        print("[post_init] Worker OK ✓")
        asyncio.create_task(schedule_daily_check(app.bot))
        print("[post_init] Terminé ✓")
    except Exception as e:
        print(f"[post_init] ERREUR: {e}")
        import traceback
        traceback.print_exc()


async def close_pool():
    """Appeler à l'arrêt (dans le lifespan FastAPI)."""
    global _pool
    if _pool:
        _pool.close()
        #await _pool.wait_closed()


# ─────────────────────────────────────────────
#  GET_DB
# ─────────────────────────────────────────────
@asynccontextmanager
async def get_db():
    """
    Fournit un curseur DictCursor prêt à l'emploi.
    Commit automatique en fin de bloc, rollback sur exception.

    Exemple :
        async with get_db() as cur:
            await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = await cur.fetchone()
    """
    async with _pool.acquire() as conn:
        async with conn.cursor() as cur:
            try:
                yield cur
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise