"""
reminder_engine.py — Moteur de relance FDK CAPITAL CONCEPT
═══════════════════════════════════════════════════════════════════════════════

Cycle complet pour chaque prospect (user validé mais formulaire incomplet) :

  Phase 1 : toutes les 10 min pendant 2 h   → 12 relances (T+10min → T+120min)
  Phase 2 : toutes les heures pendant 22 h  → 22 relances (T+3h  → T+24h)
  Phase 3 : silence jusqu'à J+7
  Phase 4 : ULTIME relance à J+7
      - envoi OK  → promotion catégorie PROSPECT → "Actif (ALL)"
      - envoi KO  → retrait de PROSPECT + suppression de la ligne users
                     (le user a bloqué le bot / n'est plus joignable)

Quand un user complète son formulaire (n'importe quand pendant le cycle),
le main appelle promote_prospect_to_main_active() pour le déplacer
PROSPECT → FDK CONCEPT CAPITAL LISTE ACTIFS.

Validation téléphone : normalize_phone_text() accepte texte libre s'il
ressemble à un numéro (+ / espaces / tirets tolérés, min 8 chiffres),
sinon le main renvoie PHONE_FORMAT_HINT.

Total : 35 relances envoyées avant décision finale.
"""

import re
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from telegram.error import Forbidden, BadRequest

from db import get_db as sync_get_db

logger = logging.getLogger("reminder_engine")

# ══════════════════════════════════════════════════════════════════════════════
# CATÉGORIES
# ══════════════════════════════════════════════════════════════════════════════

PROSPECT_CATEGORY    = "PROSPECT"
ACTIVE_MAIN_CATEGORY = "FDK CONCEPT CAPITAL LISTE ACTIFS"
ACTIVE_ALL_CATEGORY  = "Actif (ALL)"

# ══════════════════════════════════════════════════════════════════════════════
# LOG D'ERREURS (mutualisé avec main.py via le même fichier errors.log)
# ══════════════════════════════════════════════════════════════════════════════
#
# CORRECTIF : repli vers /tmp si errors.log n'est pas inscriptible
# (même filet que main.py:_ensure_errors_log_writable). Sans ça, une
# simple erreur de permission sur le fichier de log finissait par
# faire remonter une DEUXIÈME exception (PermissionError) par-dessus
# l'erreur d'origine qu'on essayait justement de logger.

def _resolve_errors_log_path() -> Path:
    candidate = Path(__file__).resolve().parent / "errors.log"
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        with candidate.open("a", encoding="utf-8"):
            pass
        return candidate
    except (PermissionError, OSError):
        fallback = Path("/tmp") / "fdk_bot_errors.log"
        try:
            with fallback.open("a", encoding="utf-8"):
                pass
        except Exception:
            pass
        return fallback


_ERRORS_LOG_PATH = _resolve_errors_log_path()


def _log_error(title: str, detail: str = ""):
    """Écrit dans errors.log (ou son repli /tmp). Ne lève jamais d'exception."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"[{ts}] [reminder] {title}"
        if detail:
            snippet = str(detail)[:4000].replace("\n", " | ")
            line += f" — {snippet}"
        with _ERRORS_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        logger.exception("[reminder] log_error a échoué")


# ══════════════════════════════════════════════════════════════════════════════
# VALIDATION TÉLÉPHONE (texte libre autorisé si ça ressemble à un numéro)
# ══════════════════════════════════════════════════════════════════════════════

PHONE_FORMAT_HINT = (
    "📱 <b>Numéro non reconnu</b>\n\n"
    "Envoyez votre numéro au format international, par exemple :\n"
    "<code>+229 00 00 00 00 00</code>\n\n"
    "Ou cliquez sur le bouton <b>« 📱 Partager mon numéro »</b> au niveau "
    "de votre clavier."
)

_PHONE_STRIP_RE = re.compile(r"[\s\-\(\)\.]")
_PHONE_VALID_RE = re.compile(r"\+?\d{8,15}")


def normalize_phone_text(text: str):
    """Retourne le numéro nettoyé si valide, None sinon.
    Permissif : accepte +, espaces, tirets, parenthèses, points."""
    if not text:
        return None
    cleaned = _PHONE_STRIP_RE.sub("", text.strip())
    if not _PHONE_VALID_RE.fullmatch(cleaned):
        return None
    return cleaned


# ══════════════════════════════════════════════════════════════════════════════
# CATÉGORIES — helpers async (import paresseux pour éviter les circulaires)
# ══════════════════════════════════════════════════════════════════════════════

async def _add_to_category(category: str, user_id: int):
    try:
        from telegram_page.categorie import add_members_to_category
        await add_members_to_category(category, [user_id])
    except Exception as e:
        logger.exception(f"[reminder] échec add_to_category({category}, {user_id})")
        _log_error(f"Échec add_to_category {category}", f"user_id={user_id} — {e}")


async def _remove_from_category(category: str, user_id: int):
    # CORRECTIF : la vraie fonction s'appelle remove_member_from_category
    # (SINGULIER) et prend un user_id nu — pas remove_members_from_category
    # (qui n'existe pas) avec une liste. C'est déjà l'API utilisée ailleurs
    # dans le projet (broadcast_send.py : appelée un par un dans une boucle).
    try:
        from telegram_page.categorie import remove_member_from_category
        await remove_member_from_category(category, user_id)
    except Exception as e:
        logger.exception(
            f"[reminder] échec remove_from_category({category}, {user_id})"
        )
        _log_error(
            f"Échec remove_from_category {category}",
            f"user_id={user_id} — {e}",
        )


async def add_to_prospect(user_id: int):
    """Nouvel arrivant validé sur le canal principal → PROSPECT."""
    await _add_to_category(PROSPECT_CATEGORY, user_id)


async def promote_prospect_to_main_active(user_id: int):
    """Formulaire terminé : PROSPECT → FDK CONCEPT CAPITAL LISTE ACTIFS."""
    await _add_to_category(ACTIVE_MAIN_CATEGORY, user_id)
    await _remove_from_category(PROSPECT_CATEGORY, user_id)


async def promote_prospect_to_active_all(user_id: int):
    """Ultime relance J+7 délivrée : PROSPECT → Actif (ALL)."""
    await _add_to_category(ACTIVE_ALL_CATEGORY, user_id)
    await _remove_from_category(PROSPECT_CATEGORY, user_id)


async def _delete_user_row(user_id: int):
    """Supprime la ligne du user dans users (utilisé quand injoignable à J+7)."""
    try:
        async with sync_get_db() as cur:
            await cur.execute(
                "DELETE FROM users WHERE telegram_id = %s", (user_id,)
            )
    except Exception as e:
        logger.exception(f"[reminder] échec DELETE user {user_id}")
        _log_error("Échec DELETE user", f"user_id={user_id} — {e}")


async def _purge_unreachable_prospect(user_id: int):
    """J+7 : user injoignable → retrait PROSPECT + suppression DB."""
    await _remove_from_category(PROSPECT_CATEGORY, user_id)
    await _delete_user_row(user_id)


# ══════════════════════════════════════════════════════════════════════════════
# DB — utils internes
# ══════════════════════════════════════════════════════════════════════════════

async def _get_incomplete_prospects():
    """Tous les users incomplets (peu importe la date de création :
    le J+7 se calcule à partir de created_at de chacun)."""
    async with sync_get_db() as cur:
        await cur.execute(
            "SELECT telegram_id, created_at, last_reminder_at, reminder_count, "
            "  name, phone, level "
            "FROM users WHERE "
            "  (name IS NULL OR name = '') "
            "  OR (phone IS NULL OR phone = '') "
            "  OR (level IS NULL OR level = '')"
        )
        return await cur.fetchall()


async def _mark_reminder_sent(user_id: int):
    async with sync_get_db() as cur:
        await cur.execute(
            "UPDATE users SET last_reminder_at=NOW(), "
            "reminder_count=reminder_count+1 WHERE telegram_id=%s",
            (user_id,),
        )


# ══════════════════════════════════════════════════════════════════════════════
# CADENCE — planification des envois
# ══════════════════════════════════════════════════════════════════════════════

PHASE1_COUNT    = 12                          # 12 × 10 min = 2 h
PHASE1_INTERVAL = timedelta(minutes=10)

PHASE2_COUNT    = 22                          # 22 × 1 h : de T+3h à T+24h
PHASE2_START    = timedelta(hours=3)          # 1 h de gap après phase 1
PHASE2_INTERVAL = timedelta(hours=1)

PHASE3_DELAY    = timedelta(days=7)           # ultime relance
TOTAL_REMINDERS = PHASE1_COUNT + PHASE2_COUNT + 1   # 35


def compute_next_due(created_at, reminder_count: int):
    """Datetime auquel la prochaine relance doit partir, ou None si terminé
    (ou si created_at est invalide/non exploitable)."""
    if reminder_count >= TOTAL_REMINDERS or created_at is None:
        return None

    # CORRECTIF : created_at doit être un vrai datetime.datetime. Si une
    # ligne `users` a une date invalide en base (ex: '0000-00-00 00:00:00'
    # — souvent une ligne ancienne créée avant migration), PyMySQL ne
    # parvient pas à la convertir et la renvoie telle quelle en str. Sans
    # ce garde-fou, `created_at + timedelta` explosait avec
    # "can only concatenate str (not 'datetime.timedelta') to str" — et
    # comme la ligne restait incomplète, ça replantait à CHAQUE cycle.
    if not isinstance(created_at, datetime):
        if isinstance(created_at, str):
            try:
                created_at = datetime.strptime(created_at.strip(), "%Y-%m-%d %H:%M:%S")
            except ValueError:
                logger.warning(
                    f"[reminder] created_at non parsable, prospect ignoré : "
                    f"{created_at!r}"
                )
                return None
        else:
            logger.warning(
                f"[reminder] created_at de type inattendu ({type(created_at)!r}), "
                f"prospect ignoré : {created_at!r}"
            )
            return None

    if reminder_count < PHASE1_COUNT:
        # T+10min, T+20min, ... T+120min
        return created_at + PHASE1_INTERVAL * (reminder_count + 1)
    if reminder_count < PHASE1_COUNT + PHASE2_COUNT:
        # T+3h, T+4h, ... T+24h
        offset = PHASE2_START + PHASE2_INTERVAL * (reminder_count - PHASE1_COUNT)
        return created_at + offset
    # Ultime : J+7
    return created_at + PHASE3_DELAY


def is_final_reminder(reminder_count: int) -> bool:
    return reminder_count == TOTAL_REMINDERS - 1


# ══════════════════════════════════════════════════════════════════════════════
# CORPUS DE MESSAGES — FOMO, urgence, enjeux concrets
# ══════════════════════════════════════════════════════════════════════════════
# Angles utilisés (rotation) :
#   • Social proof / FOMO ("X personnes se sont inscrites après vous")
#   • Enjeux concrets (tirage samedi + formation + fonds réels)
#   • Effort minimal ("45 secondes, un clic")
#   • Perte de position ("vous étiez arrivé avant eux")
#   • Alerte de retrait imminent
#   • Culpabilité positive ("vous avez fait le plus dur")
#   • Formation verrouillée
# ══════════════════════════════════════════════════════════════════════════════

_resume_button = InlineKeyboardMarkup([
    [InlineKeyboardButton(
        "▶️ Terminer mon inscription (45s)",
        callback_data="resume_registration",
    )]
])

# ── Phase 1 : 12 messages, un par relance (T+10min → T+120min) ──────────────
_PHASE1_MESSAGES = [
    # R1 — T+10min
    "⏳ <b>Votre place n'est pas encore réservée</b>\n\n"
    "Il vous manque juste quelques infos pour figurer sur la liste officielle "
    "du tirage de samedi.\n\n"
    "👇 Cliquez pour finir — <b>45 secondes maximum</b>.",

    # R2 — T+20min
    "📈 <b>La liste avance sans vous</b>\n\n"
    "<b>12 personnes</b> viennent de finaliser leur inscription pendant que "
    "la vôtre est en pause. Elles sont arrivées <b>après</b> vous.\n\n"
    "👇 Reprenez votre place, ça prend 45 secondes.",

    # R3 — T+30min
    "❌ <b>Sans formulaire complet, vous n'êtes PAS dans le tirage</b>\n\n"
    "• Pas dans la liste des participants samedi\n"
    "• Pas d'accès à la formation de préparation\n"
    "• Pas d'accès aux fonds en cas de gain\n\n"
    "Tout ça pour <b>45 secondes</b>.\n\n"
    "👇",

    # R4 — T+40min
    "🚨 <b>23 nouvelles inscriptions depuis votre arrivée</b>\n\n"
    "Ces personnes étaient <b>derrière vous</b> dans la file. Elles sont "
    "devant maintenant.\n\n"
    "C'est encore rattrapable — mais chaque minute compte.\n\n"
    "👇 Un clic.",

    # R5 — T+50min
    "💰 <b>Rappel : les gagnants reçoivent des fonds réels</b>\n\n"
    "Mais uniquement s'ils sont sur la liste. Uniquement s'ils ont suivi la "
    "formation de préparation.\n\n"
    "Les deux passent par ce formulaire.\n\n"
    "👇 45 secondes pour tout débloquer.",

    # R6 — T+60min
    "⌛ <b>1 heure que votre inscription attend</b>\n\n"
    "Pendant ce temps :\n"
    "• <b>35 personnes</b> se sont inscrites après vous\n"
    "• Toutes seront devant vous dans le tirage\n"
    "• Vous, vous n'êtes nulle part\n\n"
    "Un clic. 45 secondes. C'est tout.\n\n"
    "👇",

    # R7 — T+70min
    "🔥 <b>Le tirage de samedi ne vous attendra pas</b>\n\n"
    "Nous n'appelons pas les inscrits incomplets. Ce n'est pas de la mauvaise "
    "volonté — c'est simplement que nous n'avons ni votre nom, ni votre "
    "numéro, ni votre niveau.\n\n"
    "👇 Trois infos. 45 secondes.",

    # R8 — T+80min
    "🎯 <b>Vous avez fait le plus dur</b>\n\n"
    "Vous avez rejoint le canal. Vous avez été validé. Vous êtes éligible.\n\n"
    "Ne laissez pas tomber à <b>45 secondes</b> de la ligne d'arrivée.\n\n"
    "👇 Terminez maintenant.",

    # R9 — T+90min
    "📉 <b>Votre position dans la file : perdue</b>\n\n"
    "Vous étiez arrivé parmi les premiers. Aujourd'hui, plus de "
    "<b>40 personnes</b> vous sont passées devant simplement parce qu'elles "
    "ont pris 45 secondes pour finir.\n\n"
    "C'est encore possible de tout rattraper.\n\n"
    "👇 Un clic.",

    # R10 — T+100min
    "⚡ <b>La formation démarre bientôt</b>\n\n"
    "Ceux qui gagnent samedi <b>sans avoir suivi la formation</b> ne reçoivent "
    "rien. C'est la règle.\n\n"
    "Et la formation, on n'y a accès qu'une fois le formulaire terminé.\n\n"
    "👇 45 secondes pour tout débloquer.",

    # R11 — T+110min
    "❓ <b>Un problème pour finir ?</b>\n\n"
    "Si le bouton ne s'affiche pas ou si vous êtes bloqué, contactez "
    "directement @fiacrekpanou.\n\n"
    "Sinon, il ne reste qu'un clic.\n\n"
    "👇",

    # R12 — T+120min
    "⏰ <b>Fin des relances rapprochées</b>\n\n"
    "Après ce message, nous espacerons nos rappels. Si vous ne finalisez pas "
    "dans les prochaines heures, vous serez <b>progressivement retiré</b> de "
    "la liste des participants.\n\n"
    "Il vous reste ce clic. 45 secondes.\n\n"
    "👇",
]

# ── Phase 2 : 12 messages horaires, cycle sur les 22 relances ────────────────
_PHASE2_MESSAGES = [
    # H1
    "⏳ <b>Toujours en attente</b>\n\n"
    "Votre inscription n'est toujours pas complète. À chaque heure qui passe, "
    "de nouvelles personnes prennent la place qui aurait pu être la vôtre.\n\n"
    "👇 45 secondes suffisent.",

    # H2
    "🚨 <b>Ils sont maintenant plus de 50 devant vous</b>\n\n"
    "Ces 50 personnes se sont inscrites <b>après</b> vous. Elles ont pris les "
    "45 secondes qui manquent.\n\n"
    "Vous êtes encore éligible. Pour combien de temps ?\n\n"
    "👇",

    # H3
    "💸 <b>Vous laissez passer vos chances</b>\n\n"
    "Chaque tirage de samedi distribue des fonds réels. Chaque semaine sans "
    "inscription complète = une semaine sans la moindre chance de figurer "
    "parmi les gagnants.\n\n"
    "👇 On termine maintenant ?",

    # H4
    "🎯 <b>Rappel des règles</b>\n\n"
    "Pas de formulaire → pas dans la liste.\n"
    "Pas dans la liste → pas dans le tirage.\n"
    "Pas dans le tirage → pas de fonds.\n\n"
    "Tout part de ce bouton. 45 secondes.\n\n"
    "👇",

    # H5
    "😔 <b>Vous étiez si proche</b>\n\n"
    "Le plus difficile est fait. Vous êtes déjà validé. Il ne reste que trois "
    "infos à donner : niveau, numéro, nom.\n\n"
    "👇 On finit ?",

    # H6
    "📆 <b>Le tirage se rapproche</b>\n\n"
    "Chaque samedi, les gagnants sont tirés au sort <b>en direct</b> devant "
    "toute la communauté — uniquement parmi les inscriptions complètes.\n\n"
    "Vous n'y êtes pas encore. Réparable en 45 secondes.\n\n"
    "👇",

    # H7
    "🔒 <b>Formation encore verrouillée</b>\n\n"
    "L'accès à la formation FDK — celle qui prépare à recevoir les fonds — "
    "est bloqué tant que le formulaire n'est pas terminé.\n\n"
    "Une fois débloquée, elle est à vous à vie.\n\n"
    "👇 45 secondes pour la débloquer.",

    # H8
    "⚡ <b>Un clic. Trois infos. C'est fini.</b>\n\n"
    "Nous ne demandons ni email, ni pièce d'identité, ni versement. Juste "
    "votre niveau, votre numéro WhatsApp et votre nom.\n\n"
    "👇",

    # H9
    "🤝 <b>Nous vous avons validé pour une raison</b>\n\n"
    "Votre demande d'adhésion a été acceptée parce que vous êtes éligible. "
    "Ne gâchez pas cette place — elle est convoitée.\n\n"
    "👇 45 secondes.",

    # H10
    "🕰 <b>Cette semaine ou jamais ?</b>\n\n"
    "Les inscriptions incomplètes finissent par être retirées définitivement "
    "de nos listes. Vous avez encore la main.\n\n"
    "👇 On boucle maintenant.",

    # H11
    "🏆 <b>Les gagnants de samedi dernier</b> ont tous rempli ce formulaire.\n\n"
    "Sans exception. Il n'y a pas de porte dérobée : la seule façon d'être "
    "tiré au sort est d'y figurer.\n\n"
    "👇 45 secondes.",

    # H12
    "💬 <b>Une question ? Un blocage ?</b>\n\n"
    "Écrivez à @fiacrekpanou, il vous débloque en direct.\n\n"
    "Sinon, un clic sur le bouton et tout se finit tout seul.\n\n"
    "👇",
]

# ── Phase 3 : ultime relance à J+7 ───────────────────────────────────────────
_FINAL_MESSAGE = (
    "🚨 <b>DERNIER RAPPEL — Suppression imminente</b>\n\n"
    "Cela fait <b>7 jours</b> que votre inscription est incomplète.\n\n"
    "Sans réponse dans les prochaines heures, votre compte sera "
    "<b>définitivement retiré</b> de la liste des participants FDK CAPITAL "
    "CONCEPT — et vous n'apparaîtrez plus dans aucun tirage.\n\n"
    "C'est votre <b>dernière chance</b>. 45 secondes.\n\n"
    "👇"
)


def get_reminder_text(reminder_count: int) -> str:
    """Texte de la relance à envoyer (indexé sur reminder_count actuel,
    AVANT incrément)."""
    if reminder_count < PHASE1_COUNT:
        return _PHASE1_MESSAGES[reminder_count]
    if reminder_count < PHASE1_COUNT + PHASE2_COUNT:
        idx = (reminder_count - PHASE1_COUNT) % len(_PHASE2_MESSAGES)
        return _PHASE2_MESSAGES[idx]
    return _FINAL_MESSAGE


# ══════════════════════════════════════════════════════════════════════════════
# LISTE DES PROSPECTS (filtre catégorie côté envoi)
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_prospect_ids() -> set:
    try:
        from telegram_page.categorie import get_category_members
        result = await get_category_members(
            PROSPECT_CATEGORY, {"limit": 1000000, "offset": 0}
        )
        members = result.get("members", []) if isinstance(result, dict) else []
        return {
            int(m["telegram_id"])
            for m in members
            if m.get("telegram_id") is not None
        }
    except Exception as e:
        logger.exception("[reminder] échec fetch_prospect_ids")
        _log_error("Échec get_category_members PROSPECT", str(e))
        return set()


# ══════════════════════════════════════════════════════════════════════════════
# COMPTEUR JOURNALIER (lu par le bilan quotidien du main)
# ══════════════════════════════════════════════════════════════════════════════

_daily_reminders_sent = 0


def peek_daily_counter() -> int:
    return _daily_reminders_sent


def get_and_reset_daily_counter() -> int:
    """Renvoie le compteur du jour puis le remet à zéro (appelé à 20h)."""
    global _daily_reminders_sent
    v = _daily_reminders_sent
    _daily_reminders_sent = 0
    return v


# ══════════════════════════════════════════════════════════════════════════════
# BOUCLE PRINCIPALE
# ══════════════════════════════════════════════════════════════════════════════

async def registration_reminder_loop(bot, notify_admin_critical=None):
    """Un passage toutes les 60 secondes.

    notify_admin_critical : callback optionnel `async(bot, title, detail)`
    pour signaler une panne de la boucle elle-même.
    """
    global _daily_reminders_sent
    CHECK_INTERVAL = 60

    while True:
        try:
            await asyncio.sleep(CHECK_INTERVAL)

            try:
                rows = await _get_incomplete_prospects()
            except Exception as e:
                logger.exception("[reminder] échec DB")
                _log_error("Échec lecture users incomplets", str(e))
                continue

            if not rows:
                continue

            eligible_ids = await _fetch_prospect_ids()
            if not eligible_ids:
                continue

            now = datetime.now()
            sent = 0
            purged = 0
            promoted_all = 0
            errors_transient = 0

            for r in rows:
                user_id = r.get("telegram_id")
                if not user_id or int(user_id) not in eligible_ids:
                    continue

                count = int(r.get("reminder_count") or 0)
                created_at = r.get("created_at")
                due = compute_next_due(created_at, count)

                # Plus rien à envoyer : soit déjà purgé, soit déjà promu,
                # soit created_at invalide (voir compute_next_due).
                if due is None:
                    continue
                if now < due:
                    continue

                is_final = is_final_reminder(count)
                text = get_reminder_text(count)

                try:
                    await bot.send_message(
                        chat_id=int(user_id),
                        text=text,
                        parse_mode="HTML",
                        reply_markup=_resume_button,
                    )
                    sent += 1
                    _daily_reminders_sent += 1
                    await _mark_reminder_sent(user_id)
                    await asyncio.sleep(0.05)  # anti-flood

                    # Envoi J+7 réussi → promotion Actif (ALL)
                    if is_final:
                        try:
                            await promote_prospect_to_active_all(user_id)
                            promoted_all += 1
                        except Exception as e:
                            _log_error(
                                "Échec promotion Actif (ALL)",
                                f"user_id={user_id} — {e}",
                            )

                except (Forbidden, BadRequest) as e:
                    is_permanent = (
                        isinstance(e, Forbidden)
                        or "chat not found" in str(e).lower()
                    )

                    if is_permanent and is_final:
                        # Ultime relance + user injoignable → purge complète
                        try:
                            await _purge_unreachable_prospect(user_id)
                            purged += 1
                        except Exception as e2:
                            _log_error(
                                "Échec purge J+7",
                                f"user_id={user_id} — {e2}",
                            )

                    elif is_permanent:
                        # Injoignable pendant le cycle : on consomme la tentative
                        # (sinon on retenterait indéfiniment). Le user restera
                        # en PROSPECT jusqu'à J+7 puis sera purgé.
                        try:
                            await _mark_reminder_sent(user_id)
                        except Exception:
                            pass

                    else:
                        # BadRequest transitoire (rate-limit atypique...) :
                        # on NE consomme PAS, on retentera au prochain cycle.
                        errors_transient += 1
                        logger.warning(
                            f"[reminder] BadRequest transitoire {user_id}: {e}"
                        )
                        _log_error(
                            "BadRequest transitoire (relance)",
                            f"user_id={user_id} — {e}",
                        )

                except Exception as e:
                    # Timeout réseau, etc. → transitoire, on NE consomme PAS.
                    errors_transient += 1
                    logger.warning(f"[reminder] échec envoi {user_id}: {e}")
                    _log_error(
                        "Échec envoi relance", f"user_id={user_id} — {e}"
                    )

            if sent or purged or promoted_all or errors_transient:
                logger.info(
                    f"[reminder] cycle — envoyées={sent} "
                    f"promues_all={promoted_all} purgées={purged} "
                    f"errors_transient={errors_transient}"
                )

        except Exception as e:
            logger.exception("[reminder] erreur inattendue boucle")
            _log_error("Erreur inattendue boucle relance", str(e))
            if notify_admin_critical:
                try:
                    await notify_admin_critical(
                        bot, "Boucle de relance en erreur", str(e)
                    )
                except Exception:
                    pass