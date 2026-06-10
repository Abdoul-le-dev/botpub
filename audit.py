#!/usr/bin/env python3
"""
audit_categories.py
-------------------
Vérifie que tout ce qui est dans le .db est bien dans MySQL.
Les id_user présents dans MySQL mais pas dans le .db sont ignorés (normal).
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
SEUIL = 100

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"


def get_category_counts(cur, is_mysql):
    cur.execute(
        "SELECT name_categorie, COUNT(*) "
        "FROM categories GROUP BY name_categorie ORDER BY COUNT(*) DESC"
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def get_members(cur, category, is_mysql):
    if is_mysql:
        cur.execute("SELECT id_user FROM categories WHERE name_categorie = %s", (category,))
    else:
        cur.execute("SELECT id_user FROM categories WHERE name_categorie = ?", (category,))
    return {row[0] for row in cur.fetchall()}


def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  AUDIT CATÉGORIES — {ts}{RESET}")
    print(f"{BOLD}  Vérification : .db → MySQL (sens unique){RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} SQLite : {SQLITE_FILE}")
    except Exception as e:
        print(f"  {RED}[ERREUR]{RESET} SQLite : {e}"); sys.exit(1)

    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} MySQL  : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"  {RED}[ERREUR]{RESET} MySQL : {e}"); sys.exit(1)

    sq_counts = get_category_counts(sq_cur, False)
    my_counts = get_category_counts(my_cur, True)
    all_cats  = sorted(set(sq_counts) | set(my_counts),
                       key=lambda c: sq_counts.get(c, 0), reverse=True)

    W = 38
    print(f"  {BOLD}{'Catégorie':<{W}} {'SQLite':>8} {'MySQL':>8}  Statut{RESET}")
    print("  " + "─" * 65)

    results        = []
    total_manquants = 0

    for cat in all_cats:
        sq_n = sq_counts.get(cat, 0)
        my_n = my_counts.get(cat, 0)

        # Membres du .db absents de MySQL (le seul problème qui compte)
        missing_in_mysql = set()
        if sq_n < SEUIL:
            sq_members   = get_members(sq_cur, cat, False)
            my_members   = get_members(my_cur, cat, True)
            missing_in_mysql = sq_members - my_members   # .db → MySQL uniquement

        n_missing = len(missing_in_mysql)
        total_manquants += n_missing

        # Sévérité : uniquement basée sur ce qui manque dans MySQL
        if cat not in my_counts:
            sev, icon = "critical", f"{RED}🔴{RESET}"
        elif n_missing > 0:
            sev, icon = "critical", f"{RED}🔴{RESET}"
        elif sq_n > my_n:
            # grandes catégories : on ne peut pas faire le diff, on signale l'écart
            sev, icon = "warning", f"{YELLOW}⚠️ {RESET}"
        else:
            sev, icon = "ok", f"{GREEN}✅{RESET}"

        small_tag = f" {CYAN}[< {SEUIL}]{RESET}" if sq_n < SEUIL else ""

        print(f"  {icon} {cat:<{W-2}} {sq_n:>8,} {my_n:>8,}{small_tag}")

        if n_missing > 0:
            ids  = ", ".join(str(x) for x in sorted(missing_in_mysql)[:15])
            more = f"  … +{n_missing-15} autres" if n_missing > 15 else ""
            print(f"  {' ':{W+6}} {RED}↳ {n_missing} id_user absents de MySQL :{RESET}")
            print(f"  {' ':{W+6}}   {ids}{more}")

        # Grande catégorie avec écart non vérifiable ligne par ligne
        if sq_n >= SEUIL and sq_n > my_n:
            diff = sq_n - my_n
            print(f"  {' ':{W+6}} {YELLOW}↳ {diff} lignes de plus dans .db (non détaillées, catégorie > {SEUIL}){RESET}")

        results.append((cat, sq_n, my_n, sev, n_missing))

    # ── Résumé ────────────────────────────────────────────────────────────────
    n_critical = sum(1 for r in results if r[3] == "critical")
    n_warning  = sum(1 for r in results if r[3] == "warning")
    n_ok       = sum(1 for r in results if r[3] == "ok")

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"  {BOLD}RÉSUMÉ{RESET}  "
          f"{RED}{n_critical} critique(s){RESET}  |  "
          f"{YELLOW}{n_warning} avertissement(s){RESET}  |  "
          f"{GREEN}{n_ok} OK{RESET}")
    if total_manquants:
        print(f"  {RED}► {total_manquants} id_user au total présents dans .db mais absents de MySQL{RESET}")
    else:
        print(f"  {GREEN}► Tous les id_user du .db sont présents dans MySQL ✓{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    # ── Section 2 : Audit overflow INT_MAX (2147483647) ──────────────────────
    INT_MAX = 2147483647

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  AUDIT OVERFLOW — id_user = 2147483647 (INT_MAX){RESET}")
    print(f"{BOLD}  Ces lignes sont des Telegram IDs > 32 bits écrasés lors de la migration{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    # 1. Lignes corrompues dans MySQL par catégorie
    my_cur.execute(
        "SELECT name_categorie, COUNT(*) as nb "
        "FROM categories WHERE id_user = %s "
        "GROUP BY name_categorie ORDER BY nb DESC",
        (INT_MAX,)
    )
    corrupted_by_cat = my_cur.fetchall()
    total_corrupted  = sum(r[1] for r in corrupted_by_cat)

    if corrupted_by_cat:
        print(f"  {RED}Lignes corrompues (id_user = 2147483647) dans MySQL :{RESET}\n")
        print(f"  {'Catégorie':<38} {'Nb lignes':>10}")
        print("  " + "─" * 50)
        for cat, nb in corrupted_by_cat:
            print(f"  {RED}✗{RESET} {cat:<38} {nb:>10,}")
        print(f"\n  {RED}► Total : {total_corrupted} lignes corrompues{RESET}\n")
    else:
        print(f"  {GREEN}Aucune ligne corrompue (2147483647) dans MySQL ✓{RESET}\n")

    # 2. IDs > INT_MAX dans le .db (les vrais IDs qui auraient subi l'overflow)
    sq_cur.execute(
        "SELECT DISTINCT id_user, name_categorie "
        "FROM categories WHERE id_user > ? "
        "ORDER BY id_user",
        (INT_MAX,)
    )
    overflow_sqlite = sq_cur.fetchall()

    if overflow_sqlite:
        print(f"  {CYAN}IDs > INT_MAX dans le .db (vrais IDs écrasés côté MySQL) :{RESET}\n")
        print(f"  {'id_user':<20} {'Catégorie':<38}")
        print("  " + "─" * 60)
        for uid, cat in overflow_sqlite:
            print(f"  {CYAN}{uid:<20}{RESET} {cat}")
        print(f"\n  {CYAN}► {len(overflow_sqlite)} entrée(s) concernée(s) dans le .db{RESET}\n")
    else:
        print(f"  {GREEN}Aucun ID > INT_MAX dans le .db{RESET}\n")

    # 3. IDs > INT_MAX dans MySQL (ceux qui ont bien passé après fix BIGINT)
    my_cur.execute(
        "SELECT DISTINCT id_user FROM categories "
        "WHERE id_user > %s ORDER BY id_user",
        (INT_MAX,)
    )
    overflow_mysql = [r[0] for r in my_cur.fetchall()]

    if overflow_mysql:
        ids_str = ", ".join(str(x) for x in overflow_mysql[:20])
        more    = f"  … +{len(overflow_mysql)-20} autres" if len(overflow_mysql) > 20 else ""
        print(f"  {GREEN}IDs > INT_MAX déjà corrects dans MySQL ({len(overflow_mysql)}) :{RESET}")
        print(f"  {ids_str}{more}\n")
    else:
        print(f"  {YELLOW}Aucun ID > INT_MAX dans MySQL — tous ont été écrasés{RESET}\n")

    # 4. users touchés ?
    my_cur.execute(
        "SELECT COUNT(*) FROM users WHERE telegram_id = %s", (INT_MAX,)
    )
    users_corrupted = my_cur.fetchone()[0]
    sq_cur.execute(
        "SELECT COUNT(*) FROM users WHERE telegram_id > ?", (INT_MAX,)
    )
    users_overflow_sqlite = sq_cur.fetchone()[0]

    print(f"  {'Table users':<38}")
    print("  " + "─" * 50)
    if users_corrupted:
        print(f"  {RED}✗ {users_corrupted} ligne(s) corrompues dans MySQL (telegram_id = 2147483647){RESET}")
    else:
        print(f"  {GREEN}✓ Pas de corruption dans users (MySQL){RESET}")
    if users_overflow_sqlite:
        print(f"  {CYAN}✓ {users_overflow_sqlite} user(s) avec telegram_id > INT_MAX dans le .db{RESET}")
    else:
        print(f"  {DIM}  Aucun user avec telegram_id > INT_MAX dans le .db{RESET}")

    print(f"\n{BOLD}{'='*70}{RESET}")
    if total_corrupted:
        print(f"  {RED}► ACTION REQUISE : {total_corrupted} ligne(s) à corriger dans categories{RESET}")
        print(f"  {DIM}  → Lance le script de correction une fois cet audit validé{RESET}")
    else:
        print(f"  {GREEN}► Aucune correction nécessaire pour l'overflow ✓{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    sq_conn.close()
    my_cur.close()
    my_conn.close()


if __name__ == "__main__":
    run()