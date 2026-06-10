#!/usr/bin/env python3
"""
Inspecte la structure et un échantillon des 3 tables à corriger
pour comprendre comment identifier et remplacer les lignes corrompues.
"""

import sqlite3
import mysql.connector
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

INT_MAX = 2147483647
TABLES  = ["categories", "categories_backup", "exam_user"]

RED   = "\033[91m"; GREEN = "\033[92m"; CYAN = "\033[96m"
BOLD  = "\033[1m";  DIM   = "\033[2m";  RESET = "\033[0m"

def run():
    sq_conn = sqlite3.connect(SQLITE_FILE)
    sq_conn.row_factory = sqlite3.Row
    sq_cur  = sq_conn.cursor()

    conn = mysql.connector.connect(**MYSQL_CONFIG)
    cur  = conn.cursor()

    for table in TABLES:
        print(f"\n{BOLD}{'='*70}{RESET}")
        print(f"{BOLD}  TABLE : {table}{RESET}")
        print(f"{BOLD}{'='*70}{RESET}")

        # Schéma SQLite
        sq_cur.execute(f"PRAGMA table_info(`{table}`)")
        sq_schema = sq_cur.fetchall()
        print(f"\n  {CYAN}Schéma SQLite :{RESET}")
        for row in sq_schema:
            pk = " ← PK" if row[5] else ""
            print(f"    {row[1]:<30} {row[2]}{pk}")

        # Schéma MySQL
        cur.execute("""
            SELECT COLUMN_NAME, COLUMN_TYPE, COLUMN_KEY, IS_NULLABLE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
        """, (MYSQL_CONFIG["database"], table))
        my_schema = cur.fetchall()
        print(f"\n  {CYAN}Schéma MySQL :{RESET}")
        for col, ctype, key, nullable in my_schema:
            key_str = f" ← {key}" if key else ""
            print(f"    {col:<30} {ctype:<20} nullable={nullable}{key_str}")

        # Index / contraintes MySQL
        cur.execute(f"SHOW INDEX FROM `{table}`")
        indexes = cur.fetchall()
        if indexes:
            print(f"\n  {CYAN}Index MySQL :{RESET}")
            for idx in indexes:
                print(f"    {idx[2]:<25} col={idx[4]}  unique={not idx[1]}")

        # Échantillon lignes corrompues dans MySQL
        cur.execute(
            f"SELECT * FROM `{table}` WHERE `{'id_user' if table != 'exam_user' else 'id_user'}` = %s LIMIT 5",
            (INT_MAX,)
        )
        # détecte la colonne id
        col_names = [d[0] for d in cur.description]
        rows = cur.fetchall()
        if rows:
            print(f"\n  {RED}Échantillon lignes corrompues MySQL (max 5) :{RESET}")
            print(f"  {', '.join(col_names)}")
            print("  " + "─" * 60)
            for row in rows:
                print(f"  {row}")

        # Échantillon lignes > INT_MAX dans SQLite
        id_col = "id_user" if table in ("categories", "categories_backup", "exam_user") else "user_id"
        sq_cur.execute(
            f"SELECT * FROM `{table}` WHERE `{id_col}` > ? LIMIT 5",
            (INT_MAX,)
        )
        sq_rows = sq_cur.fetchall()
        if sq_rows:
            print(f"\n  {GREEN}Échantillon lignes > INT_MAX SQLite (max 5) :{RESET}")
            sq_col_names = [d[0] for d in sq_cur.description]
            print(f"  {', '.join(sq_col_names)}")
            print("  " + "─" * 60)
            for row in sq_rows:
                print(f"  {tuple(row)}")

        # Y a-t-il une clé unique qui pourrait identifier la ligne sans l'id_user ?
        # Cherche doublons potentiels sur autres colonnes
        if table == "categories":
            cur.execute("""
                SELECT name_categorie, COUNT(*) as nb
                FROM categories WHERE id_user = %s
                GROUP BY name_categorie ORDER BY nb DESC
            """, (INT_MAX,))
            print(f"\n  {RED}Répartition des corrompus par name_categorie :{RESET}")
            for cat, nb in cur.fetchall():
                print(f"    {cat:<40} {nb:>6} lignes corrompues")

        if table == "exam_user":
            cur.execute("""
                SELECT exam_id, COUNT(*) as nb
                FROM exam_user WHERE id_user = %s
                GROUP BY exam_id ORDER BY nb DESC LIMIT 10
            """, (INT_MAX,))
            rows_exam = cur.fetchall()
            if rows_exam:
                print(f"\n  {RED}Répartition des corrompus par exam_id :{RESET}")
                for eid, nb in rows_exam:
                    print(f"    exam_id={eid}  →  {nb} lignes corrompues")

    sq_conn.close()
    conn.close()

if __name__ == "__main__":
    run()