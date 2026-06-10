#!/usr/bin/env python3
"""
audit_schema_bigint.py
----------------------
Vérifie toutes les colonnes qui stockent des Telegram IDs
et détecte celles encore en INT (devrait être BIGINT).
"""

import mysql.connector
from mysql.connector import Error
from datetime import datetime
import sys

MYSQL_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",
    "database": "fdkvip_db",
}

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# Noms de colonnes qui stockent typiquement des Telegram IDs
TELEGRAM_ID_COLS = {
    "telegram_id", "id_user", "user_id", "chat_id",
    "sender_id", "receiver_id", "from_id", "to_id",
}

def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  AUDIT SCHÉMA BIGINT — {ts}{RESET}")
    print(f"{BOLD}  Colonnes Telegram ID encore en INT (devrait être BIGINT){RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cur  = conn.cursor()
        print(f"  {GREEN}[OK]{RESET} MySQL : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"  {RED}[ERREUR]{RESET} MySQL : {e}"); sys.exit(1)

    # Récupère toutes les colonnes de la base
    cur.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """, (MYSQL_CONFIG["database"],))

    all_cols = cur.fetchall()

    # Filtre : colonnes dont le nom ressemble à un Telegram ID
    to_check = [
        (table, col, ctype, nullable, default)
        for table, col, ctype, nullable, default in all_cols
        if col.lower() in TELEGRAM_ID_COLS
    ]

    need_fix   = []
    already_ok = []

    for table, col, ctype, nullable, default in to_check:
        base_type = ctype.lower().split("(")[0].split(" ")[0]
        if base_type == "bigint":
            already_ok.append((table, col, ctype))
        else:
            need_fix.append((table, col, ctype, nullable, default))

    # ── Tables à corriger ──────────────────────────────────────────────────
    if need_fix:
        print(f"  {RED}{BOLD}À CORRIGER — colonnes encore en INT :{RESET}\n")
        print(f"  {'Table':<35} {'Colonne':<25} {'Type actuel':<20} {'Nullable'}")
        print("  " + "─" * 85)
        for table, col, ctype, nullable, default in need_fix:
            print(f"  {RED}✗{RESET} {table:<35} {col:<25} {RED}{ctype:<20}{RESET} {nullable}")
        print()

        # ALTER TABLE suggérés
        print(f"  {BOLD}ALTER TABLE à exécuter :{RESET}\n")
        for table, col, ctype, nullable, default in need_fix:
            null_clause    = "NULL" if nullable == "YES" else "NOT NULL"
            default_clause = f" DEFAULT {default}" if default is not None else ""
            print(f"  {CYAN}ALTER TABLE `{table}` MODIFY COLUMN `{col}` BIGINT {null_clause}{default_clause};{RESET}")
        print()
    else:
        print(f"  {GREEN}✓ Aucune colonne Telegram ID encore en INT{RESET}\n")

    # ── Tables déjà OK ─────────────────────────────────────────────────────
    print(f"  {BOLD}Déjà en BIGINT ({len(already_ok)}) :{RESET}\n")
    print(f"  {'Table':<35} {'Colonne':<25} {'Type'}")
    print("  " + "─" * 65)
    for table, col, ctype in already_ok:
        print(f"  {GREEN}✓{RESET} {table:<35} {col:<25} {GREEN}{ctype}{RESET}")

    # ── Résumé ─────────────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*70}{RESET}")
    if need_fix:
        print(f"  {RED}► {len(need_fix)} colonne(s) à migrer en BIGINT{RESET}")
        print(f"  {DIM}  → Copie les ALTER TABLE ci-dessus et exécute-les{RESET}")
        print(f"  {DIM}  → Ensuite lance le script de correction des données corrompues{RESET}")
    else:
        print(f"  {GREEN}► Schéma OK — toutes les colonnes Telegram ID sont en BIGINT ✓{RESET}")
        print(f"  {DIM}  → Tu peux passer directement à la correction des données{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    cur.close()
    conn.close()


if __name__ == "__main__":
    run()