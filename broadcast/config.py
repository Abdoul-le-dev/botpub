"""
broadcast/config.py — constantes et paramètres du moteur.

Toutes les valeurs sensibles (ADMIN_IDS, chemins) sont pilotables via variables
d'environnement. Les valeurs par défaut correspondent au setup actuel du projet
pour garder la compatibilité.
"""

from __future__ import annotations

import os
from pathlib import Path


# ── Admins ────────────────────────────────────────────────────────────────────
# Format env : BROADCAST_ADMIN_IDS="6992809421,571718066"
# Fallback : l'ADMIN_ID historiquement présent dans broadcast_engine.py v1.
def _parse_admin_ids() -> list[int]:
    raw = os.getenv("BROADCAST_ADMIN_IDS", "").strip()
    if not raw:
        return [571718066]
    out: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.append(int(part))
    return out or [571718066]


ADMIN_IDS: list[int] = _parse_admin_ids()
# Compat pour code externe qui importait broadcast_engine.ADMIN_ID :
ADMIN_ID: int = ADMIN_IDS[0]


# ── Rate limiter ──────────────────────────────────────────────────────────────
# Cible finale : 28–30 msg/s. On démarre au milieu, on plafonne à 30.
RATE_TARGET: float      = float(os.getenv("BROADCAST_RATE_TARGET", "29.0"))
RATE_MAX: float         = float(os.getenv("BROADCAST_RATE_MAX", "30.0"))
RATE_MIN: float         = float(os.getenv("BROADCAST_RATE_MIN", "25.0"))
# Après combien d'envois consécutifs OK on remonte d'un cran (0.5 msg/s)
RATE_RECOVERY_STEP_SUCCESSES: int = int(os.getenv("BROADCAST_RATE_RECOVERY_STEP", "100"))
RATE_RECOVERY_INCREMENT: float    = float(os.getenv("BROADCAST_RATE_RECOVERY_INC", "0.5"))


# ── Workers ───────────────────────────────────────────────────────────────────
# Nombre de coroutines qui consomment la queue en parallèle. Le vrai débit est
# borné par le rate limiter global : ces workers passent l'essentiel de leur
# temps à attendre le token. 32 suffit largement pour saturer 30 msg/s.
NUM_WORKERS: int  = int(os.getenv("BROADCAST_WORKERS", "1000"))
QUEUE_MAXSIZE: int = int(os.getenv("BROADCAST_QUEUE_MAXSIZE", "2000"))


# ── Personnalisation ──────────────────────────────────────────────────────────
PLACEHOLDER_PRENOM: str  = "+prenom"
PRENOM_FALLBACK: str     = "l'ami"
PRENOM_MIN_LEN: int      = 1
PRENOM_MAX_LEN: int      = 15
# Taille des chunks pour le fetch groupé des prénoms (limite pratique MySQL).
PRENOM_BATCH_SIZE: int   = 5000


# ── Limites Telegram ──────────────────────────────────────────────────────────
TG_MAX_MESSAGE_LEN: int = 4096
TG_MAX_CAPTION_LEN: int = 1024
# Marge de sécurité sous 1024 pour absorber les caractères multi-octets emoji.
TG_CAPTION_SAFE_LEN: int = 1000


# ── Rapports CSV ──────────────────────────────────────────────────────────────
REPORTS_DIR: Path = Path(os.getenv("BROADCAST_REPORTS_DIR", "/tmp/broadcast_reports"))
REPORTS_DIR.mkdir(parents=True, exist_ok=True)


# ── Progression admin ────────────────────────────────────────────────────────
# Le brief impose : start, 50%, end. On garde ces valeurs pilotables au cas où.
PROGRESS_NOTIFY_PERCENTS: tuple[int, ...] = (50,)


# ── Callback data prefixes ────────────────────────────────────────────────────
# Préfixes réservés pour les CallbackQuery de nettoyage post-broadcast.
CB_CLEANUP_DELETE: str = "bcclean:del:"
CB_CLEANUP_IGNORE: str = "bcclean:ign:"


# ── Cleanup pending TTL ───────────────────────────────────────────────────────
# Durée pendant laquelle un token de cleanup admin reste valide.
CLEANUP_TOKEN_TTL_SECONDS: int = int(os.getenv("BROADCAST_CLEANUP_TTL", str(24 * 3600)))
