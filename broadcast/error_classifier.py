"""
broadcast/error_classifier.py — classification centralisée des exceptions
python-telegram-bot v22.

Chaque erreur d'envoi est traduite en une catégorie stable, indépendamment du
libellé exact renvoyé par l'API. Cela permet aux rapports CSV et aux stats de
rester cohérents même si Telegram change ses messages d'erreur.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TelegramError,
    TimedOut,
)


class ErrorCategory(str, Enum):
    """Catégories exposées dans les rapports CSV et les stats DB."""
    BLOCKED = "blocked"   # user a bloqué le bot / bot expulsé du chat
    DELETED = "deleted"   # compte supprimé / chat introuvable
    FLOOD   = "flood"     # RetryAfter — géré par le rate limiter, pas un vrai échec user
    NETWORK = "network"   # timeout, coupure réseau
    UNKNOWN = "unknown"   # tout le reste


# Fragments de messages BadRequest qui indiquent un utilisateur "supprimé".
# Basé sur la doc Telegram Bot API + observations en production.
_DELETED_MARKERS: Final[tuple[str, ...]] = (
    "chat not found",
    "user is deactivated",
    "peer_id_invalid",
    "user not found",
    "chat_id is empty",
)

# Fragments BadRequest qui signifient en réalité un blocage/kick.
_BLOCKED_MARKERS: Final[tuple[str, ...]] = (
    "bot was blocked",
    "bot was kicked",
    "not enough rights to send",
)


def classify(exc: BaseException) -> ErrorCategory:
    """
    Traduit une exception PTB v22 en catégorie stable.

    L'ordre des tests importe :
      1. RetryAfter en premier (sous-classe de TelegramError)
      2. Forbidden (utilisateur a bloqué)
      3. BadRequest (analyse du message pour distinguer deleted vs blocked)
      4. TimedOut / NetworkError (transitoires)
      5. Le reste : UNKNOWN
    """
    if isinstance(exc, RetryAfter):
        return ErrorCategory.FLOOD

    if isinstance(exc, Forbidden):
        return ErrorCategory.BLOCKED

    if isinstance(exc, BadRequest):
        msg = str(exc).lower()
        if any(m in msg for m in _DELETED_MARKERS):
            return ErrorCategory.DELETED
        if any(m in msg for m in _BLOCKED_MARKERS):
            return ErrorCategory.BLOCKED
        return ErrorCategory.UNKNOWN

    # TimedOut hérite de NetworkError dans PTB v22, on garde les deux
    # pour la clarté.
    if isinstance(exc, (TimedOut, NetworkError)):
        return ErrorCategory.NETWORK

    # TelegramError générique non-reconnu : unknown.
    if isinstance(exc, TelegramError):
        return ErrorCategory.UNKNOWN

    return ErrorCategory.UNKNOWN


def is_permanent_user_error(cat: ErrorCategory) -> bool:
    """Erreur définitive côté utilisateur — candidat pour nettoyage DB."""
    return cat in (ErrorCategory.BLOCKED, ErrorCategory.DELETED)
