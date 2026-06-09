
"""
migrate_to_mysql.py
-------------------
Crée toutes les tables de preincristion.db dans une base MySQL.

Usage :
    pip install mysql-connector-python
    python migrate_to_mysql.py
"""

import re
import os
import mysql.connector
from mysql.connector import Error

# ─────────────────────────────────────────────
#  CONFIG  –  adapte ces valeurs
# ─────────────────────────────────────────────
DB_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",   # ← ton mot de passe MySQL
    "database": "fdkvip_db",       # ← nom de ta base
}

SQL_FILE = "database/table.sql"      # doit être dans le même dossier
# ─────────────────────────────────────────────


def parse_sql(path: str) -> list:
    """
    Parse un fichier SQL en ignorant les commentaires --
    et en gérant les blocs DELIMITER $$ (triggers).
    """
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    statements = []
    delimiter  = ";"
    buffer     = ""

    for line in lines:
        stripped = line.strip()

        # Ignorer les lignes de commentaires purs
        if stripped.startswith("--") or stripped.startswith("#"):
            continue

        # Changement de délimiteur (ex: DELIMITER $$)
        if re.match(r"^DELIMITER\s+", stripped, re.IGNORECASE):
            if buffer.strip():
                statements.append(buffer.strip())
                buffer = ""
            delimiter = stripped.split()[1]
            continue

        buffer += line

        # Le buffer se termine par le délimiteur courant
        if buffer.rstrip("\n").rstrip().endswith(delimiter):
            stmt = buffer.strip()
            if stmt.endswith(delimiter):
                stmt = stmt[: -len(delimiter)].strip()
            if stmt:
                statements.append(stmt)
            buffer    = ""
            delimiter = ";"   # reset après chaque statement

    # Dernier fragment éventuel
    if buffer.strip():
        stmt = buffer.strip()
        if stmt.endswith(delimiter):
            stmt = stmt[: -len(delimiter)].strip()
        if stmt:
            statements.append(stmt)

    return statements


def label(stmt: str) -> str:
    m = re.search(r"TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", stmt, re.IGNORECASE)
    if m:
        return m.group(1)
    m2 = re.search(r"TRIGGER\s+`?(\w+)`?", stmt, re.IGNORECASE)
    if m2:
        return f"TRIGGER {m2.group(1)}"
    return stmt[:50]


def run_migration():
    print("=" * 60)
    print("  Migration SQLite → MySQL")
    print("=" * 60)

    if not os.path.exists(SQL_FILE):
        print(f"[ERREUR] Fichier SQL introuvable : {SQL_FILE}")
        print(f"         Mets schema_mysql.sql dans le même dossier que ce script.")
        return

    statements = parse_sql(SQL_FILE)
    print(f"[INFO] {len(statements)} statements détectés\n")

    try:
        conn   = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()
        print(f"[OK] Connecté à MySQL – base : {DB_CONFIG['database']}\n")
    except Error as e:
        print(f"[ERREUR] Connexion MySQL : {e}")
        return

    success = skipped = errors = 0

    for i, stmt in enumerate(statements, 1):
        clean      = stmt.strip()
        first_word = clean.split()[0].upper() if clean.split() else ""
        lbl        = label(clean)

        try:
            cursor.execute(clean)
            conn.commit()
            print(f"  [{i:03d}] ✓  {first_word:10}  {lbl}")
            success += 1
        except Error as e:
            msg = str(e).lower()
            if "already exists" in msg:
                print(f"  [{i:03d}] ~  (existante)  {lbl}")
                skipped += 1
            else:
                print(f"  [{i:03d}] ✗  ERREUR : {e}")
                print(f"         SQL : {clean[:120]}\n")
                errors += 1

    cursor.close()
    conn.close()

    print("\n" + "=" * 60)
    print(f"  Résultat : ✓ {success} OK  |  ~ {skipped} ignorés  |  ✗ {errors} erreurs")
    print("=" * 60)

    if errors == 0:
        print("\n✅ Migration terminée avec succès !")
    else:
        print(f"\n⚠️  {errors} erreur(s) – vérifie les lignes ✗ ci-dessus.")


if __name__ == "__main__":
    run_migration()