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


async def init_pool():
    """Appeler une seule fois au démarrage (dans le lifespan FastAPI)."""
    global _pool
    _pool = await aiomysql.create_pool(
        minsize=2,
        maxsize=POOL_SIZE,
        **DB_CONFIG,
    )


async def close_pool():
    """Appeler à l'arrêt (dans le lifespan FastAPI)."""
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()


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
POOL_SIZE = 10   # connexions simultanées max
# ─────────────────────────────────────────────

# Initialisation du pool (une seule fois au démarrage)
_pool = pooling.MySQLConnectionPool(
    pool_name="fdkvip_pool",
    pool_size=POOL_SIZE,
    pool_reset_session=True,
    **DB_CONFIG
)


class _CursorWrapper:
    """
    Wrapper curseur : traduit automatiquement la syntaxe SQLite → MySQL.
    """

    # Traductions statiques appliquées sur chaque requête
    _REPLACEMENTS = [
        ("?",                           "%s"),
        ("INSERT OR IGNORE INTO",       "INSERT IGNORE INTO"),
        ("INSERT OR REPLACE INTO",      "REPLACE INTO"),
        ("SELECT changes()",            "SELECT ROW_COUNT()"),
        ("datetime('now')",             "NOW()"),
        ("datetime('now', 'localtime')", "NOW()"),
    ]

    def __init__(self, cursor):
        self._cur = cursor

    def _translate(self, sql: str) -> str:
        for old, new in self._REPLACEMENTS:
            sql = sql.replace(old, new)
        return sql

    def execute(self, sql: str, params=None):
        # Ignorer les PRAGMA SQLite
        if sql.strip().upper().startswith("PRAGMA"):
            return self
        # Ignorer les CREATE TABLE (schéma déjà créé)
        if sql.strip().upper().startswith("CREATE TABLE"):
            return self

        sql = self._translate(sql)

        try:
            if params is not None:
                self._cur.execute(sql, params)
            else:
                self._cur.execute(sql)
        except Error as e:
            raise RuntimeError(f"[DB] Erreur SQL : {e}\nSQL : {sql[:200]}")

        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    def fetchmany(self, size):
        return self._cur.fetchmany(size)

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return self._cur.rowcount


class _ConnWrapper:
    """
    Wrapper connexion : délègue execute() au curseur interne.
    """

    def __init__(self, connection):
        self._conn    = connection
        self._cur     = connection.cursor(dictionary=True)
        self._wrapper = _CursorWrapper(self._cur)

    def execute(self, sql: str, params=None):
        return self._wrapper.execute(sql, params)

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        try:
            self._cur.close()
        except Exception:
            pass
        try:
            self._conn.close()   # remet la connexion dans le pool
        except Exception:
            pass


@contextmanager
def get_db():
    """
    Récupère une connexion du pool, yield, commit ou rollback, remet dans le pool.

    Thread-safe — plusieurs coroutines/threads peuvent appeler get_db()
    simultanément sans se bloquer.
    """
    try:
        raw_conn = _pool.get_connection()
    except Error as e:
        raise RuntimeError(f"[DB] Pool épuisé ou indisponible : {e}")

    conn = _ConnWrapper(raw_conn)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()