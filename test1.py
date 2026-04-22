import sqlite3
conn = sqlite3.connect("preinscriptions.db")

# 1. Voir ce qu'il y a dans categories_meta
print("categories_meta:", conn.execute("SELECT * FROM categories_meta LIMIT 5").fetchall())

# 2. Voir les noms distincts dans categories
print("categories noms:", conn.execute("SELECT DISTINCT name_categorie FROM categories LIMIT 5").fetchall())

# 3. Tester la jointure directement
print("join:", conn.execute("""
    SELECT cm.name_categorie, COUNT(c.id) as cnt
    FROM categories_meta cm
    LEFT JOIN categories c ON c.name_categorie = cm.name_categorie
    GROUP BY cm.id
    LIMIT 5
""").fetchall())
conn.close()