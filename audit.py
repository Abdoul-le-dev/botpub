#!/usr/bin/env python3
"""
audit_deep.py
-------------
Audit complet SQLite → MySQL :
  1. COUNT(*) par table
  2. Colonnes manquantes / en trop dans MySQL
  3. Types de colonnes divergents
  4. Échantillon de 3 lignes comparé (SQLite vs MySQL) pour les tables non vides
  5. Rapport HTML + console

Usage :
    pip install mysql-connector-python
    python audit_deep.py
    # puis ouvrir audit_report_YYYYMMDD_HHMMSS.html
"""

import sqlite3
import mysql.connector
from mysql.connector import Error
import sys
import json
import html as html_lib
from datetime import datetime

# ─────────────────────────────────────────────
SQLITE_FILE = "preinscriptions.db"

MYSQL_CONFIG = {
    "host":     "localhost",
    "port":     3306,
    "user":     "fiacrefdksignal",
    "password": "Fiacre2026@#",
    "database": "fdkvip_db",
}
# ─────────────────────────────────────────────

TABLE_ORDER = [
    "users", "mail_valide", "categories_meta", "category_rules",
    "subscription_plans", "growth_subscriptions", "promo_codes",
    "auto_promo_config", "subscriptions", "messages", "conversations",
    "categories", "categories_backup", "usersdefault", "videos",
    "categorie_exercice", "exercice", "resultat_student_question",
    "resultat_student_day", "args", "participants", "participants_2nd",
    "exam", "exam_user", "broadcast_history", "trade_comments",
    "signals", "trade_journal", "forms", "form_sessions",
    "form_submissions", "form_responses", "signal_participations",
    "followup_comments", "trading_pairs", "member_capital", "ai_bilans",
    "invite_links", "invite_link_stats", "ia_trigger_config",
    "automation_jobs", "automation_logs", "ia_prompts", "ia_functions",
    "subscription_info", "gold_seasons", "gold_tp_rules",
    "gold_trade_sessions", "gold_user_sessions", "gold_member_entries",
    "gold_flow_events", "simulation_accounts", "simulation_trades",
]

# ── Normalisation des types ──────────────────────────────────────────────────

SQLITE_TO_MYSQL_TYPE = {
    "integer":   ["int", "bigint", "tinyint", "smallint", "mediumint", "integer"],
    "real":      ["float", "double", "decimal", "numeric", "real"],
    "text":      ["varchar", "char", "text", "mediumtext", "longtext", "tinytext", "enum", "set"],
    "blob":      ["blob", "mediumblob", "longblob", "tinyblob", "binary", "varbinary"],
    "numeric":   ["decimal", "numeric", "int", "bigint", "float", "double"],
}

def normalize_sqlite_type(t: str) -> str:
    t = t.lower().strip()
    if not t:
        return "text"
    for base, _ in SQLITE_TO_MYSQL_TYPE.items():
        if base in t:
            return base
    if "int" in t:
        return "integer"
    if "char" in t or "clob" in t or "text" in t:
        return "text"
    if "real" in t or "floa" in t or "doub" in t:
        return "real"
    if "blob" in t or not t:
        return "blob"
    return "numeric"


def normalize_mysql_type(t: str) -> str:
    t = t.lower().strip().split("(")[0]
    for sqlite_base, mysql_variants in SQLITE_TO_MYSQL_TYPE.items():
        if t in mysql_variants:
            return sqlite_base
    if "int" in t:
        return "integer"
    if "char" in t or "text" in t or "enum" in t or "set" in t:
        return "text"
    if "float" in t or "double" in t or "decimal" in t or "real" in t:
        return "real"
    if "blob" in t or "binary" in t:
        return "blob"
    return "numeric"


# ── Helpers connexion ────────────────────────────────────────────────────────

def safe_count(cur, table: str, is_mysql: bool) -> int:
    try:
        cur.execute(f"SELECT COUNT(*) FROM `{table}`")
        row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception as e:
        return -1


def get_sqlite_schema(cur, table: str) -> dict:
    """Retourne {col_name: sqlite_type_raw}"""
    cur.execute(f"PRAGMA table_info(`{table}`)")
    return {row[1]: row[2] for row in cur.fetchall()}


def get_mysql_schema(cur, table: str) -> dict:
    """Retourne {col_name: mysql_type_raw}"""
    try:
        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_TYPE "
            "FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s "
            "ORDER BY ORDINAL_POSITION",
            (MYSQL_CONFIG["database"], table)
        )
        return {row[0]: row[1] for row in cur.fetchall()}
    except Error:
        return {}


def get_sqlite_sample(cur, table: str, columns: list, n: int = 3) -> list:
    try:
        cols = ", ".join(f"`{c}`" for c in columns)
        cur.execute(f"SELECT {cols} FROM `{table}` LIMIT {n}")
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Exception:
        return []


def get_mysql_sample(cur, table: str, columns: list, n: int = 3) -> list:
    try:
        cols = ", ".join(f"`{c}`" for c in columns)
        cur.execute(f"SELECT {cols} FROM `{table}` LIMIT {n}")
        return [dict(zip(columns, row)) for row in cur.fetchall()]
    except Error:
        return []


# ── Audit d'une table ────────────────────────────────────────────────────────

def audit_table(sq_cur, my_cur, table: str, mysql_tables: set) -> dict:
    result = {
        "table":          table,
        "sq_count":       0,
        "my_count":       0,
        "count_ok":       False,
        "in_mysql":       table in mysql_tables,
        "only_in_sqlite": [],   # colonnes présentes SQLite, absentes MySQL
        "only_in_mysql":  [],   # colonnes présentes MySQL, absentes SQLite
        "type_mismatches": [],  # [{col, sqlite_type, mysql_type}]
        "sq_sample":      [],
        "my_sample":      [],
        "common_cols":    [],
        "errors":         [],
    }

    # COUNT SQLite
    result["sq_count"] = safe_count(sq_cur, table, False)

    if not result["in_mysql"]:
        result["errors"].append("Table absente de MySQL")
        return result

    # COUNT MySQL
    result["my_count"] = safe_count(my_cur, table, True)
    result["count_ok"] = (result["sq_count"] == result["my_count"])

    # Schémas
    sq_schema = get_sqlite_schema(sq_cur, table)
    my_schema = get_mysql_schema(my_cur, table)

    sq_cols = set(sq_schema.keys())
    my_cols = set(my_schema.keys())

    result["only_in_sqlite"] = sorted(sq_cols - my_cols)
    result["only_in_mysql"]  = sorted(my_cols - sq_cols)
    result["common_cols"]    = sorted(sq_cols & my_cols)

    # Types divergents (sur colonnes communes)
    for col in result["common_cols"]:
        sq_norm = normalize_sqlite_type(sq_schema[col])
        my_norm = normalize_mysql_type(my_schema[col])
        if sq_norm != my_norm:
            result["type_mismatches"].append({
                "col":         col,
                "sqlite_type": sq_schema[col],
                "mysql_type":  my_schema[col],
                "sq_norm":     sq_norm,
                "my_norm":     my_norm,
            })

    # Échantillons (seulement si les deux ont des données)
    if result["sq_count"] > 0 and result["common_cols"]:
        result["sq_sample"] = get_sqlite_sample(sq_cur, table, result["common_cols"])
    if result["my_count"] > 0 and result["common_cols"]:
        result["my_sample"] = get_mysql_sample(my_cur, table, result["common_cols"])

    return result


# ── Rapport HTML ─────────────────────────────────────────────────────────────

def severity(r: dict) -> str:
    if not r["in_mysql"]:
        return "critical"
    if r["sq_count"] != r["my_count"] and r["sq_count"] > 0:
        return "critical"
    if r["only_in_sqlite"] or r["type_mismatches"]:
        return "warning"
    if not r["count_ok"] and r["sq_count"] == 0:
        return "ok"
    return "ok"


def build_html(results: list, ts: str) -> str:
    critical = [r for r in results if severity(r) == "critical"]
    warnings = [r for r in results if severity(r) == "warning"]
    oks      = [r for r in results if severity(r) == "ok"]

    total_sq = sum(r["sq_count"] for r in results if r["sq_count"] > 0)
    total_my = sum(r["my_count"] for r in results if isinstance(r["my_count"], int) and r["my_count"] >= 0)

    def e(v):
        return html_lib.escape(str(v))

    rows_html = ""
    for r in results:
        sev  = severity(r)
        bg   = {"critical": "#fff0f0", "warning": "#fffbe6", "ok": "#f0fff4"}.get(sev, "white")
        icon = {"critical": "🔴", "warning": "⚠️", "ok": "✅"}.get(sev, "")
        ecart = r["sq_count"] - r["my_count"] if isinstance(r["my_count"], int) else "?"

        col_issues = ""
        if r["only_in_sqlite"]:
            col_issues += f"<div class='badge red'>SQLite only: {e(', '.join(r['only_in_sqlite']))}</div>"
        if r["only_in_mysql"]:
            col_issues += f"<div class='badge blue'>MySQL only: {e(', '.join(r['only_in_mysql']))}</div>"
        if r["type_mismatches"]:
            mismatch_list = ", ".join(
                f"{m['col']} ({e(m['sqlite_type'])}→{e(m['mysql_type'])})"
                for m in r["type_mismatches"]
            )
            col_issues += f"<div class='badge orange'>Types: {mismatch_list}</div>"

        # Échantillon
        sample_html = ""
        if r["sq_sample"] or r["my_sample"]:
            cols = r["common_cols"][:6]  # max 6 colonnes dans l'aperçu
            th = "".join(f"<th>{e(c)}</th>" for c in cols)

            sq_rows = ""
            for row in r["sq_sample"]:
                sq_rows += "<tr>" + "".join(f"<td>{e(row.get(c, ''))}</td>" for c in cols) + "</tr>"

            my_rows = ""
            for row in r["my_sample"]:
                my_rows += "<tr>" + "".join(f"<td>{e(row.get(c, ''))}</td>" for c in cols) + "</tr>"

            sample_html = f"""
            <details>
              <summary>Aperçu données (3 premières lignes, {len(cols)} colonnes)</summary>
              <div style="display:flex;gap:16px;margin-top:8px;flex-wrap:wrap">
                <div>
                  <strong>SQLite</strong>
                  <table class="sample"><thead><tr>{th}</tr></thead><tbody>{sq_rows or '<tr><td colspan="{len(cols)}">vide</td></tr>'}</tbody></table>
                </div>
                <div>
                  <strong>MySQL</strong>
                  <table class="sample"><thead><tr>{th}</tr></thead><tbody>{my_rows or '<tr><td colspan="{len(cols)}">vide</td></tr>'}</tbody></table>
                </div>
              </div>
            </details>"""

        rows_html += f"""
        <tr style="background:{bg}">
          <td>{icon} {e(r['table'])}</td>
          <td style="text-align:right">{r['sq_count']:,}</td>
          <td style="text-align:right">{"—" if not r["in_mysql"] else f"{r['my_count']:,}"}</td>
          <td style="text-align:right;{'color:red;font-weight:bold' if isinstance(ecart,int) and ecart>0 else ''}">{ecart if isinstance(ecart,int) else "?"}</td>
          <td>{col_issues or '<span style="color:#aaa">—</span>'}</td>
        </tr>
        {"<tr style='background:" + bg + "'><td colspan='5' style='padding:4px 20px'>" + sample_html + "</td></tr>" if sample_html else ""}
        """

    return f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<title>Audit Migration — {ts}</title>
<style>
  body {{ font-family: system-ui, sans-serif; margin: 0; padding: 24px; background: #f5f5f5; color: #222; }}
  h1   {{ font-size: 1.4rem; margin-bottom: 4px; }}
  .meta {{ color: #666; font-size: .85rem; margin-bottom: 24px; }}
  .summary-grid {{ display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }}
  .card {{ background: white; border-radius: 8px; padding: 16px 20px; min-width: 140px;
           box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  .card .num {{ font-size: 2rem; font-weight: 700; line-height: 1; }}
  .card .lbl {{ font-size: .78rem; color: #666; margin-top: 4px; }}
  .card.red  .num {{ color: #c0392b; }}
  .card.orange .num {{ color: #e67e22; }}
  .card.green  .num {{ color: #27ae60; }}
  table.main {{ width: 100%; border-collapse: collapse; background: white;
                border-radius: 8px; overflow: hidden;
                box-shadow: 0 1px 4px rgba(0,0,0,.08); }}
  table.main th {{ background: #2d3748; color: white; padding: 10px 14px;
                   text-align: left; font-size: .82rem; }}
  table.main td {{ padding: 8px 14px; border-bottom: 1px solid #eee;
                   font-size: .82rem; vertical-align: top; }}
  table.main tr:last-child td {{ border-bottom: none; }}
  table.sample {{ border-collapse: collapse; font-size: .75rem; margin-top: 4px; }}
  table.sample th, table.sample td {{ border: 1px solid #ddd; padding: 3px 6px;
                                      max-width: 180px; overflow: hidden;
                                      text-overflow: ellipsis; white-space: nowrap; }}
  table.sample thead {{ background: #eee; }}
  .badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px;
            font-size: .75rem; margin: 2px; }}
  .badge.red    {{ background: #ffe0e0; color: #c0392b; }}
  .badge.blue   {{ background: #e0eaff; color: #2c5282; }}
  .badge.orange {{ background: #fff0d0; color: #975a00; }}
  details summary {{ cursor: pointer; color: #2c5282; font-size: .8rem; }}
</style>
</head>
<body>
<h1>🔍 Audit Migration SQLite → MySQL</h1>
<div class="meta">Généré le {ts} &nbsp;|&nbsp; Source : {SQLITE_FILE} &nbsp;→&nbsp; {MYSQL_CONFIG["database"]}</div>

<div class="summary-grid">
  <div class="card red">
    <div class="num">{len(critical)}</div>
    <div class="lbl">Tables critiques<br>(fuites / absentes)</div>
  </div>
  <div class="card orange">
    <div class="num">{len(warnings)}</div>
    <div class="lbl">Avertissements<br>(colonnes / types)</div>
  </div>
  <div class="card green">
    <div class="num">{len(oks)}</div>
    <div class="lbl">Tables OK</div>
  </div>
  <div class="card">
    <div class="num">{total_sq:,}</div>
    <div class="lbl">Lignes SQLite</div>
  </div>
  <div class="card {'red' if total_sq != total_my else 'green'}">
    <div class="num">{total_my:,}</div>
    <div class="lbl">Lignes MySQL</div>
  </div>
  <div class="card {'red' if total_sq - total_my != 0 else 'green'}">
    <div class="num">{total_sq - total_my:,}</div>
    <div class="lbl">Écart total</div>
  </div>
</div>

<table class="main">
  <thead>
    <tr>
      <th>Table</th>
      <th>SQLite</th>
      <th>MySQL</th>
      <th>Écart</th>
      <th>Colonnes / Types</th>
    </tr>
  </thead>
  <tbody>
    {rows_html}
  </tbody>
</table>
</body>
</html>"""


# ── Point d'entrée ────────────────────────────────────────────────────────────

def run():
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = f"audit_report_{ts_file}.html"
    json_file   = f"audit_data_{ts_file}.json"

    print("=" * 70)
    print(f"  AUDIT PROFOND — {ts}")
    print("=" * 70)

    # Connexions
    try:
        sq_conn = sqlite3.connect(SQLITE_FILE)
        sq_cur  = sq_conn.cursor()
        print(f"[OK] SQLite  : {SQLITE_FILE}")
    except Exception as e:
        print(f"[ERREUR] SQLite : {e}"); sys.exit(1)

    try:
        my_conn = mysql.connector.connect(**MYSQL_CONFIG)
        my_cur  = my_conn.cursor()
        print(f"[OK] MySQL   : {MYSQL_CONFIG['database']}\n")
    except Error as e:
        print(f"[ERREUR] MySQL : {e}"); sys.exit(1)

    # Tables disponibles
    sq_cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    )
    sq_tables = [r[0] for r in sq_cur.fetchall()]
    my_cur.execute("SHOW TABLES")
    my_tables = {r[0] for r in my_cur.fetchall()}

    ordered    = [t for t in TABLE_ORDER if t in sq_tables]
    extras     = [t for t in sq_tables  if t not in TABLE_ORDER]
    all_tables = ordered + extras

    results = []
    W = 42

    print(f"  {'Table':<{W}} {'SQLite':>8} {'MySQL':>8} {'Écart':>7}  Colonnes  Types")
    print("  " + "─" * 72)

    for table in all_tables:
        r = audit_table(sq_cur, my_cur, table, my_tables)
        results.append(r)

        sev   = severity(r)
        icon  = {"critical": "🔴", "warning": "⚠️ ", "ok": "✅"}.get(sev, "  ")
        ecart = r["sq_count"] - r["my_count"] if isinstance(r["my_count"], int) else "?"
        col_flag  = f"⚠ +{len(r['only_in_sqlite'])} col SQLite" if r["only_in_sqlite"] else "✓"
        type_flag = f"⚠ {len(r['type_mismatches'])} type(s)" if r["type_mismatches"] else "✓"

        print(
            f"  {icon} {table:<{W-2}} "
            f"{r['sq_count']:>8} "
            f"{str(r['my_count']) if r['in_mysql'] else '—':>8} "
            f"{str(ecart):>7}  "
            f"{col_flag:<20} {type_flag}"
        )

        if r["only_in_sqlite"]:
            print(f"  {'':>{W+2}}   ↳ absent MySQL : {r['only_in_sqlite']}")
        if r["type_mismatches"]:
            for m in r["type_mismatches"]:
                print(f"  {'':>{W+2}}   ↳ type {m['col']}: SQLite={m['sqlite_type']} / MySQL={m['mysql_type']}")

    sq_conn.close()
    my_cur.close()
    my_conn.close()

    # Export JSON brut
    with open(json_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)

    # Export HTML
    html_content = build_html(results, ts)
    with open(report_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Résumé console
    critical = [r for r in results if severity(r) == "critical"]
    warnings = [r for r in results if severity(r) == "warning"]

    total_sq = sum(r["sq_count"] for r in results if r["sq_count"] > 0)
    total_my = sum(r["my_count"] for r in results if isinstance(r["my_count"], int) and r["my_count"] >= 0)

    print("\n" + "=" * 70)
    print(f"  RÉSUMÉ : {len(critical)} critique(s)  |  {len(warnings)} avertissement(s)")
    print(f"  Total SQLite : {total_sq:,}  |  Total MySQL : {total_my:,}  |  Écart : {total_sq - total_my:,}")
    print("=" * 70)
    print(f"\n  📄 Rapport HTML  : {report_file}   ← ouvre dans ton navigateur")
    print(f"  📄 Données brutes: {json_file}")

    if critical:
        print(f"\n  ➡  Lance migrate_data_safe.py pour combler les {len(critical)} table(s) critiques.")
    print()


if __name__ == "__main__":
    run()