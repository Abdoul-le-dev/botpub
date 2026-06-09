"""
db.py — Connexion MySQL avec pool de connexions.

Usage :
    from db import get_db

    with get_db() as conn:
        rows = conn.execute("SELECT ...").fetchall()

- Pool de 10 connexions simultanées (configurable)
- Écritures parallèles sans blocage
- Interface identique à l'ancienne version SQLite
- Auto-commit / rollback garanti
"""

import mysql.connector
from mysql.connector import Error, pooling
from contextlib import contextmanager

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",   # ← ton mot de passe MySQL
    "database": "fdkvip_db",
    "charset":  "utf8mb4",
}

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