import sqlite3
import asyncio
import time


async def broadcast_message(bot, admin_id, text):
    conn = sqlite3.connect('preinscriptions.db')
    cursor = conn.cursor()
    cursor.execute("SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()

    user_ids = [row[0] for row in rows]
    total = len(user_ids)
    sent = 0

    # Estimation du temps
    est = round(total * 0.1 / 60, 2)

    if total == 0:
        await bot.send_message(admin_id, "❌ Aucun utilisateur à contacter.")
        return

    await bot.send_message(admin_id, f"📤 Envoi du message à {total} utilisateurs en cours...\n⏳ Estimé : {est} min")

    for idx, user_id in enumerate(user_ids, start=1):
        try:
            await bot.send_message(chat_id=user_id, text=text)
            sent += 1
        except:
            pass

        # Annonce à 1/3, 2/3, 3/3
        if idx == total // 3:
            await bot.send_message(admin_id, "✅ 1/3 du message envoyé")
        elif idx == (2 * total) // 3:
            await bot.send_message(admin_id, "✅ 2/3 du message envoyé")
        elif idx == total:
            await bot.send_message(admin_id, f"✅ Message terminé — envoyé à {sent} utilisateurs")

        await asyncio.sleep(0.1)  # Respect limite API

