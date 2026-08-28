"""
test_send_signal_manual.py — Envoi de test manuel du signal Gold.

À exécuter à la main pour vérifier le nouveau flux (send_signal +
rate limiter adaptatif + clavier par destinataire) SANS toucher aux
vrais abonnés. Cible EXCLUSIVEMENT la catégorie de test :

    "FDK MASTER CLASS Aout"

La catégorie de production (CATEGORY_TARGET = "clients_actifs" dans
signal_broadcast.py) n'est pas modifiée par ce script — la catégorie
est passée explicitement à send_signal().

Prérequis :
  - Une session Gold existe déjà en base (gold_trade_sessions),
    créée via gold_engine.create_gold_session(...).
  - La catégorie "FDK MASTER CLASS Aout" existe dans `categories` et
    contient les comptes de test.
  - Variable d'environnement `tokens` (même nom que main.py) définie.

Usage :
    python test_send_signal_manual.py <session_id>

    # ou, pour aussi créer une session de test jetable avant l'envoi :
    python test_send_signal_manual.py --create-test-session
"""

from __future__ import annotations

import asyncio
import gc
import os
import sys

from dotenv import load_dotenv
from telegram import Bot

from db import init_pool
from telegram_page.gold.gold_engine import create_gold_session
from telegram_page.gold.signal_broadcast import send_signal
from telegram_page.gold.disclaimer_gate import (
    ensure_schema as ensure_disclaimer_schema,
    disclaimer_gate,
)

load_dotenv()

TEST_CATEGORY = "FDK MASTER CLASS Aout"


async def _create_disposable_test_session() -> int:
    """Crée une session Gold factice pour un essai isolé."""
    session = await create_gold_session({
        "direction": "buy",
        "entry_price": 2358.50,
        "sl": 2354.00,
        "tp1": 2362.00,
        "tp2": 2366.00,
        "tp3": 2370.00,
        "timeframe": "M15",
        "confidence_level": 3,
        "note": "Session de test — envoi manuel",
    })
    print(f"[test] session de test créée : #{session['id']}")
    return session["id"]


async def main():
    token = os.getenv("tokens")
    if not token:
        print("Variable d'environnement 'tokens' manquante.")
        sys.exit(1)

    await init_pool()
    await ensure_disclaimer_schema()   # crée weekly_disclaimer_consents si absente
                                        # (indispensable ici : ce script tourne
                                        # standalone, sans passer par le post_init
                                        # du bot principal qui l'appelle aussi)

    if len(sys.argv) > 1 and sys.argv[1] == "--create-test-session":
        session_id = await _create_disposable_test_session()
    elif len(sys.argv) > 1 and sys.argv[1] == "--consent":
        if len(sys.argv) < 3:
            print("Usage : python test_send_signal_manual.py --consent <telegram_id>")
            sys.exit(1)
        uid = int(sys.argv[2])
        await disclaimer_gate.record_consent(uid)
        print(f"[test] consentement hebdo validé pour uid={uid} (semaine courante)")
        return
    elif len(sys.argv) > 1:
        session_id = int(sys.argv[1])
    else:
        print("Usage : python test_send_signal_manual.py <session_id>")
        print("        python test_send_signal_manual.py --create-test-session")
        print("        python test_send_signal_manual.py --consent <telegram_id>")
        sys.exit(1)

    bot = Bot(token=token)

    print(f"[test] envoi du signal #{session_id} → catégorie de test "
          f"'{TEST_CATEGORY}' (production non affectée)")

    report = await send_signal(bot, session_id, category=TEST_CATEGORY)

    print("[test] résultat :", report)

    # Ferme proprement le pool aiomysql pour éviter le
    # "RuntimeError: Event loop is closed" cosmétique au garbage collection
    # après la fin d'asyncio.run(). Best-effort : db.py n'expose peut-être
    # pas close_pool, on ignore silencieusement si absent.
    try:
        from db import close_pool
        await close_pool()
    except ImportError:
        pass
    except Exception as e:
        print(f"[test] avertissement : échec fermeture propre du pool ({e})")

    # Force le nettoyage des connexions aiomysql MAINTENANT, pendant que
    # la boucle asyncio est encore active — évite le "RuntimeError: Event
    # loop is closed" cosmétique sinon déclenché plus tard, pendant
    # l'arrêt de l'interpréteur (boucle déjà fermée à ce moment-là).
    gc.collect()
    await asyncio.sleep(0)


if __name__ == "__main__":
    asyncio.run(main())