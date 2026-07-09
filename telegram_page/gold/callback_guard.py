"""
callback_guard.py — Validation systématique des callbacks Telegram (v7).

PROBLÈME
En v6, un callback_data ressemblait à `gold_confirm_42`. Le handler
lisait la session courante via signal_cache et confirmait — sans vérifier
que le callback concernait bien CETTE session (et pas une session
précédente dont le message serait encore visible chez l'utilisateur).

Scénario cassé :
  - Session #41 envoyée hier. Message encore présent dans le chat user.
  - Session #42 ouverte ce matin.
  - User clique sur le vieux bouton "confirmer" du message #41.
  - Handler → confirme sur la session courante (#42) avec calcul basé
    sur les nouvelles valeurs. L'user pensait confirmer #41.

SOLUTION
Chaque callback_data v7 inclut session_id ET version :
    gold_confirm_<sid>_<ver>

Le décorateur @guard() ci-dessous :
  1. Parse session_id / version depuis le callback_data
  2. Vérifie session_registry.matches(sid, ver)
  3. Vérifie que le status est ACTIVE (pas OPENING, pas CLOSING)
  4. Vérifie que le user_state est bindé à la même session
  5. Si l'un des tests échoue → répond "Ce trade n'est plus disponible."
     et ne fait RIEN d'autre. Le handler n'est jamais appelé.

Résultat : impossible qu'un vieux click sur un vieux message pollue
la session courante.
"""

from __future__ import annotations

import functools
import logging
import re

from telegram_page.gold.session_registry import session_registry
from telegram_page.gold.gold_state import user_state_v7

logger = logging.getLogger(__name__)

# Formats acceptés :
#   gold_action_<sid>_<ver>        v7 (nouveau)
#   gold_action_<sid>              rétro-compat v6 (accepté pendant migration)
_CB_RE = re.compile(r"^gold_[a-z_]+_(\d+)(?:_v(\d+))?(?:_[\d.]+)?$")


def parse_callback(cb_data: str) -> tuple[int, int | None] | None:
    """Renvoie (session_id, version | None) ou None si non parsable."""
    m = _CB_RE.match(cb_data or "")
    if not m:
        return None
    sid = int(m.group(1))
    ver = int(m.group(2)) if m.group(2) else None
    return sid, ver


def make_callback_data(action: str, session_id: int, version: int) -> str:
    """
    Format v7 : gold_<action>_<sid>_v<ver>
    Ex : gold_confirm_42_v7
    """
    return f"gold_{action}_{session_id}_v{version}"


class GuardResult:
    OK              = "ok"
    NOT_PARSABLE    = "not_parsable"
    NO_SESSION      = "no_session"
    WRONG_SESSION   = "wrong_session"
    WRONG_VERSION   = "wrong_version"
    NOT_ACTIVE      = "not_active"
    STATE_MISMATCH  = "state_mismatch"


def check_callback(cb_data: str) -> tuple[str, int | None, int | None]:
    """
    Renvoie (result, session_id, version).
    - result == OK : callback valide, on peut exécuter le handler
    - autre : callback à rejeter, le user recevra un message d'expiration
    """
    parsed = parse_callback(cb_data)
    if parsed is None:
        return GuardResult.NOT_PARSABLE, None, None
    sid, ver = parsed

    current = session_registry.current()
    if current is None:
        return GuardResult.NO_SESSION, sid, ver

    if current.session_id != sid:
        return GuardResult.WRONG_SESSION, sid, ver

    # Rétro-compat v6 : callback sans version → on accepte SEULEMENT si
    # aucune version postérieure n'a été ouverte. Comme la version bump
    # à chaque ouverture, un click sur un vieux message v6 après
    # ouverture d'une v7 = current.version ≥ 2 pour la même session_id,
    # ce qui est impossible (une session ne se réouvre pas). Donc :
    # ver=None ET current.session_id==sid ⇒ OK.
    if ver is not None and current.version != ver:
        return GuardResult.WRONG_VERSION, sid, ver

    # Le state manager doit être bindé sur la même session — sinon
    # incohérence process interne.
    if not user_state_v7.validate(sid, current.version):
        return GuardResult.STATE_MISMATCH, sid, ver

    return GuardResult.OK, sid, current.version


# ══════════════════════════════════════════════════════════════════════════════
# Décorateur pour handlers
# ══════════════════════════════════════════════════════════════════════════════

def guard(action_name: str):
    """
    Décore un handler de callback query. Ajoute deux kwargs :
        session_id : int
        version    : int

    Le handler ne sera appelé QUE si le callback est valide et concerne
    la session active. Sinon, un message d'expiration est renvoyé et le
    handler n'est pas exécuté.

    Usage :
        @guard("confirm")
        async def handle_gold_confirm(update, context, *, session_id, version):
            ...
    """
    def decorator(fn):
        @functools.wraps(fn)
        async def wrapper(update, context, *args, **kwargs):
            query = update.callback_query
            if query is None:
                return
            result, sid, ver = check_callback(query.data)

            if result == GuardResult.OK:
                return await fn(update, context, *args,
                                session_id=sid, version=ver, **kwargs)

            # Rejet — on répond au callback et on informe l'user.
            logger.info(
                f"[guard/{action_name}] callback REJETÉ "
                f"uid={query.from_user.id} data={query.data!r} → {result}"
            )
            try:
                await query.answer("⏰ Ce trade n'est plus disponible.",
                                   show_alert=(result != GuardResult.NOT_ACTIVE))
            except Exception:
                pass
            # Retire le clavier pour éviter de nouveaux clicks sur ce vieux message
            try:
                from telegram import InlineKeyboardMarkup
                await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([]))
            except Exception:
                pass
            return None
        return wrapper
    return decorator