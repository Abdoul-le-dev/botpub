"""
get_user_prompts.py — Fonction autonome.

Récupère les prompts correspondant aux catégories d'un utilisateur.

Logique :
  1. Récupère toutes les catégories du user depuis la table `categories`
  2. Charge tous les prompts actifs depuis `ia_prompts`
  3. Pour chaque prompt, lit la description et cherche [categorie: name_categorie]
  4. Si la catégorie du prompt correspond à une catégorie du user → inclus

Format attendu dans ia_prompts.description :
  [categorie: VIP]
  [categorie: Forex-Pro]
  [categorie: TEMOIGNAGE]   ← pas de catégorie user, inclus pour tous
"""

import sqlite3
import re

DB_PATH = "preinscriptions.db"

# Regex qui extrait la valeur dans [categorie: xxx]
_CAT_PATTERN = re.compile(r"\[categorie\s*:\s*([^\]]+)\]", re.IGNORECASE)


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_user_prompts(telegram_id: int) -> list[dict]:
    """
    Retourne la liste des prompts pertinents pour un utilisateur.
 
    Un prompt est inclus si :
      - Sa description contient [categorie: X] et X est une catégorie du user
      - Sa description ne contient PAS de marqueur [categorie:] du tout
        (prompt global, applicable à tous)
 
    Retourne :
    [
      {
        "id":            int,
        "name":          str,
        "description":   str,
        "content":       str,
        "return_format": str,   # text | json | list | markdown
      },
      ...
    ]
    Retourne [] si l'utilisateur n'existe pas ou n'a aucun prompt applicable.
    """
    conn = get_conn()
    try:
        # ── Étape 1 : catégories du user ──────────────────────────────────────
        rows = conn.execute("""
            SELECT name_categorie
            FROM categories
            WHERE id_user = ?
        """, (telegram_id,)).fetchall()
 
        user_categories = {row["name_categorie"].strip().lower() for row in rows}
 
        # ── Étape 2 : tous les prompts actifs ────────────────────────────────
        prompts = conn.execute("""
            SELECT id, name, description, content, return_format
            FROM ia_prompts
            WHERE is_active = 1
            ORDER BY id ASC
        """).fetchall()
 
    finally:
        conn.close()
 
    # ── Étape 3 : filtrage selon [categorie: xxx] ─────────────────────────────
    result = []
 
    for p in prompts:
        description = p["description"] or ""
        match       = _CAT_PATTERN.search(description)
 
        if match:
            # Le prompt cible une catégorie précise
            prompt_cat = match.group(1).strip().lower()
            if prompt_cat in user_categories:
                result.append({
                    "name_categorie": p["name"],
                    "prompt":         p["content"],
                })
        else:
            # Pas de marqueur [categorie:] → prompt global, pour tout le monde
            result.append({
                "name_categorie": p["name"],
                "prompt":         p["content"],
            })
 
    return result