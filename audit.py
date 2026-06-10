#!/usr/bin/env python3
"""
fix_overflow.py
---------------
Corrige tous les id_user / user_id = 2147483647 (INT_MAX overflow).

Tables :
  1. categories        → PK join sur .db
  2. categories_backup → PK join sur .db
  3. exam_user         → PK join + vérification email
  4. categories (9 lignes production) → correction manuelle
  5. messages          → croisement users par proximité temporelle

Mode dry-run par défaut — passer --apply pour exécuter.
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

INT_MAX    = 2147483647
DRY_RUN    = "--apply" not in sys.argv
BATCH_SIZE = 500

RED    = "\033[91m"
YELLOW = "\033[93m"
GREEN  = "\033[92m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

# 9 lignes production corrompues — correction manuelle
# id=20281 → Samuel (7003132059, 178 min d'écart)
# id=20266..20280 → inconnus → 0
PRODUCTION_FIXES = {
    20281: 7003132059,
    20266: 0,
    20267: 0,
    20269: 0,
    20274: 0,
    20277: 0,
    20278: 0,
    20279: 0,
    20280: 0,
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def section(title):
    print(f"\n{BOLD}{'─'*70}{RESET}")
    print(f"{BOLD}  {title}{RESET}")
    print(f"{BOLD}{'─'*70}{RESET}")

def apply_updates(my_cur, my_conn, table, id_col, updates, label="id"):
    """
    updates : [(pk, vrai_id), ...]
    Exécute par lots, affiche progression.
    """
    done = errors = 0
    for i in range(0, len(updates), BATCH_SIZE):
        batch = updates[i:i + BATCH_SIZE]

        if DRY_RUN:
            if i == 0:
                print(f"  {DIM}  [DRY-RUN] Aperçu :{RESET}")
                for pk, real_id in batch[:3]:
                    print(f"  {DIM}    UPDATE `{table}` SET `{id_col}`={real_id} WHERE {label}={pk}{RESET}")
            done += len(batch)
            continue

        try:
            my_cur.executemany(
                f"UPDATE `{table}` SET `{id_col}` = %s WHERE `{label}` = %s",
                [(real_id, pk) for pk, real_id in batch]
            )
            my_conn.commit()
            done += len(batch)
            pct = done / len(updates) * 100
            print(f"  {CYAN}  {done:>6,}/{len(updates):,} ({pct:.0f}%){RESET}", end="\r")
        except Error as e:
            my_conn.rollback()
            errors += len(batch)
            print(f"\n  {RED}✗ Erreur batch : {e}{RESET}")

    print()
    if DRY_RUN:
        print(f"  {YELLOW}[DRY-RUN] {done:,} UPDATE(s) prêts{RESET}")
    else:
        print(f"  {GREEN}✓ {done:,} corrigé(s){RESET}")
        if errors:
            print(f"  {RED}✗ {errors} erreur(s){RESET}")
    return done, errors


# ── Étape 1 & 2 : categories + categories_backup via PK ──────────────────────

def fix_by_pk(sq_cur, my_cur, my_conn, table, id_col, max_db_id):
    section(f"ÉTAPE — {table}  (PK join, id <= {max_db_id})")

    # Lignes corrompues dans le périmètre .db uniquement
    my_cur.execute(
        f"SELECT id FROM `{table}` "
        f"WHERE `{id_col}` = %s AND id <= %s ORDER BY id",
        (INT_MAX, max_db_id)
    )
    corrupted_ids = [r[0] for r in my_cur.fetchall()]
    total = len(corrupted_ids)

    if not total:
        print(f"\n  {GREEN}✓ Aucune ligne corrompue dans le périmètre .db{RESET}")
        return 0, 0

    print(f"\n  {RED}► {total:,} ligne(s) corrompues{RESET}")

    # Vrais IDs depuis SQLite
    placeholders = ",".join("?" * len(corrupted_ids))
    sq_cur.execute(
        f"SELECT id, `{id_col}` FROM `{table}` "
        f"WHERE id IN ({placeholders}) AND `{id_col}` > ?",
        corrupted_ids + [INT_MAX]
    )
    sq_map  = {row[0]: row[1] for row in sq_cur.fetchall()}
    missing = [i for i in corrupted_ids if i not in sq_map]

    print(f"  {GREEN}✓ {len(sq_map):,} vrai(s) ID trouvé(s) dans le .db{RESET}")
    if missing:
        print(f"  {YELLOW}⚠ {len(missing)} introuvable(s) dans .db (ignorés) : {missing[:10]}{RESET}")

    if not sq_map:
        print(f"  {RED}✗ Rien à corriger{RESET}")
        return 0, total

    return apply_updates(my_cur, my_conn, table, id_col, list(sq_map.items()))


# ── Étape 3 : exam_user via PK + vérification email ──────────────────────────

def fix_exam_user(sq_cur, my_cur, my_conn):
    section("ÉTAPE — exam_user  (PK join + vérification email)")

    my_cur.execute(
        "SELECT id, email, user_name, last_name "
        "FROM exam_user WHERE id_user = %s ORDER BY id",
        (INT_MAX,)
    )
    my_rows = {r[0]: {"email": r[1], "user_name": r[2], "last_name": r[3]}
               for r in my_cur.fetchall()}
    total = len(my_rows)

    if not total:
        print(f"\n  {GREEN}✓ Aucune ligne corrompue{RESET}")
        return 0, 0

    print(f"\n  {RED}► {total} ligne(s) corrompues{RESET}\n")

    placeholders = ",".join("?" * len(my_rows))
    sq_cur.execute(
        f"SELECT id, id_user, email, user_name, last_name "
        f"FROM exam_user WHERE id IN ({placeholders}) AND id_user > ?",
        list(my_rows.keys()) + [INT_MAX]
    )
    sq_rows = {r[0]: {"id_user": r[1], "email": r[2],
                      "user_name": r[3], "last_name": r[4]}
               for r in sq_cur.fetchall()}

    print(f"  {'id':>6}  {'Email':<35} {'Nom':<20} {'Statut'}")
    print("  " + "─" * 75)

    confirmed  = []
    mismatches = []
    not_found  = []

    for pk, my_data in sorted(my_rows.items()):
        if pk not in sq_rows:
            not_found.append(pk)
            print(f"  {pk:>6}  {my_data['email']:<35} {my_data['user_name']:<20} "
                  f"{RED}✗ absent du .db{RESET}")
            continue

        sq_data   = sq_rows[pk]
        email_ok  = my_data["email"].strip().lower() == sq_data["email"].strip().lower()
        name_ok   = my_data["user_name"].strip().lower() == sq_data["user_name"].strip().lower()

        if email_ok:
            confirmed.append((pk, sq_data["id_user"]))
            suffix = "" if name_ok else f" {YELLOW}(nom diff){RESET}"
            print(f"  {pk:>6}  {my_data['email']:<35} {my_data['user_name']:<20} "
                  f"{GREEN}✓ → {sq_data['id_user']}{RESET}{suffix}")
        else:
            mismatches.append(pk)
            print(f"  {pk:>6}  {my_data['email']:<35} {my_data['user_name']:<20} "
                  f"{RED}✗ MISMATCH email : '{sq_data['email']}'{RESET}")

    print()
    print(f"  {GREEN}► {len(confirmed)} confirmée(s){RESET}", end="")
    if mismatches:
        print(f"  {RED}  {len(mismatches)} mismatch(es) ignoré(s){RESET}", end="")
    if not_found:
        print(f"  {YELLOW}  {len(not_found)} absent(s) ignoré(s){RESET}", end="")
    print()

    if not confirmed:
        print(f"  {RED}✗ Rien à corriger{RESET}")
        return 0, total

    return apply_updates(my_cur, my_conn, "exam_user", "id_user", confirmed)


# ── Étape 4 : 9 lignes production categories ─────────────────────────────────

def fix_production_categories(my_cur, my_conn):
    section("ÉTAPE — categories (9 lignes production, correction manuelle)")

    print(f"\n  {'id':>6}  {'name_categorie':<30} {'vrai id_user':<15}  Action")
    print("  " + "─" * 65)

    updates = []
    for pk, real_id in sorted(PRODUCTION_FIXES.items()):
        my_cur.execute(
            "SELECT name_categorie FROM categories WHERE id = %s", (pk,)
        )
        row = my_cur.fetchone()
        cat = row[0] if row else "???"

        action = f"→ {real_id}" if real_id != 0 else f"{YELLOW}→ 0 (inconnu){RESET}"
        color  = GREEN if real_id != 0 else YELLOW
        print(f"  {pk:>6}  {cat:<30} {color}{str(real_id):<15}{RESET}  {action}")
        updates.append((pk, real_id))

    print()
    return apply_updates(my_cur, my_conn, "categories", "id_user", updates)


# ── Étape 5 : messages via users (proximité temporelle) ──────────────────────

def fix_messages(my_cur, my_conn):
    section("ÉTAPE — messages  (croisement users par proximité temporelle)")

    my_cur.execute(
        "SELECT COUNT(*) FROM messages WHERE user_id = %s", (INT_MAX,)
    )
    total = my_cur.fetchone()[0]
    print(f"\n  {RED}► {total:,} message(s) corrompus{RESET}")

    # Récupère tous les messages corrompus
    my_cur.execute(
        "SELECT id, created_at FROM messages "
        "WHERE user_id = %s ORDER BY created_at",
        (INT_MAX,)
    )
    corrupted = my_cur.fetchall()   # [(id, created_at), ...]

    # Tous les users connus avec telegram_id > INT_MAX (vrais IDs)
    my_cur.execute(
        "SELECT telegram_id, created_at FROM users "
        "WHERE telegram_id > %s ORDER BY created_at",
        (INT_MAX,)
    )
    known_users = my_cur.fetchall()   # [(telegram_id, created_at), ...]

    confirmed  = []
    ambiguous  = 0
    not_found  = 0

    for msg_id, msg_at in corrupted:
        # Cherche les users dans une fenêtre de ±5 minutes
        candidates = [
            uid for uid, u_at in known_users
            if u_at and abs((msg_at - u_at).total_seconds()) < 300
        ]

        if len(candidates) == 1:
            confirmed.append((msg_id, candidates[0]))
        elif len(candidates) > 1:
            ambiguous += 1
        else:
            not_found += 1

    print(f"  {GREEN}✓ {len(confirmed):,} message(s) avec 1 seul candidat → corrigeable{RESET}")
    print(f"  {YELLOW}⚠ {ambiguous:,} ambigu(s) (plusieurs candidats) → ignorés{RESET}")
    print(f"  {RED}✗ {not_found:,} sans candidat → ignorés{RESET}")

    if not confirmed:
        print(f"\n  {RED}✗ Aucun message corrigeable{RESET}")
        return 0, 0

    # Aperçu des 5 premières corrections
    print(f"\n  {DIM}  Aperçu (5 premières) :{RESET}")
    for msg_id, real_uid in confirmed[:5]:
        print(f"  {DIM}    UPDATE messages SET user_id={real_uid} WHERE id={msg_id}{RESET}")

    return apply_updates(my_cur, my_conn, "messages", "user_id", confirmed)


# ── Vérification finale ───────────────────────────────────────────────────────

def verify_all(my_cur):
    section("VÉRIFICATION FINALE")
    print()
    tables = [
        ("categories",        "id_user"),
        ("categories_backup", "id_user"),
        ("exam_user",         "id_user"),
        ("messages",          "user_id"),
    ]
    total_remaining = 0
    for table, col in tables:
        my_cur.execute(
            f"SELECT COUNT(*) FROM `{table}` WHERE `{col}` = %s", (INT_MAX,)
        )
        n = my_cur.fetchone()[0]
        total_remaining += n
        if n == 0:
            print(f"  {GREEN}✓ {table:<30} propre{RESET}")
        else:
            print(f"  {RED}✗ {table:<30} {n:,} ligne(s) encore corrompues{RESET}")
    return total_remaining


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run():
    ts       = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    mode_str = (f"{YELLOW}DRY-RUN (simulation — aucune écriture){RESET}"
                if DRY_RUN else f"{RED}APPLY (écriture réelle){RESET}")

    print(f"\n{BOLD}{'='*70}{RESET}")
    print(f"{BOLD}  FIX OVERFLOW INT_MAX — {ts}{RESET}")
    print(f"  Mode : {mode_str}")
    print(f"{BOLD}{'='*70}{RESET}")

    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"\n  {GREEN}[OK]{RESET} SQLite : {SQLITE_FILE}")
    except Exception as e:
        print(f"  {RED}[ERREUR]{RESET} SQLite : {e}"); sys.exit(1)

    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"  {GREEN}[OK]{RESET} MySQL  : {MYSQL_CONFIG['database']}")
    except Error as e:
        print(f"  {RED}[ERREUR]{RESET} MySQL : {e}"); sys.exit(1)

    MAX_DB_ID = 20258   # dernier id commun SQLite / MySQL

    total_done = total_errors = 0

    # 1. categories (périmètre .db)
    d, e = fix_by_pk(sq_cur, my_cur, my_conn, "categories", "id_user", MAX_DB_ID)
    total_done += d; total_errors += e

    # 2. categories_backup
    d, e = fix_by_pk(sq_cur, my_cur, my_conn, "categories_backup", "id_user", MAX_DB_ID)
    total_done += d; total_errors += e

    # 3. exam_user
    d, e = fix_exam_user(sq_cur, my_cur, my_conn)
    total_done += d; total_errors += e

    # 4. 9 lignes production
    d, e = fix_production_categories(my_cur, my_conn)
    total_done += d; total_errors += e

    # 5. messages
    d, e = fix_messages(my_cur, my_conn)
    total_done += d; total_errors += e

    # Vérification finale
    if not DRY_RUN:
        remaining = verify_all(my_cur)

    # Résumé
    print(f"\n{BOLD}{'='*70}{RESET}")
    if DRY_RUN:
        print(f"  {YELLOW}[DRY-RUN] {total_done:,} UPDATE(s) seraient exécutés{RESET}")
        print(f"  {DIM}  → python fix_overflow.py --apply{RESET}")
    else:
        color = GREEN if total_errors == 0 else RED
        print(f"  {color}► {total_done:,} corrigées  |  {total_errors} erreur(s){RESET}")
        if remaining == 0:
            print(f"  {GREEN}► Base propre — aucun 2147483647 restant ✓{RESET}")
        else:
            print(f"  {RED}► {remaining:,} ligne(s) encore corrompues — vérifie les erreurs{RESET}")
    print(f"{BOLD}{'='*70}{RESET}\n")

    sq_conn.close()
    my_cur.close()
    my_conn.close()


if __name__ == "__main__":
    run()