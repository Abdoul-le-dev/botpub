import sqlite3
from datetime import datetime, timedelta

DB = "preinscriptions.db"

def run():
    c = sqlite3.connect(DB)
    now = datetime.now()

    data = [
        {
            "email": "jean.dupont@gmail.com", "name": "Jean Dupont",
            "phone": "+33612345678", "country_code": "FR",
            "plan": "FDK Gold", "duration_days": 30,
            "amount_usd": 97.0, "currency": "USD", "amount_local": 97.0,
            "paid_at": (now - timedelta(days=2)).isoformat(),
            "started_at": (now - timedelta(days=2)).isoformat(),
            "expires_at": (now + timedelta(days=28)).isoformat(),
            "status": "pending", "note": None,
            "order_id": "ORD-001", "aggregator": "stripe",
        },
        {
            "email": "marie.martin@hotmail.com", "name": "Marie Martin",
            "phone": "+32479000001", "country_code": "BE",
            "plan": "FDK Gold", "duration_days": 30,
            "amount_usd": 97.0, "currency": "EUR", "amount_local": 89.0,
            "paid_at": (now - timedelta(days=5)).isoformat(),
            "started_at": (now - timedelta(days=5)).isoformat(),
            "expires_at": (now + timedelta(days=25)).isoformat(),
            "status": "pending", "note": None,
            "order_id": "ORD-002", "aggregator": "paypal",
        },
        {
            "email": "koffi.asante@yahoo.com", "name": "Koffi Asante",
            "phone": "+22507000001", "country_code": "CI",
            "plan": "FDK Gold", "duration_days": 30,
            "amount_usd": 97.0, "currency": "XOF", "amount_local": 60000.0,
            "paid_at": (now - timedelta(days=10)).isoformat(),
            "started_at": (now - timedelta(days=10)).isoformat(),
            "expires_at": (now + timedelta(days=20)).isoformat(),
            "status": "active", "note": "valide par telegram",
            "order_id": "ORD-003", "aggregator": "cinetpay",
        },
        {
            "email": "amara.diallo@gmail.com", "name": "Amara Diallo",
            "phone": "+221770000001", "country_code": "SN",
            "plan": "FDK Gold", "duration_days": 30,
            "amount_usd": 97.0, "currency": "XOF", "amount_local": 60000.0,
            "paid_at": (now - timedelta(days=35)).isoformat(),
            "started_at": (now - timedelta(days=35)).isoformat(),
            "expires_at": (now - timedelta(days=5)).isoformat(),
            "status": "active", "note": "valide par telegram",
            "order_id": "ORD-004", "aggregator": "cinetpay",
        },
        {
            "email": "amara.diallo@gmail.com", "name": "Amara Diallo",
            "phone": "+221770000001", "country_code": "SN",
            "plan": "FDK Gold", "duration_days": 30,
            "amount_usd": 97.0, "currency": "XOF", "amount_local": 60000.0,
            "paid_at": (now - timedelta(days=1)).isoformat(),
            "started_at": (now - timedelta(days=1)).isoformat(),
            "expires_at": (now + timedelta(days=29)).isoformat(),
            "status": "pending", "note": None,
            "order_id": "ORD-005", "aggregator": "cinetpay",
        },
    ]

    for d in data:
        c.execute("""
            INSERT INTO subscription_info
                (email, name, phone, country_code, plan, duration_days,
                 amount_usd, currency, amount_local, paid_at,
                 started_at, expires_at, status, note, order_id, aggregator,
                 created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'),datetime('now'))
        """, (
            d["email"], d["name"], d["phone"], d["country_code"],
            d["plan"], d["duration_days"], d["amount_usd"], d["currency"],
            d["amount_local"], d["paid_at"], d["started_at"], d["expires_at"],
            d["status"], d["note"], d["order_id"], d["aggregator"],
        ))

    c.execute("""
        INSERT OR IGNORE INTO users (telegram_id, name, phone, country, email, created_at)
        VALUES (999888777, 'Marie Martin', '+32479000001', 'BE', '', datetime('now'))
    """)

    c.commit()
    c.close()
    print("OK\n")
    print("jean.dupont@gmail.com    → user inconnu, paiement non validé")
    print("marie.martin@hotmail.com → user connu (telegram 999888777)")
    print("koffi.asante@yahoo.com   → tous paiements déjà validés")
    print("amara.diallo@gmail.com   → 2 paiements, prend ORD-005")

if __name__ == "__main__":
    run()