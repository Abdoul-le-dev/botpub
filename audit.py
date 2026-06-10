#!/usr/bin/env python3
"""
audit_schema_bigint.py
----------------------
1. Détecte les colonnes Telegram ID encore en INT
2. Pour chaque table à corriger : vérifie données corrompues + ALTER TABLE prêts
3. Vérifie aussi les données dans le .db pour ces mêmes tables
"""

import sqlite3
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import sys

SQLITE_FILE  = "preinscriptions.db"
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

INT_MAX = 2147483647

TELEGRAM_ID_COLS = {
    "telegram_id", "id_user", "user_id", "chat_id",
    "sender_id", "receiver_id", "from_id", "to_id",
}

def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  AUDIT SCHÉMA BIGINT — {ts}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} SQLite : {SQLITE_FILE}")
    except Exception as e:
        print(f"  {RED}[ERREUR]{RESET} SQLite : {e}"); sys.exit(1)

    try:
        conn = mysql.connector.connect(**MYSQL_CONFIG)
        cur  = conn.cursor()
        print(f"  {GREEN}[OK]{RESET} MySQL  : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"  {RED}[ERREUR]{RESET} MySQL : {e}"); sys.exit(1)

    # ── Récupère toutes les colonnes ───────────────────────────────────────
    cur.execute("""
        SELECT TABLE_NAME, COLUMN_NAME, COLUMN_TYPE, IS_NULLABLE, COLUMN_DEFAULT
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = %s
        ORDER BY TABLE_NAME, ORDINAL_POSITION
    """, (MYSQL_CONFIG["database"],))
    all_cols = cur.fetchall()

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

    # ── Tables à corriger — données ────────────────────────────────────────
    if need_fix:
        print(f"  {RED}{BOLD}TABLES À CORRIGER ({len(need_fix)} colonnes) :{RESET}\n")
        print("  " + "─" * 70)

        for table, col, ctype, nullable, default in need_fix:
            base_type = ctype.lower().split("(")[0].split(" ")[0]
            print(f"\n  {RED}✗{RESET} {BOLD}{table}.{col}{RESET}  {RED}[{ctype}]{RESET}")

            # Données corrompues dans MySQL
            if base_type in ("int", "integer", "mediumint", "smallint", "tinyint"):
                try:
                    cur.execute(
                        f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` = %s",
                        (INT_MAX,)
                    )
                    n_corrupted = cur.fetchone()[0]
                    if n_corrupted:
                        print(f"    {RED}► {n_corrupted} ligne(s) corrompues (= 2147483647) dans MySQL{RESET}")
                    else:
                        print(f"    {GREEN}✓ Aucune valeur 2147483647 dans MySQL{RESET}")

                    cur.execute(
                        f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` > %s",
                        (INT_MAX,)
                    )
                    n_over = cur.fetchone()[0]
                    if n_over:
                        print(f"    {GREEN}✓ {n_over} valeur(s) > INT_MAX déjà correctes dans MySQL{RESET}")
                except Error as e:
                    print(f"    {YELLOW}⚠ Impossible de vérifier MySQL : {e}{RESET}")
            else:
                print(f"    {YELLOW}⚠ Type {ctype} — pas de vérification overflow{RESET}")

            # Données dans le .db
            try:
                sq_cur.execute(
                    f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` > ?",
                    (INT_MAX,)
                )
                n_sq_over = sq_cur.fetchone()[0]
                sq_cur.execute(f"SELECT COUNT(*) FROM `{table}`")
                n_sq_total = sq_cur.fetchone()[0]
                if n_sq_over:
                    print(f"    {CYAN}► {n_sq_over}/{n_sq_total} lignes avec {col} > INT_MAX dans le .db{RESET}")
                else:
                    print(f"    {DIM}  Aucun {col} > INT_MAX dans le .db ({n_sq_total} lignes totales){RESET}")
            except Exception:
                print(f"    {DIM}  Table absente du .db{RESET}")

            # ALTER TABLE
            null_clause    = "NULL" if nullable == "YES" else "NOT NULL"
            default_clause = f" DEFAULT {default}" if default is not None else ""
            print(f"    {CYAN}→ ALTER TABLE `{table}` MODIFY COLUMN `{col}` BIGINT {null_clause}{default_clause};{RESET}")

        print()
    else:
        print(f"  {GREEN}✓ Aucune colonne Telegram ID encore en INT{RESET}\n")

    # ── Tables déjà OK ─────────────────────────────────────────────────────
    print(f"\n  {BOLD}Déjà en BIGINT ({len(already_ok)}) :{RESET}\n")
    print(f"  {'Table':<35} {'Colonne':<25} {'Type'}")
    print("  " + "─" * 65)
    for table, col, ctype in already_ok:
        print(f"  {GREEN}✓{RESET} {table:<35} {col:<25} {GREEN}{ctype}{RESET}")

    # ── Résumé final ───────────────────────────────────────────────────────
    print(f"\n{BOLD}{'='*70}{RESET}")
    if need_fix:
        print(f"  {RED}► {len(need_fix)} colonne(s) à migrer en BIGINT{RESET}")
        print(f"  {BOLD}Ordre d'opérations :{RESET}")
        print(f"  {DIM}  1. Exécute les ALTER TABLE ci-dessus (schéma){RESET}")
        print(f"  {DIM}  2. Lance le script de correction des données (2147483647){RESET}")
        print(f"  {DIM}  3. Relance cet audit pour confirmer{RESET}")
    else:
        print(f"  {GREEN}► Schéma OK — toutes les colonnes Telegram ID sont en BIGINT ✓{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    sq_cur.close()
    sq_conn.close()
    cur.close()
    conn.close()


if __name__ == "__main__":
    run()