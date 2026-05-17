"""
db.py — Connexion SQLite centralisée pour tout le projet.

Usage :
    from db import get_db

    with get_db() as conn:
        rows = conn.execute("SELECT ...").fetchall()
        # commit automatique, fermeture garantie
"""

import sqlite3
from contextlib import contextmanager

DB_PATH = "preinscriptions.db"


@contextmanager
def get_db():
    """
    Connexion SQLite locale, thread-safe, fermée automatiquement.

    - WAL : plusieurs lecteurs simultanés sans blocage
    - busy_timeout : attend jusqu'à 5s si la base est verrouillée
    - row_factory : accès par nom de colonne (row["name"])
    - auto-commit si pas d'exception, rollback sinon
    """
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()