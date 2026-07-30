# ══════════════════════════════════════════════════════════════════════════════
# engagement.py — Module d'engagement (deep links)
# ══════════════════════════════════════════════════════════════════════════════
#
# Module 100% indépendant du tunnel d'inscription existant.
# 4 scénarios accessibles via deep link :
#
#   /start fdk_concept_capital_verify      → vérif inscription
#   /start fdk_concept_capital_motivation  → collecte motivation
#   /start fdk_concept_capital_vote        → vote (1 / 2 / 3 gagnants)
#   /start fdk_concept_capital_share       → CTA partage
#
# Extensible : pour ajouter un scénario, ajoute une entrée dans SCENARIOS
# et écris la fonction handle_<nom>() correspondante.
#
# Intégration dans main.py — UNE SEULE LIGNE, APRÈS registration_conv :
#
#     from engagement import register_engagement_handlers
#     ...
#     app.add_handler(registration_conv)     # existant
#     register_engagement_handlers(app)      # ← ajouter ici
#
# ⚠️ L'ordre est important : ce module réutilise le callback
# "resume_registration" du tunnel existant. En enregistrant APRÈS
# le ConversationHandler, on garantit que ce callback reste bien
# capté par le tunnel (et pas par ce module).
# ══════════════════════════════════════════════════════════════════════════════

import logging
from datetime import datetime
from pathlib import Path

from telegram import (
    Update, InlineKeyboardMarkup, InlineKeyboardButton,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters, ApplicationHandlerStop,
)

# On réutilise ce qui existe déjà dans main.py — jamais de duplication.
# - ADMIN_IDS : liste des admins (autorisation /engagement)
# - sync_get_db : pool aiomysql du reste du bot
# - log_error : journalisation d'erreurs dans errors.log (envoyé à 20h)
from main import ADMIN_IDS
from db import get_db as sync_get_db

try:
    # log_error existe dans main.py (log fichier). En cas d'import circulaire
    # improbable, on tombe sur un fallback silencieux.
    from main import log_error
except Exception:  # pragma: no cover
    def log_error(title, detail=""):
        logging.getLogger("engagement").warning(f"{title} — {detail}")


logger = logging.getLogger("engagement")


# ══════════════════════════════════════════════════════════════════════════════
# CONFIG
# ══════════════════════════════════════════════════════════════════════════════

# Préfixe commun à tous les deep links du module
DEEPLINK_PREFIX = "fdk_concept_capital_"

# Fichiers d'historique à la racine du projet
MOTIVATIONS_FILE = Path("motivations.txt")
VOTES_FILE       = Path("votes.txt")

# Compteur mémoire — nombre de vérifications depuis le dernier démarrage.
# Volontairement non persisté : sert juste à voir si le lien tourne.
_verify_count_since_boot = 0

# Libellés des votes (stockés en base sous forme "1"/"2"/"3")
VOTE_LABELS = {
    "1": "🥇 1 gagnant",
    "2": "🥈 2 gagnants",
    "3": "🥉 3 gagnants",
}

# ── Flag "en attente d'une motivation" — stocké au niveau MODULE
# et non dans context.user_data. Raison : context.user_data peut être
# scopé différemment selon les handlers (ConversationHandler, etc.), et
# on veut être 100% sûr que le flag armé dans handle_deeplink() soit
# visible depuis capture_engagement_response() quel que soit le groupe
# / handler qui déclenche le texte suivant. Ce dict est indexé par
# telegram_id, indépendant du contexte.
_awaiting_motivation: set = set()


def _arm_motivation_wait(telegram_id: int):
    _awaiting_motivation.add(int(telegram_id))


def _disarm_motivation_wait(telegram_id: int):
    _awaiting_motivation.discard(int(telegram_id))


def _is_awaiting_motivation(telegram_id: int) -> bool:
    return int(telegram_id) in _awaiting_motivation


# ══════════════════════════════════════════════════════════════════════════════
# SCHÉMA — nouvelles colonnes (idempotent)
# ══════════════════════════════════════════════════════════════════════════════

async def ensure_engagement_schema():
    """Ajoute vote / motivation_at / vote_at à la table users.
    Idempotent : ignore l'erreur 1060 (Duplicate column)."""
    ddl_statements = [
        "ALTER TABLE users ADD COLUMN vote VARCHAR(10) NULL",
        "ALTER TABLE users ADD COLUMN motivation_at DATETIME NULL",
        "ALTER TABLE users ADD COLUMN vote_at DATETIME NULL",
    ]
    async with sync_get_db() as cur:
        for stmt in ddl_statements:
            try:
                await cur.execute(stmt)
            except Exception as e:
                if "1060" in str(e) or "duplicate column" in str(e).lower():
                    continue
                logger.exception(f"[engagement.schema] échec: {stmt}")
                log_error("Échec ALTER engagement schema", f"{stmt} — {e}")


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS DB
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_user(telegram_id: int):
    """Retourne le dict de l'utilisateur (name/phone/level/motivation/vote)
    ou None si aucune ligne."""
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT name, phone, level, motivation, vote "
            "FROM users WHERE telegram_id = %s",
            (telegram_id,),
        )
        return await cur.fetchone()


async def _save_motivation(telegram_id: int, motivation: str):
    """Enregistre / remplace la motivation. Crée la ligne si l'utilisateur
    n'existe pas encore (peu probable mais on est safe)."""
    async with sync_get_db() as cur:
        await cur.execute(
            "INSERT INTO users (telegram_id, created_at, motivation, motivation_at) "
            "VALUES (%s, NOW(), %s, NOW()) AS new_row "
            "ON DUPLICATE KEY UPDATE "
            "  motivation    = new_row.motivation, "
            "  motivation_at = NOW()",
            (telegram_id, motivation),
        )


async def _save_vote(telegram_id: int, vote: str):
    """Enregistre / remplace le vote."""
    async with sync_get_db() as cur:
        await cur.execute(
            "INSERT INTO users (telegram_id, created_at, vote, vote_at) "
            "VALUES (%s, NOW(), %s, NOW()) AS new_row "
            "ON DUPLICATE KEY UPDATE "
            "  vote    = new_row.vote, "
            "  vote_at = NOW()",
            (telegram_id, vote),
        )


async def _get_user_name(telegram_id: int) -> str:
    """Nom de l'utilisateur pour l'historique fichier (fallback à ''
    si non enregistré)."""
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT name FROM users WHERE telegram_id = %s", (telegram_id,)
        )
        row = await cur.fetchone()
    return (row.get("name") or "") if row else ""


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS FICHIER (append seulement — l'historique est conservé)
# ══════════════════════════════════════════════════════════════════════════════

def _sanitize(value: str) -> str:
    """Retire les retours à la ligne pour garder une entrée par ligne dans
    le fichier historique."""
    return (value or "").replace("\n", " ").replace("\r", " ").strip()


def _append_motivation_file(telegram_id: int, name: str, motivation: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f'[{ts}] telegram_id={telegram_id} | '
            f'nom="{_sanitize(name)}" | '
            f'motivation="{_sanitize(motivation)}"\n'
        )
        with MOTIVATIONS_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.exception("[engagement] échec écriture motivations.txt")
        log_error("Échec écriture motivations.txt",
                  f"telegram_id={telegram_id} — {e}")


def _append_vote_file(telegram_id: int, name: str, vote: str):
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = (
            f'[{ts}] telegram_id={telegram_id} | '
            f'nom="{_sanitize(name)}" | '
            f'vote={vote}\n'
        )
        with VOTES_FILE.open("a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        logger.exception("[engagement] échec écriture votes.txt")
        log_error("Échec écriture votes.txt",
                  f"telegram_id={telegram_id} — {e}")


# ══════════════════════════════════════════════════════════════════════════════
# ENTRÉE PUBLIQUE — appelée depuis telegram_page/start_handler.py
# ══════════════════════════════════════════════════════════════════════════════

def is_engagement_payload(start_param: str) -> bool:
    """Retourne True si le start_param correspond à un scénario d'engagement.
    Utilisé par process_start_link() pour router avant tout autre traitement."""
    if not start_param:
        return False
    if not start_param.startswith(DEEPLINK_PREFIX):
        return False
    key = start_param[len(DEEPLINK_PREFIX):]
    return key in SCENARIOS


async def handle_deeplink(update: Update,
                          context: ContextTypes.DEFAULT_TYPE,
                          start_param: str) -> bool:
    """Route un start_param vers le bon scénario d'engagement.

    Appelée par process_start_link() dans telegram_page/start_handler.py.

    Retourne True si un scénario a été traité (l'appelant doit alors
    interrompre son traitement normal), False sinon.
    """
    if not start_param or not start_param.startswith(DEEPLINK_PREFIX):
        return False

    key = start_param[len(DEEPLINK_PREFIX):]
    scenario = SCENARIOS.get(key)
    if scenario is None:
        logger.info(f"[engagement] scénario inconnu: {key!r}")
        return False

    try:
        await scenario(update, context)
        return True
    except Exception as e:
        logger.exception(f"[engagement] erreur scénario {key}")
        log_error(f"Erreur scénario {key}",
                  f"user_id={update.effective_user.id} — {e}")
        # Même en cas d'erreur on retourne True : l'utilisateur a reçu
        # (ou aurait dû recevoir) une réponse d'engagement, l'appelant
        # ne doit pas enchaîner sur un autre flow.
        return True


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 1 — VERIFY
# ══════════════════════════════════════════════════════════════════════════════

async def handle_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global _verify_count_since_boot
    _verify_count_since_boot += 1

    user_id = update.effective_user.id
    row = await _fetch_user(user_id)

    name  = (row or {}).get("name")  if row else None
    phone = (row or {}).get("phone") if row else None
    level = (row or {}).get("level") if row else None

    all_ok = all(x and str(x).strip() for x in (name, phone, level))

    if all_ok:
        # Cas 1 : tout est bon
        await update.message.reply_text(
            "🎉 <b>Félicitations !</b>\n"
            "\n"
            "Votre inscription est bien validée. ✅\n"
            "\n"
            "Vous êtes officiellement dans la liste des <b>participants</b>.\n"
            "\n"
            "🍀 Bonne chance pour le tirage de samedi !",
            parse_mode="HTML",
        )
        return

    # Cas 2 : il manque quelque chose — on affiche précisément quoi
    def mark(v):
        return "✅" if (v and str(v).strip()) else "❌"

    checklist = (
        f"{mark(name)}  Nom\n"
        f"{mark(phone)}  Numéro\n"
        f"{mark(level)}  Niveau"
    )

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("▶️  Terminer mon inscription",
                              callback_data="resume_registration")]
    ])

    await update.message.reply_text(
        "⚠️ <b>Attention !</b>\n"
        "\n"
        "Votre inscription n'est <b>pas encore complète</b>.\n"
        "\n"
        "Voici ce qui manque 👇\n"
        "\n"
        f"{checklist}\n"
        "\n"
        "Un dernier effort et vous êtes dans la liste des gagnants 🎯",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 2 — MOTIVATION
# ══════════════════════════════════════════════════════════════════════════════

async def handle_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = await _fetch_user(user_id)
    existing = (row or {}).get("motivation") if row else None

    if existing and str(existing).strip():
        # Déjà répondu → propose Voir / Modifier
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👀  Voir ma motivation",
                                  callback_data="eng:mot:view")],
            [InlineKeyboardButton("✏️  Modifier ma motivation",
                                  callback_data="eng:mot:edit")],
        ])
        await update.message.reply_text(
            "✅ <b>Nous avons déjà reçu votre motivation.</b>\n"
            "\n"
            "🙏 Merci beaucoup pour votre participation !\n"
            "\n"
            "Que souhaitez-vous faire ? 👇",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    # Première fois → on demande la motivation
    await _ask_motivation(update, context)


async def _ask_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Envoie la question et arme le flag pour capter la réponse suivante."""
    _arm_motivation_wait(update.effective_user.id)

    # On envoie via update.message si dispo, sinon via bot.send_message
    text = (
        "🔥 <b>C'est votre moment !</b>\n"
        "\n"
        "Nous voulons <b>vous connaître</b>.\n"
        "\n"
        "💰 <b>200 dollars</b> attendent le prochain gagnant.\n"
        "\n"
        "En <b>3 phrases</b>, expliquez-nous 👇\n"
        "\n"
        "<i>Pourquoi VOUS devriez faire partie des gagnants ?</i>\n"
        "\n"
        "✍️ Envoyez votre réponse maintenant."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML")
    else:
        await context.bot.send_message(chat_id=update.effective_user.id,
                                       text=text, parse_mode="HTML")


async def _on_motivation_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback des boutons Voir / Modifier."""
    query = update.callback_query
    await query.answer()
    action = query.data.split(":")[-1]  # view | edit
    user_id = query.from_user.id

    if action == "view":
        row = await _fetch_user(user_id)
        motivation = (row or {}).get("motivation") if row else None
        if motivation and str(motivation).strip():
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "📝 <b>Votre motivation enregistrée :</b>\n"
                    "\n"
                    f"<i>{motivation}</i>"
                ),
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="🤔 Aucune motivation enregistrée pour le moment.",
            )
        return

    if action == "edit":
        # Réutilise exactement le même prompt que la 1ère fois
        await _ask_motivation(update, context)
        return


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 3 — VOTE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    row = await _fetch_user(user_id)
    existing = (row or {}).get("vote") if row else None

    if existing and str(existing).strip():
        # Déjà voté → propose Voir / Modifier
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("👀  Voir mon vote",
                                  callback_data="eng:vote:view")],
            [InlineKeyboardButton("✏️  Modifier mon vote",
                                  callback_data="eng:vote:edit")],
        ])
        await update.message.reply_text(
            "✅ <b>Vous avez déjà participé au vote.</b>\n"
            "\n"
            "🙏 Merci pour votre participation !\n"
            "\n"
            "Que souhaitez-vous faire ? 👇",
            parse_mode="HTML",
            reply_markup=keyboard,
        )
        return

    await _ask_vote(update, context)


async def _ask_vote(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Affiche la question de vote avec les 3 boutons."""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(VOTE_LABELS["1"], callback_data="eng:vote:cast:1")],
        [InlineKeyboardButton(VOTE_LABELS["2"], callback_data="eng:vote:cast:2")],
        [InlineKeyboardButton(VOTE_LABELS["3"], callback_data="eng:vote:cast:3")],
    ])
    text = (
        "🗳️ <b>Votre avis compte !</b>\n"
        "\n"
        "Selon vous, combien de <b>gagnants</b> devrions-nous "
        "sélectionner chaque samedi ?\n"
        "\n"
        "👇 Choisissez ci-dessous."
    )
    if update.message:
        await update.message.reply_text(text, parse_mode="HTML",
                                        reply_markup=keyboard)
    else:
        await context.bot.send_message(chat_id=update.effective_user.id,
                                       text=text, parse_mode="HTML",
                                       reply_markup=keyboard)


async def _on_vote_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Callback : clic sur un choix ou sur Voir / Modifier."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")  # eng:vote:<action>[:val]
    action = parts[2]
    user_id = query.from_user.id

    if action == "view":
        row = await _fetch_user(user_id)
        vote = (row or {}).get("vote") if row else None
        if vote:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🗳️ <b>Votre vote enregistré :</b>\n"
                    "\n"
                    f"{VOTE_LABELS.get(str(vote), str(vote))}"
                ),
                parse_mode="HTML",
            )
        else:
            await context.bot.send_message(
                chat_id=user_id, text="🤔 Aucun vote enregistré pour le moment.",
            )
        return

    if action == "edit":
        await _ask_vote(update, context)
        return

    if action == "cast":
        try:
            value = parts[3]
        except IndexError:
            return
        if value not in VOTE_LABELS:
            return

        try:
            await _save_vote(user_id, value)
        except Exception as e:
            logger.exception(f"[engagement] échec sauvegarde vote {user_id}")
            log_error("Échec sauvegarde vote", f"user_id={user_id} — {e}")

        try:
            name = await _get_user_name(user_id)
        except Exception:
            name = ""
        _append_vote_file(user_id, name, value)

        # Confirmation : édite le message pour éviter l'empilement
        try:
            await query.edit_message_text(
                "🎉 <b>Merci pour votre vote !</b>\n"
                "\n"
                f"Votre choix : <b>{VOTE_LABELS[value]}</b>\n"
                "\n"
                "🙏 Votre avis nous aide énormément.",
                parse_mode="HTML",
            )
        except Exception:
            await context.bot.send_message(
                chat_id=user_id,
                text=(
                    "🎉 <b>Merci pour votre vote !</b>\n"
                    "\n"
                    f"Votre choix : <b>{VOTE_LABELS[value]}</b>"
                ),
                parse_mode="HTML",
            )


# ══════════════════════════════════════════════════════════════════════════════
# SCÉNARIO 4 — SHARE
# ══════════════════════════════════════════════════════════════════════════════

async def handle_share(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🚀  Inviter mes amis maintenant",
                              url="https://fdkservice.com/share")]
    ])
    await update.message.reply_text(
        "🔥 <b>Vous connaissez quelqu'un qui mérite ces 200$ ?</b>\n"
        "\n"
        "Partagez l'aventure. 💫\n"
        "\n"
        "Un ami invité, c'est une <b>chance de plus</b> pour toute la communauté.\n"
        "\n"
        "🤝 Ensemble, on change des vies.\n"
        "\n"
        "👇 Cliquez pour partager.",
        parse_mode="HTML",
        reply_markup=keyboard,
    )


# ══════════════════════════════════════════════════════════════════════════════
# CAPTURE DE LA RÉPONSE TEXTE (motivation)
# ══════════════════════════════════════════════════════════════════════════════
# Un seul MessageHandler global, groupe 10 : passe uniquement si le flag
# engagement_awaiting est armé. Sinon, l'update continue son chemin
# vers le tunnel d'inscription et le log_unhandled_message (groupe 99).

async def capture_engagement_response(update: Update,
                                       context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if not _is_awaiting_motivation(user_id):
        return  # rien à intercepter, on laisse passer aux autres groupes

    motivation = (update.message.text or "").strip()

    if not motivation:
        try:
            await update.message.reply_text(
                "🤔 Message vide reçu.\n"
                "\n"
                "Envoyez-nous votre motivation en 3 phrases 👇"
            )
        except Exception:
            pass
        # Flag toujours armé, on empêche la propagation vers form_engine
        raise ApplicationHandlerStop

    # Sauvegarde DB
    try:
        await _save_motivation(user_id, motivation)
    except Exception as e:
        logger.exception(f"[engagement] échec sauvegarde motivation {user_id}")
        log_error("Échec sauvegarde motivation", f"user_id={user_id} — {e}")

    # Historique fichier
    try:
        name = await _get_user_name(user_id)
    except Exception:
        name = ""
    _append_motivation_file(user_id, name, motivation)

    # Désarme le flag
    _disarm_motivation_wait(user_id)

    try:
        await update.message.reply_text(
            "🎉 <b>Merci pour votre motivation !</b>\n"
            "\n"
            "Elle est bien enregistrée. ✅\n"
            "\n"
            "🍀 Bonne chance pour samedi !",
            parse_mode="HTML",
        )
    except Exception as e:
        logger.exception(f"[engagement] échec confirmation motivation {user_id}")
        log_error("Échec confirmation motivation", f"user_id={user_id} — {e}")

    # IMPORTANT : on stoppe la propagation aux autres groupes.
    # Sinon le ConversationHandler de form_engine (groupe 1) réagirait
    # aussi au message texte s'il est en état actif.
    raise ApplicationHandlerStop


# ══════════════════════════════════════════════════════════════════════════════
# COMMANDE ADMIN /engagement
# ══════════════════════════════════════════════════════════════════════════════

async def cmd_engagement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return

    try:
        async with sync_get_db() as cur:
            # Total motivations
            await cur.execute(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE motivation IS NOT NULL AND motivation <> ''"
            )
            total_motivations = int((await cur.fetchone())["n"])

            # Total votes
            await cur.execute(
                "SELECT COUNT(*) AS n FROM users "
                "WHERE vote IS NOT NULL AND vote <> ''"
            )
            total_votes = int((await cur.fetchone())["n"])

            # Répartition
            await cur.execute(
                "SELECT vote, COUNT(*) AS n FROM users "
                "WHERE vote IS NOT NULL AND vote <> '' "
                "GROUP BY vote"
            )
            repart = {r["vote"]: int(r["n"]) for r in await cur.fetchall()}
    except Exception as e:
        logger.exception("[engagement] erreur /engagement")
        log_error("Erreur /engagement", str(e))
        try:
            await update.message.reply_text("❌ Erreur lors du calcul des stats.")
        except Exception:
            pass
        return

    def pct(n):
        return f"{(n * 100 / total_votes):.1f}%" if total_votes else "0%"

    n1 = repart.get("1", 0)
    n2 = repart.get("2", 0)
    n3 = repart.get("3", 0)

    report = (
        f"📊 <b>Engagement — {datetime.now().strftime('%d/%m/%Y %H:%M')}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        f"🔎 <b>Vérifications</b> (depuis dernier démarrage) : "
        f"<b>{_verify_count_since_boot}</b>\n"
        "\n"
        f"✍️ <b>Motivations reçues</b> : <b>{total_motivations}</b>\n"
        "\n"
        f"🗳️ <b>Votes reçus</b> : <b>{total_votes}</b>\n"
        "\n"
        f"<b>Répartition des votes</b>\n"
        f"• 🥇 1 gagnant  : <b>{n1}</b> ({pct(n1)})\n"
        f"• 🥈 2 gagnants : <b>{n2}</b> ({pct(n2)})\n"
        f"• 🥉 3 gagnants : <b>{n3}</b> ({pct(n3)})"
    )

    try:
        await update.message.reply_text(report, parse_mode="HTML")
    except Exception as e:
        logger.exception("[engagement] échec envoi /engagement")
        log_error("Échec envoi /engagement", str(e))


# ══════════════════════════════════════════════════════════════════════════════
# TABLE DE ROUTAGE — pour ajouter un scénario, ajoute une entrée + la fonction
# ══════════════════════════════════════════════════════════════════════════════

SCENARIOS = {
    "verify":     handle_verify,
    "motivation": handle_motivation,
    "vote":       handle_vote,
    "share":      handle_share,
}


# ══════════════════════════════════════════════════════════════════════════════
# ENREGISTREMENT — appelé UNE FOIS depuis main.py après le registration_conv
# ══════════════════════════════════════════════════════════════════════════════

def register_engagement_handlers(app: Application):
    """Enregistre les handlers du module (hors dispatch /start).

    Le dispatch des deep links (/start fdk_concept_capital_*) est fait
    par telegram_page/start_handler.py qui appelle handle_deeplink().
    Ici on enregistre uniquement les handlers de suivi (boutons,
    capture texte motivation, commande admin).

    Doit être appelé APRÈS `app.add_handler(registration_conv)` dans main.py
    pour que le callback 'resume_registration' reste bien capté par le tunnel
    d'inscription existant (priorité au ConversationHandler).
    """

    # 1) Boutons scénario motivation (Voir / Modifier) — groupe -1 pour
    #    prévalence sur d'éventuels autres CallbackQueryHandler.
    app.add_handler(
        CallbackQueryHandler(_on_motivation_button, pattern=r"^eng:mot:(view|edit)$"),
        group=-1,
    )

    # 2) Boutons scénario vote (Voir / Modifier / Cast) — même logique
    app.add_handler(
        CallbackQueryHandler(_on_vote_button,
                             pattern=r"^eng:vote:(view|edit|cast:[123])$"),
        group=-1,
    )

    # 3) Capture de la réponse texte de motivation.
    #    Groupe -1 : AVANT tous les autres handlers (form_engine est en
    #    groupe 1). C'est nécessaire parce que le ConversationHandler de
    #    form_engine capte tout texte quand un user est en état FORM_STEP.
    #    Le handler ne fait rien tant que le flag engagement_awaiting
    #    n'est pas armé -> aucun impact sur les autres flows.
    #    Il lève ApplicationHandlerStop quand il traite un message pour
    #    empêcher form_engine de le récupérer aussi.
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND
            & filters.UpdateType.MESSAGE
            & filters.ChatType.PRIVATE,
            capture_engagement_response,
        ),
        group=-1,
    )

    # 4) Commande admin
    app.add_handler(CommandHandler("engagement", cmd_engagement), group=5)

    # 5) S'assure que les nouvelles colonnes existent, au premier démarrage
    #    (via post_init pour rester dans la boucle asyncio du bot)
    previous_post_init = app.post_init

    async def _combined_post_init(application):
        if previous_post_init is not None:
            await previous_post_init(application)
        try:
            await ensure_engagement_schema()
            logger.info("[engagement] schéma OK ✓")
            print("[engagement] schéma OK ✓")
        except Exception as e:
            logger.exception("[engagement] échec ensure_engagement_schema")
            log_error("Échec ensure_engagement_schema", str(e))

    app.post_init = _combined_post_init