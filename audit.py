#!/usr/bin/env python3
"""
audit_categories.py
-------------------
Affiche dans la console :
  1. Nombre de membres par catégorie (SQLite vs MySQL)
  2. Catégories < 100 membres : vérification id_user manquants
"""

import sqlite3
import mysql.connector
from mysql.connector import Error
from datetime import datetime
import sys

SQLITE_FILE = "preinscriptions.db"

MYSQL_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",
    "database": "fdkvip_db",
}

SEUIL = 100

# ── Couleurs ANSI ─────────────────────────────────────────────────────────────
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
        "FROM categories "
        "GROUP BY name_categorie "
        "ORDER BY COUNT(*) DESC"
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
    print(f"{BOLD}{'='*70}{RESET}\n")

    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} SQLite  : {SQLITE_FILE}")
    except Exception as e:
        print(f"  {RED}[ERREUR]{RESET} SQLite : {e}"); sys.exit(1)

    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} MySQL   : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"  {RED}[ERREUR]{RESET} MySQL : {e}"); sys.exit(1)

    sq_counts = get_category_counts(sq_cur, False)
    my_counts = get_category_counts(my_cur, True)
    all_cats  = sorted(set(sq_counts) | set(my_counts),
                       key=lambda c: sq_counts.get(c, 0), reverse=True)

    # ── Tableau global ────────────────────────────────────────────────────────
    W = 38
    print(f"  {BOLD}{'Catégorie':<{W}} {'SQLite':>8} {'MySQL':>8} {'Écart':>7}  Statut{RESET}")
    print("  " + "─" * 68)

    results = []
    for cat in all_cats:
        sq_n  = sq_counts.get(cat, 0)
        my_n  = my_counts.get(cat, 0)
        ecart = sq_n - my_n

        only_sq, only_my = set(), set()
        if sq_n < SEUIL:
            sq_members = get_members(sq_cur, cat, False)
            my_members = get_members(my_cur, cat, True)
            only_sq = sq_members - my_members
            only_my = my_members - sq_members

        if cat not in my_counts:
            sev, icon = "critical", f"{RED}🔴{RESET}"
        elif ecart > 0:
            sev, icon = "critical", f"{RED}🔴{RESET}"
        elif ecart < 0:
            sev, icon = "warning",  f"{YELLOW}⚠️ {RESET}"
        elif only_sq or only_my:
            sev, icon = "warning",  f"{YELLOW}⚠️ {RESET}"
        else:
            sev, icon = "ok",       f"{GREEN}✅{RESET}"

        ecart_str = f"{RED}{ecart:+d}{RESET}" if ecart > 0 else \
                    f"{YELLOW}{ecart:+d}{RESET}" if ecart < 0 else \
                    f"{GREEN}  0{RESET}"

        small_tag = f" {CYAN}[< {SEUIL}]{RESET}" if sq_n < SEUIL else ""

        print(f"  {icon} {cat:<{W-2}} {sq_n:>8,} {my_n:>8,} {ecart_str:>7}{small_tag}")

        if only_sq:
            ids = ", ".join(str(x) for x in sorted(only_sq)[:15])
            more = f" (+{len(only_sq)-15} autres)" if len(only_sq) > 15 else ""
            print(f"  {' ':{W+6}} {RED}↳ Absents MySQL  ({len(only_sq)}) : {ids}{more}{RESET}")
        if only_my:
            ids = ", ".join(str(x) for x in sorted(only_my)[:15])
            more = f" (+{len(only_my)-15} autres)" if len(only_my) > 15 else ""
            print(f"  {' ':{W+6}} {YELLOW}↳ Absents SQLite ({len(only_my)}) : {ids}{more}{RESET}")

        results.append((cat, sq_n, my_n, ecart, sev, only_sq, only_my))

    # ── Résumé ────────────────────────────────────────────────────────────────
    n_critical = sum(1 for r in results if r[4] == "critical")
    n_warning  = sum(1 for r in results if r[4] == "warning")
    n_ok       = sum(1 for r in results if r[4] == "ok")
    n_small    = sum(1 for r in results if r[1] < SEUIL)
    total_sq   = sum(r[1] for r in results)
    total_my   = sum(r[2] for r in results)

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"  {BOLD}RÉSUMÉ{RESET}  "
          f"{RED}{n_critical} critique(s){RESET}  |  "
          f"{YELLOW}{n_warning} avertissement(s){RESET}  |  "
          f"{GREEN}{n_ok} OK{RESET}")
    print(f"  Total SQLite : {total_sq:,}  |  "
          f"Total MySQL : {total_my:,}  |  "
          f"Écart : {RED if total_sq!=total_my else GREEN}{total_sq-total_my:+,}{RESET}")
    print(f"  Catégories auditées finement (< {SEUIL}) : {CYAN}{n_small}{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    sq_conn.close()
    my_cur.close()
    my_conn.close()


if __name__ == "__main__":
    run()