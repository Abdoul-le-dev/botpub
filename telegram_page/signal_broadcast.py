"""
signal_broadcast.py — Envoi de signaux de trading avec boutons inline Telegram.

Problème résolu : broadcast_engine envoie du texte brut (send_message).
Pour les signaux, il faut send_message + reply_markup (InlineKeyboardMarkup)
afin que les boutons "Je suis dedans / Je ne prends pas" apparaissent.

Flux :
  1. publish_signal() appelle broadcast_signal()
  2. broadcast_signal() résout les destinataires via la catégorie
  3. Pour chaque membre → send_one_signal() avec InlineKeyboardMarkup
  4. Les callbacks "sgt_in_{signal_id}" / "sgt_out_{signal_id}" sont
     interceptés par register_signal_handlers() dans le bot Telegram
  5. Le handler appelle POST /trading/signals/{id}/participate via l'API
"""

import asyncio
import sqlite3
import logging

from datetime import datetime
from pathlib import Path
from telegram import InlineKeyboardMarkup, InlineKeyboardButton

DB_PATH  = "preinscriptions.db"
ADMIN_ID = 571718066

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DB
# ══════════════════════════════════════════════════════════════════════════════

def _get_conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def _get_category_user_ids(category: str) -> list[int]:
    """Retourne tous les telegram_id de la catégorie."""
    with _get_conn() as conn:
        if category == "all":
            rows = conn.execute(
                "SELECT telegram_id FROM users WHERE telegram_id IS NOT NULL"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id_user FROM categories WHERE name_categorie = ?",
                (category,)
            ).fetchall()
        return [r[0] for r in rows]


def _get_member_capital(user_id: int) -> float | None:
    """Retourne le dernier capital déclaré d'un membre, ou None."""
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT capital FROM member_capital WHERE user_id = ? ORDER BY declared_at DESC LIMIT 1",
            (user_id,)
        ).fetchone()
    return float(row["capital"]) if row else None


def _get_prenom(user_id: int) -> str:
    with _get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM users WHERE telegram_id = ?", (user_id,)
        ).fetchone()
    if row and row["name"]:
        p = row["name"].strip()
        if 1 <= len(p) <= 20:
            return p
    return "l'ami"


# ══════════════════════════════════════════════════════════════════════════════
# CONSTRUCTION DU MESSAGE SIGNAL
# ══════════════════════════════════════════════════════════════════════════════

def build_signal_message(signal: dict, lot: float | None = None, prenom: str = "") -> str:
    """
    Construit le texte Markdown du signal.
    lot      : lot suggéré personnalisé pour ce membre (None → pas affiché)
    prenom   : prénom du membre pour la personnalisation
    """
    d   = "📈 LONG"  if signal["direction"] == "long"  else "📉 SHORT"
    sep = "\n"

    lines = [
        f"📊 *Signal de Trading*{' — ' + prenom if prenom else ''}",
        "",
        f"🔷 Paire : *{signal['pair']}*",
        f"{d}",
        f"🎯 Entrée : *{signal['entry_price']}*",
    ]

    if signal.get("tp1"):
        lines.append(f"✅ TP1 : *{signal['tp1']}*")
    if signal.get("tp2"):
        lines.append(f"   TP2 : *{signal['tp2']}*")
    if signal.get("sl"):
        lines.append(f"❌ SL : *{signal['sl']}*")

    if signal.get("tp1") and signal.get("sl") and signal["entry_price"]:
        try:
            tp_dist = abs(signal["tp1"] - signal["entry_price"])
            sl_dist = abs(signal["entry_price"] - signal["sl"])
            if sl_dist > 0:
                rr = round(tp_dist / sl_dist, 1)
                lines.append(f"📐 R:R : 1:{rr}")
        except Exception:
            pass

    if signal.get("timeframe"):
        lines.append(f"⏱ TF : {signal['timeframe']}")

    if signal.get("note"):
        lines.append(f"\n_{signal['note']}_")

    # Lot suggéré personnalisé
    if lot is not None:
        lines.append(f"\n💼 *Lot suggéré pour toi : {lot:.4f}*")
        lines.append("_(basé sur ton capital déclaré · risque 2%)_")

    return sep.join(lines)


def build_participation_keyboard(signal_id: int) -> InlineKeyboardMarkup:
    """
    Boutons inline de participation.
    Callback data : "sgt_in_{signal_id}" / "sgt_out_{signal_id}"
    """
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Je suis dans ce trade",
                callback_data=f"sgt_in_{signal_id}"
            ),
            InlineKeyboardButton(
                "❌ J'ai pas pu",
                callback_data=f"sgt_out_{signal_id}"
            ),
        ]
    ])


# ══════════════════════════════════════════════════════════════════════════════
# CALCUL LOT PAR MEMBRE
# ══════════════════════════════════════════════════════════════════════════════

def calculate_member_lot(capital: float, sl_pips: float, pip_value: float, risk_pct: float = 2.0) -> float:
    """Formule : lot = (capital × risk%) / (sl_pips × pip_value)"""
    risk_usd = capital * risk_pct / 100
    if sl_pips <= 0 or pip_value <= 0:
        return 0.0
    return round(risk_usd / (sl_pips * pip_value), 4)


def get_signal_sl_pips(signal: dict) -> float | None:
    """Calcule les pips de SL depuis le signal."""
    if not signal.get("sl") or not signal.get("entry_price"):
        return None
    try:
        # Nombre de décimales selon la paire (défaut 5 pour forex)
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT decimals, pip_value FROM trading_pairs WHERE symbol = ?",
                (signal["pair"].upper(),)
            ).fetchone()
        decimals  = int(row["decimals"])  if row else 5
        pip_value = float(row["pip_value"]) if row else 10.0

        multiplier = 10 ** (decimals - 1)
        diff       = abs(signal["entry_price"] - signal["sl"])
        sl_pips    = round(diff * multiplier, 1)
        return sl_pips, pip_value
    except Exception:
        return None, 10.0


# ══════════════════════════════════════════════════════════════════════════════
# ENVOI UNITAIRE
# ══════════════════════════════════════════════════════════════════════════════

async def _send_one_signal(
    bot,
    user_id:    int,
    signal:     dict,
    sl_pips:    float | None,
    pip_value:  float,
    media_url:  str | None,
    personalize_lot: bool = True,
) -> bool:
    """
    Envoie le signal à un seul membre avec les boutons inline.
    - Si media_url → send_photo / send_video + caption
    - Sinon → send_message

    Retourne True si succès.
    """
    try:
        prenom   = _get_prenom(user_id)
        lot      = None

        # Calcul lot personnalisé si SL connu
        if personalize_lot and sl_pips:
            capital = _get_member_capital(user_id) or 1000.0
            lot     = calculate_member_lot(capital, sl_pips, pip_value)

        text     = build_signal_message(signal, lot=lot, prenom=prenom)
        keyboard = build_participation_keyboard(signal["id"])

        if media_url:
            local_path = Path(media_url.lstrip("/"))
            is_local   = local_path.exists()

            if is_local:
                media_bytes = open(local_path, "rb")
            else:
                media_bytes = media_url  # file_id Telegram ou URL publique

            ext = local_path.suffix.lower() if is_local else ""
            is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm")

            try:
                if is_video:
                    if len(text) > 1024:
                        await bot.send_message(
                            chat_id=user_id, text=text,
                            parse_mode="Markdown", reply_markup=keyboard
                        )
                        await bot.send_video(chat_id=user_id, video=media_bytes)
                    else:
                        await bot.send_video(
                            chat_id=user_id, video=media_bytes,
                            caption=text, parse_mode="Markdown",
                            reply_markup=keyboard
                        )
                else:
                    # Image
                    if len(text) > 1024:
                        await bot.send_message(
                            chat_id=user_id, text=text,
                            parse_mode="Markdown", reply_markup=keyboard
                        )
                        await bot.send_photo(chat_id=user_id, photo=media_bytes)
                    else:
                        await bot.send_photo(
                            chat_id=user_id, photo=media_bytes,
                            caption=text, parse_mode="Markdown",
                            reply_markup=keyboard
                        )
            finally:
                if is_local and hasattr(media_bytes, "close"):
                    media_bytes.close()
        else:
            await bot.send_message(
                chat_id=user_id,
                text=text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )

        return True

    except Exception as e:
        logger.warning(f"[signal_broadcast] Échec uid={user_id} signal={signal['id']}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MOTEUR PRINCIPAL
# ══════════════════════════════════════════════════════════════════════════════

async def broadcast_signal(
    bot,
    signal:   dict,
    category: str        = "clients_actifs",
    media_url: str | None = None,
    delay:    float       = 0.08,
    retry:    bool        = True,
    risk_pct: float       = 2.0,
) -> dict:
    """
    Broadcast d'un signal de trading avec boutons inline.

    Paramètres :
      signal    : dict complet du signal (doit contenir 'id', 'pair', etc.)
      category  : catégorie Telegram à cibler (ou 'all')
      media_url : chemin local /media/... ou file_id Telegram
      delay     : délai entre chaque envoi (throttle)
      retry     : retry automatique en cas d'échec
      risk_pct  : pourcentage de risque pour le calcul de lot

    Retourne : {
        total, sent, errors, started_at, finished_at,
        avg_lot, skipped_no_capital
    }
    """
    # ── Résolution des destinataires ─────────────────────────────────────────
    user_ids = _get_category_user_ids(category)
    total    = len(user_ids)

    if total == 0:
        logger.warning(f"[signal_broadcast] Aucun destinataire pour category={category}")
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=f"⚠️ Signal #{signal['id']} — Aucun destinataire trouvé (catégorie: {category})"
            )
        except Exception:
            pass
        return {"total": 0, "sent": 0, "errors": 0}

    # ── Calcul SL pips pour lot personnalisé ─────────────────────────────────
    sl_pips_result = get_signal_sl_pips(signal)
    if isinstance(sl_pips_result, tuple):
        sl_pips, pip_value = sl_pips_result
    else:
        sl_pips, pip_value = None, 10.0

    # ── Notification admin démarrage ─────────────────────────────────────────
    started_at  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    est_min     = round(total * delay / 60, 1)
    pair_str    = signal.get("pair", "?")
    dir_str     = "LONG" if signal.get("direction") == "long" else "SHORT"

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"📤 Signal {pair_str} {dir_str} en cours d'envoi\n"
                f"Destinataires : {total}\n"
                f"Durée estimée : ~{est_min} min\n"
                f"Lot personnalisé : {'✅ Oui' if sl_pips else '❌ SL manquant'}"
            )
        )
    except Exception:
        pass

    # ── Boucle d'envoi ───────────────────────────────────────────────────────
    sent    = 0
    errors  = 0
    lots    = []

    for idx, user_id in enumerate(user_ids, start=1):

        success = await _send_one_signal(
            bot, user_id, signal,
            sl_pips=sl_pips, pip_value=pip_value,
            media_url=media_url,
            personalize_lot=(sl_pips is not None),
        )

        # Retry si échec
        if not success and retry:
            await asyncio.sleep(1)
            success = await _send_one_signal(
                bot, user_id, signal,
                sl_pips=sl_pips, pip_value=pip_value,
                media_url=media_url,
                personalize_lot=(sl_pips is not None),
            )

        if success:
            sent += 1
            # Calcul lot pour stats
            if sl_pips:
                cap = _get_member_capital(user_id) or 1000.0
                lots.append(calculate_member_lot(cap, sl_pips, pip_value))
        else:
            errors += 1

        # Progression admin
        if idx == total // 3:
            try:
                await bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"📊 Signal {pair_str} — 1/3 envoyé ({sent}/{total})"
                )
            except Exception:
                pass
        elif idx == (2 * total) // 3:
            try:
                await bot.send_message(
                    chat_id=ADMIN_ID,
                    text=f"📊 Signal {pair_str} — 2/3 envoyé ({sent}/{total})"
                )
            except Exception:
                pass

        logger.debug(f"[signal_broadcast] {idx}/{total} uid={user_id} {'✓' if success else '✗'}")
        await asyncio.sleep(delay)

    # ── Rapport final ─────────────────────────────────────────────────────────
    finished_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    avg_lot     = round(sum(lots) / len(lots), 4) if lots else None

    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"✅ Signal {pair_str} {dir_str} terminé\n"
                f"Envoyés : {sent}/{total} | Erreurs : {errors}\n"
                f"Lot moyen : {avg_lot or '—'}\n"
                f"{started_at} → {finished_at}"
            )
        )
    except Exception:
        pass

    return {
        "total":       total,
        "sent":        sent,
        "errors":      errors,
        "started_at":  started_at,
        "finished_at": finished_at,
        "avg_lot":     avg_lot,
    }


import httpx as _httpx

API_BASE = "http://localhost:8000/trading"   # ← adapter si besoin


async def handle_signal_participation(update, context):
    print("=== CALLBACK RECU ===", update.callback_query.data)
    query   = update.callback_query
    user_id = query.from_user.id
    data    = query.data  # "sgt_in_42" ou "sgt_out_42"

    try:
        parts     = data.split("_")
        response  = parts[1]   # "in" | "out"
        signal_id = int(parts[2])
    except (IndexError, ValueError):
        await query.answer("❌ Erreur — réessaie.")
        return

    # Enregistrement en base
    try:
        async with _httpx.AsyncClient(timeout=8) as client:
            r = await client.post(
                f"{API_BASE}/signals/{signal_id}/participate",
                json={"user_id": user_id, "response": response}
            )
            r.raise_for_status()
    except Exception:
        await query.answer("⚠️ Erreur de communication, réessaie.", show_alert=True)
        return

    # Toast de confirmation
    toast = {
        "in":  "✅ Trade pris — bonne chance !",
        "out": "👌 Noté, trade non pris.",
    }
    await query.answer(toast.get(response, "OK"), show_alert=False)

    # Remplacement du clavier par une ligne de statut (boutons désactivés)
    label = "✅ Trade pris" if response == "in" else "❌ Trade non pris"
    new_kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="sgt_done")]
    ])

    try:
        await query.edit_message_reply_markup(reply_markup=new_kbd)
    except Exception:
        pass

def register_signal_handlers(app):
    from telegram.ext import CallbackQueryHandler

    

    app.add_handler(
        CallbackQueryHandler(
            handle_signal_participation,
            pattern=r"^sgt_(in|out)_\d+$"
        ),
        group=2
    )

    # Bloque le clic sur le bouton statut "Trade pris / non pris"
    async def _done(update, context):
        await update.callback_query.answer("Tu as déjà répondu à ce signal.", show_alert=False)

    app.add_handler(
        CallbackQueryHandler(_done, pattern=r"^sgt_done$"),
        group=2
    )

    print("[signal_broadcast] Handlers participation enregistrés ✓")