"""
test_full_flow_manual.py — Tests manuels end-to-end (v8), avec un vrai user_id.

Chaque sous-commande teste UN cas précis, isolément, contre la vraie
base et le vrai bot (aucun mock) — pratique pour vérifier le
comportement réel sans attendre un vrai mouvement de prix ni une vraie
campagne week-end.

Prérequis : variable d'environnement `tokens` définie (comme main.py).

═══════════════════════════════════════════════════════════════════════
CAS COUVERTS
═══════════════════════════════════════════════════════════════════════

Consentement hebdomadaire :
  consent-status <uid>
      Affiche si <uid> a validé pour la semaine en cours.

  consent-request <uid>
      Envoie la demande de validation (comme /je_valide_mon_engagement
      quand pas encore validé) — vérifie visuellement le message +
      le bouton reçus par <uid>.

  consent-validate <uid> [--pending SESSION_ID]
      Simule le clic sur "✅ Je valide". Sans --pending : simule une
      validation via /je_valide_mon_engagement (cherche un trade en
      cours à envoyer, sinon confirme "prochaine opportunité"). Avec
      --pending : simule une validation déclenchée par un signal
      manqué (ce signal précis est envoyé après validation).

  consent-clear <uid>
      Réinitialise le consentement de <uid> pour la semaine en cours
      (pour repartir d'un état "pas validé" entre deux tests).

Money management (calcul à la demande) :
  mm-calc <uid> <session_id> <capital>
      Envoie le calcul de lot/gain pour ce signal, SANS sauvegarder
      (comme taper un capital dans Money management).

  mm-save <uid> <session_id> <capital>
      Sauvegarde le capital (opt-in permanent) + envoie la notif
      immédiate de gestion du trade (comme cliquer "💾 Sauvegarder").

Capital sauvegardé (permanent, opt-in) :
  capital-status <uid>
      Affiche le capital sauvegardé pour <uid>, s'il existe.

  capital-clear <uid>
      Supprime le capital sauvegardé de <uid> (désabonne des notifs
      de gestion du trade).

Simulation TP/SL (sans attendre un vrai mouvement de prix) :
  simulate-tp <uid> <session_id> <tp_level 1|2|3>
      Simule ce niveau TP atteint sur cette session : déclenche
      notify_opted_in_members() directement. Si tp_level=3, ferme
      aussi la session (comme en conditions réelles). Vérifie que
      SEULS les membres dont le palier inclut ce niveau reçoivent un
      message (teste le comportement à paliers).

  simulate-sl <uid> <session_id>
      Simule le SL touché sur cette session : ferme la session,
      notifie l'admin — et NE DOIT ENVOYER AUCUN message à <uid>,
      même si <uid> a sauvegardé son capital. Le script confirme
      explicitement l'absence de message.

Session :
  create-test-session
      Crée une session Gold factice (pour avoir un session_id à
      utiliser dans les commandes ci-dessus).

  session-status <session_id>
      Affiche la phase actuelle d'une session.

═══════════════════════════════════════════════════════════════════════
EXEMPLES D'ENCHAÎNEMENT
═══════════════════════════════════════════════════════════════════════

# 1. Cas "pas encore validé" → validation → trade en cours envoyé
python test_full_flow_manual.py create-test-session
python test_full_flow_manual.py consent-clear 123456789
python test_full_flow_manual.py consent-status 123456789        # → invalide
python test_full_flow_manual.py consent-request 123456789       # → reçoit le contrat
python test_full_flow_manual.py consent-validate 123456789      # → reçoit le signal en cours
python test_full_flow_manual.py consent-status 123456789        # → valide

# 2. Money management + sauvegarde + paliers TP
python test_full_flow_manual.py mm-calc 123456789 125 300       # petit compte
python test_full_flow_manual.py mm-save 123456789 125 300       # sauvegarde (palier TP1 seul)
python test_full_flow_manual.py simulate-tp 123456789 125 1     # → doit recevoir
python test_full_flow_manual.py simulate-tp 123456789 125 2     # → NE DOIT PAS recevoir (palier TP1 seul)

# 3. SL silencieux même opt-in
python test_full_flow_manual.py simulate-sl 123456789 125       # → aucun message à 123456789
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv
from telegram import Bot

from db import init_pool, get_db
from telegram_page.gold.gold_engine import create_gold_session, set_bot as set_gold_bot
from telegram_page.gold.disclaimer_gate import (
    disclaimer_gate,
    ensure_schema as ensure_disclaimer_schema,
    send_consent_request,
    _deliver_after_consent,
)
from telegram_page.gold.interactive_tools import (
    calc_lot as mm_calc_lot,
    _build_result_message as mm_build_result_message,
)
from member_capital import (
    ensure_schema as ensure_capital_schema,
    get_capital,
    save_capital,
    delete_capital,
)
from telegram_page.gold.trade_watcher import (
    set_bot as set_watcher_bot,
    _get_session as watcher_get_session,
    _close_session,
    _notify_admin_closed,
    _notify_opted_in_tp,
)
from telegram_page.gold.trade_management_notifs import notify_opted_in_members

load_dotenv()


async def _get_session_dict(session_id: int) -> dict | None:
    async with get_db() as cur:
        await cur.execute("SELECT * FROM gold_trade_sessions WHERE id = %s", (session_id,))
        row = await cur.fetchone()
    return dict(row) if row else None


async def _setup(bot: Bot):
    await init_pool()
    await ensure_disclaimer_schema()
    await ensure_capital_schema()
    set_gold_bot(bot)
    set_watcher_bot(bot)


# ══════════════════════════════════════════════════════════════════════════════
# Cas — consentement hebdomadaire
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_consent_status(bot, uid: int):
    valid = await disclaimer_gate.is_valid(uid)
    print(f"[consent-status] uid={uid} → {'VALIDE cette semaine' if valid else 'PAS VALIDÉ'}")


async def cmd_consent_request(bot, uid: int):
    intro = (
        "⚠️ *Tu n'as pas encore validé ton engagement hebdomadaire.*\n\n"
        "Tant que ce n'est pas fait, tu ne peux pas recevoir les signaux."
    )
    await send_consent_request(bot, uid, intro=intro)
    print(f"[consent-request] demande envoyée à uid={uid} — vérifie sur Telegram")


async def cmd_consent_validate(bot, uid: int, pending_session_id: int | None):
    await disclaimer_gate.record_consent(uid)
    print(f"[consent-validate] consentement enregistré pour uid={uid} "
          f"(pending_session_id={pending_session_id})")
    await _deliver_after_consent(bot, uid, pending_session_id)
    print("[consent-validate] livraison post-consentement effectuée — vérifie sur Telegram "
          "(signal en cours, OU message 'prochaine opportunité' si aucun trade actif)")


async def cmd_consent_clear(bot, uid: int):
    async with get_db() as cur:
        await cur.execute(
            "DELETE FROM weekly_disclaimer_consents WHERE user_id = %s", (uid,)
        )
    disclaimer_gate.invalidate_cache()
    print(f"[consent-clear] consentement de uid={uid} réinitialisé pour la semaine en cours")


# ══════════════════════════════════════════════════════════════════════════════
# Cas — Money management
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_mm_calc(bot, uid: int, session_id: int, capital: float):
    session = await _get_session_dict(session_id)
    if session is None:
        print(f"[mm-calc] session #{session_id} introuvable")
        return
    lot = mm_calc_lot(capital, float(session["entry_price"]), float(session["sl"]))
    text = mm_build_result_message(session, capital, lot)
    await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
    print(f"[mm-calc] envoyé à uid={uid} — capital={capital} lot={lot}")


async def cmd_mm_save(bot, uid: int, session_id: int, capital: float):
    session = await _get_session_dict(session_id)
    await save_capital(uid, capital)
    print(f"[mm-save] capital {capital}$ sauvegardé pour uid={uid}")
    header = "🔔 *Notifications de gestion du trade activées.*\n\n"
    if session is None:
        await bot.send_message(chat_id=uid, text=header +
            "Tu recevras un message à chaque niveau important (TP1, TP2, TP3) "
            "sur tes prochains trades.", parse_mode="Markdown")
    else:
        lot = mm_calc_lot(capital, float(session["entry_price"]), float(session["sl"]))
        text = header + mm_build_result_message(session, capital, lot)
        await bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
    print("[mm-save] notif immédiate envoyée — vérifie sur Telegram")


# ══════════════════════════════════════════════════════════════════════════════
# Cas — capital sauvegardé
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_capital_status(bot, uid: int):
    capital = await get_capital(uid)
    if capital is None:
        print(f"[capital-status] uid={uid} → aucun capital sauvegardé (pas opt-in)")
    else:
        print(f"[capital-status] uid={uid} → {capital}$ sauvegardé")


async def cmd_capital_clear(bot, uid: int):
    await delete_capital(uid)
    print(f"[capital-clear] capital de uid={uid} supprimé — ne recevra plus les notifs de trade")


# ══════════════════════════════════════════════════════════════════════════════
# Cas — simulation TP / SL
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_simulate_tp(bot, uid: int, session_id: int, tp_level: int):
    session = await watcher_get_session(session_id)
    if session is None:
        print(f"[simulate-tp] session #{session_id} introuvable")
        return

    capital_before = await get_capital(uid)
    print(f"[simulate-tp] uid={uid} capital sauvegardé = {capital_before}")

    if tp_level == 3:
        await _close_session(session_id, "tp3")
        await _notify_admin_closed(session_id, "tp3")
        print(f"[simulate-tp] session #{session_id} fermée (tp3_reached)")

    report = await notify_opted_in_members(bot, session, tp_level)
    print(f"[simulate-tp] TP{tp_level} — rapport : {report}")
    if capital_before is not None:
        print(f"[simulate-tp] → vérifie sur Telegram si uid={uid} a reçu un message "
              f"(dépend de son palier d'objectif pour {capital_before}$)")
    else:
        print(f"[simulate-tp] uid={uid} n'a pas sauvegardé de capital → ne doit RIEN recevoir")


async def cmd_simulate_sl(bot, uid: int, session_id: int):
    session = await watcher_get_session(session_id)
    if session is None:
        print(f"[simulate-sl] session #{session_id} introuvable")
        return

    capital_before = await get_capital(uid)
    await _close_session(session_id, "sl")
    await _notify_admin_closed(session_id, "sl")
    print(f"[simulate-sl] session #{session_id} fermée (sl_touched)")
    print(f"[simulate-sl] uid={uid} capital sauvegardé = {capital_before} — "
          f"AUCUN message ne doit lui être envoyé (SL toujours silencieux, "
          f"même opt-in) → vérifie sur Telegram qu'il n'a RIEN reçu")


# ══════════════════════════════════════════════════════════════════════════════
# Cas — session
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_create_test_session(bot):
    session = await create_gold_session({
        "direction": "buy",
        "entry_price": 2358.50,
        "sl": 2354.00,
        "tp1": 2362.00,
        "tp2": 2366.00,
        "tp3": 2370.00,
        "timeframe": "M15",
        "confidence_level": 3,
        "note": "Session de test — full flow manuel",
    })
    print(f"[create-test-session] session créée : #{session['id']}")


async def cmd_session_status(bot, session_id: int):
    session = await _get_session_dict(session_id)
    if session is None:
        print(f"[session-status] session #{session_id} introuvable")
        return
    print(f"[session-status] #{session_id} → phase={session['current_phase']} "
          f"direction={session['direction']} entry={session['entry_price']} "
          f"sl={session['sl']} tp1={session.get('tp1')} tp2={session.get('tp2')} "
          f"tp3={session.get('tp3')}")


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Tests manuels end-to-end (v8)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("consent-status").add_argument("uid", type=int)
    sub.add_parser("consent-request").add_argument("uid", type=int)

    cv = sub.add_parser("consent-validate")
    cv.add_argument("uid", type=int)
    cv.add_argument("--pending", type=int, default=None, dest="pending_session_id")

    sub.add_parser("consent-clear").add_argument("uid", type=int)

    mmc = sub.add_parser("mm-calc")
    mmc.add_argument("uid", type=int)
    mmc.add_argument("session_id", type=int)
    mmc.add_argument("capital", type=float)

    mms = sub.add_parser("mm-save")
    mms.add_argument("uid", type=int)
    mms.add_argument("session_id", type=int)
    mms.add_argument("capital", type=float)

    sub.add_parser("capital-status").add_argument("uid", type=int)
    sub.add_parser("capital-clear").add_argument("uid", type=int)

    stp = sub.add_parser("simulate-tp")
    stp.add_argument("uid", type=int)
    stp.add_argument("session_id", type=int)
    stp.add_argument("tp_level", type=int, choices=[1, 2, 3])

    ssl = sub.add_parser("simulate-sl")
    ssl.add_argument("uid", type=int)
    ssl.add_argument("session_id", type=int)

    sub.add_parser("create-test-session")

    sst = sub.add_parser("session-status")
    sst.add_argument("session_id", type=int)

    return p


async def main():
    token = os.getenv("tokens")
    if not token:
        print("Variable d'environnement 'tokens' manquante.")
        sys.exit(1)

    args = _build_parser().parse_args()
    bot = Bot(token=token)
    await _setup(bot)

    if args.cmd == "consent-status":
        await cmd_consent_status(bot, args.uid)
    elif args.cmd == "consent-request":
        await cmd_consent_request(bot, args.uid)
    elif args.cmd == "consent-validate":
        await cmd_consent_validate(bot, args.uid, args.pending_session_id)
    elif args.cmd == "consent-clear":
        await cmd_consent_clear(bot, args.uid)
    elif args.cmd == "mm-calc":
        await cmd_mm_calc(bot, args.uid, args.session_id, args.capital)
    elif args.cmd == "mm-save":
        await cmd_mm_save(bot, args.uid, args.session_id, args.capital)
    elif args.cmd == "capital-status":
        await cmd_capital_status(bot, args.uid)
    elif args.cmd == "capital-clear":
        await cmd_capital_clear(bot, args.uid)
    elif args.cmd == "simulate-tp":
        await cmd_simulate_tp(bot, args.uid, args.session_id, args.tp_level)
    elif args.cmd == "simulate-sl":
        await cmd_simulate_sl(bot, args.uid, args.session_id)
    elif args.cmd == "create-test-session":
        await cmd_create_test_session(bot)
    elif args.cmd == "session-status":
        await cmd_session_status(bot, args.session_id)

    try:
        from db import close_pool
        await close_pool()
    except ImportError:
        pass
    except Exception as e:
        print(f"[test] avertissement : échec fermeture propre du pool ({e})")


if __name__ == "__main__":
    asyncio.run(main())