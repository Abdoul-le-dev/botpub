#!/usr/bin/env python3
"""
migrate_to_mysql.py
-------------------
Crée toutes les tables de preincristion.db dans une base MySQL.

Usage :
    pip install mysql-connector-python
    python migrate_to_mysql.py
"""

import os
import mysql.connector
from mysql.connector import Error

# ─────────────────────────────────────────────
#  CONFIG  –  adapte ces valeurs
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",          # ou ton user MySQL
    "password": "Fiacre2026@#", # mot de passe MySQL
    "database": "fdkvip_db",     # nom de ta base (doit déjà exister)
}

SQL_FILE = "database/table.sql"   # chemin vers le fichier SQL généré
# ─────────────────────────────────────────────


def read_sql_file(path: str) -> list[str]:
    """
    Lit le fichier SQL et retourne une liste de statements.
    Gère les blocs DELIMITER $$ pour les triggers.
    """
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    statements = []
    delimiter = ";"
    buffer = ""

    for line in content.splitlines():
        stripped = line.strip()

        # Changement de délimiteur (ex: DELIMITER $$)
        if stripped.upper().startswith("DELIMITER"):
            parts = stripped.split()
            if len(parts) == 2:
                delimiter = parts[1]
            continue

        buffer += line + "\n"

        if buffer.rstrip().endswith(delimiter):
            stmt = buffer.rstrip()
            if delimiter != ";":
                stmt = stmt[: -len(delimiter)]   # retire le délimiteur custom
            stmt = stmt.strip()
            if stmt and not stmt.startswith("--"):
                statements.append(stmt)
            buffer = ""
            delimiter = ";"  # reset après le bloc trigger

    # Dernier fragment éventuel
    if buffer.strip():
        statements.append(buffer.strip())

    return statements


def run_migration():
    print("=" * 60)
    print("  Migration SQLite → MySQL")
    print("=" * 60)

    # Lecture du fichier SQL
    if not os.path.exists(SQL_FILE):
        print(f"[ERREUR] Fichier SQL introuvable : {SQL_FILE}")
        return

    statements = read_sql_file(SQL_FILE)
    print(f"[INFO] {len(statements)} statements détectés\n")

    # Connexion MySQL
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print(f"[OK] Connecté à MySQL – base : {DB_CONFIG['database']}\n")
    except Error as e:
        print(f"[ERREUR] Connexion MySQL : {e}")
        return

    success = 0
    skipped = 0
    errors  = 0

    for i, stmt in enumerate(statements, 1):
        # Ignore les commentaires seuls et lignes vides
        clean = stmt.strip()
        if not clean or clean.startswith("--"):
            skipped += 1
            continue

        try:
            # mysql-connector ne supporte pas les multi-statements,
            # on exécute statement par statement
            cursor.execute(clean)
            conn.commit()
            # Affiche un résumé court
            first_word = clean.split()[0].upper()
            table_hint = ""
            if "TABLE" in clean.upper():
                parts = clean.upper().split("TABLE")
                if len(parts) > 1:
                    table_hint = parts[1].strip().split()[0].replace("IF", "").replace("NOT", "").replace("EXISTS", "").strip()
            print(f"  [{i:03d}] ✓  {first_word} {table_hint}")
            success += 1
        except Error as e:
            # Ignore les "table déjà existante" si IF NOT EXISTS est présent
            if "already exists" in str(e).lower():
                print(f"  [{i:03d}] ~  (déjà existante) {clean[:60]}…")
                skipped += 1
            else:
                print(f"  [{i:03d}] ✗  ERREUR : {e}")
                print(f"         SQL : {clean[:120]}…\n")
                errors += 1

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print(f"  Résultat  :  ✓ {success} OK  |  ~ {skipped} ignorés  |  ✗ {errors} erreurs")
    print("=" * 60)

    if errors == 0:
        print("\n✅ Migration terminée avec succès !")
    else:
        print(f"\n⚠️  {errors} erreur(s) – vérifie les lignes marquées ✗ ci-dessus.")


if __name__ == "__main__":
    run_migration()