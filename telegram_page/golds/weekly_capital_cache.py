"""
weekly_capital_cache.py — Cache mémoire du capital utilisateur, TTL 7 jours (v7.1).

PRINCIPE
Le capital d'un utilisateur est stable sur des périodes longues (semaine).
Le redemander à chaque trade est inutile et alourdit le parcours.

Le cache :
  - vit dans le process (RAM)
  - a une TTL de 7 jours par entrée
  - est adossé à une table SQL user_capital_weekly (source persistante)
  - se recharge intelligemment : miss RAM → lecture SQL → copie RAM

DÉCOUPLAGE
Ce cache est INDÉPENDANT de la session Gold courante. Il survit aux
ouvertures/fermetures de sessions. Le capital appartient à un user,
pas à un trade.

CONCURRENCE
Un lock par-user pour la lecture SQL (évite de faire 300 SELECT
identiques quand 300 users cliquent en même temps sans capital chargé).
Le lock est éphémère — libéré dès que le capital est mis en cache.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from db import get_db

logger = logging.getLogger(__name__)

TTL_SECONDS = 7 * 24 * 3600         # 7 jours
MIN_CAPITAL = 30.0


@dataclass(slots=True)
class CapitalEntry:
    user_id:    int
    capital:    float
    version:    int
    updated_at: float                # epoch
    expire_at:  float                # epoch

    def is_expired(self, now: float | None = None) -> bool:
        return (now or time.time()) >= self.expire_at


class WeeklyCapitalCache:
    """
    Singleton process. Lecture O(1), écriture O(1) + write-behind SQL.
    """

    def __init__(self):
        self._entries: dict[int, CapitalEntry] = {}
        # locks par user_id — évite le "thundering herd" au boot :
        # 500 users qui cliquent en même temps sur le teaser sans que
        # le cache soit peuplé feront UN seul SELECT et se serviront
        # tous du résultat.
        self._load_locks: dict[int, asyncio.Lock] = {}
        # protège la création des locks eux-mêmes
        self._locks_registry_lock = asyncio.Lock()

    # ── Lecture (chemin chaud, 0 SQL si présent) ─────────────────────────

    def get_ram(self, user_id: int) -> float | None:
        """
        Renvoie le capital si présent en RAM ET non expiré. Sinon None.
        Fonction PURE — jamais bloquante, jamais SQL.
        Utilisée dans le chemin chaud du handler.
        """
        e = self._entries.get(user_id)
        if e is None:
            return None
        if e.is_expired():
            # On garde l'entrée en RAM le temps qu'elle soit rafraîchie —
            # ne pas la supprimer ici pour éviter les races.
            return None
        return e.capital

    async def get_or_load(self, user_id: int) -> float | None:
        """
        Cherche en RAM, sinon en SQL. Si trouvé en SQL, promeut en RAM.
        Renvoie None si aucun capital connu → l'appelant devra demander
        à l'utilisateur.
        """
        # Fast path RAM
        cap = self.get_ram(user_id)
        if cap is not None:
            return cap

        # Slow path SQL — protégé par un lock par-user pour éviter que
        # N clicks simultanés du même user provoquent N SELECT identiques.
        lock = await self._get_lock(user_id)
        async with lock:
            # Re-check après acquisition du lock (un autre coroutine a
            # peut-être déjà chargé pendant qu'on attendait).
            cap = self.get_ram(user_id)
            if cap is not None:
                return cap

            row = await self._select(user_id)
            if row is None:
                return None

            # Vérifie expiration SQL — si expiré, on considère comme absent
            # pour forcer une re-saisie via le formulaire.
            now = time.time()
            expire_at = row["expire_at_epoch"]
            if now >= expire_at:
                logger.info(f"[capital] uid={user_id} capital SQL expiré")
                return None

            entry = CapitalEntry(
                user_id=user_id,
                capital=float(row["capital"]),
                version=int(row["version"]),
                updated_at=row["updated_at_epoch"],
                expire_at=expire_at,
            )
            self._entries[user_id] = entry
            return entry.capital

    # ── Écriture (nouveau capital saisi par le user) ─────────────────────

    async def set(self, user_id: int, capital: float) -> CapitalEntry:
        """
        Enregistre un nouveau capital pour un user.
        - Écrit en SQL (source de vérité)
        - Met à jour la RAM
        - Bump la version → tout ancien CalcContext basé sur l'ancien
          capital devient invalide (utile pour audit, pas bloquant)
        """
        if capital < MIN_CAPITAL:
            raise ValueError(f"Capital minimum : {MIN_CAPITAL}$")

        now = time.time()
        # Version incrémentée : on lit la précédente pour +1
        prev = self._entries.get(user_id)
        new_version = (prev.version + 1) if prev else 1

        await self._upsert(user_id, capital, new_version, now, now + TTL_SECONDS)

        entry = CapitalEntry(
            user_id=user_id,
            capital=float(capital),
            version=new_version,
            updated_at=now,
            expire_at=now + TTL_SECONDS,
        )
        self._entries[user_id] = entry
        logger.info(f"[capital] uid={user_id} → {capital}$ (v{new_version})")
        return entry

    def invalidate(self, user_id: int):
        """Force la re-lecture au prochain accès (utilisé par les tests / admin)."""
        self._entries.pop(user_id, None)

    def clear_expired(self) -> int:
        """
        Purge les entrées expirées. À appeler périodiquement (tâche cron
        interne). Renvoie le nombre d'entrées purgées.
        """
        now = time.time()
        to_del = [uid for uid, e in self._entries.items() if e.expire_at <= now]
        for uid in to_del:
            del self._entries[uid]
        if to_del:
            logger.info(f"[capital] {len(to_del)} entrées expirées purgées")
        return len(to_del)

    # ── Préchargement massif (au boot / avant broadcast) ─────────────────

    async def preload(self, user_ids: list[int]) -> int:
        """
        Précharge le capital pour une liste d'user_ids. Une seule requête
        SQL pour toute la liste. Utilisé avant broadcast : les 30 000 users
        auront leur capital en RAM AVANT le premier click.
        Renvoie le nombre d'entrées chargées.
        """
        if not user_ids:
            return 0

        loaded = 0
        CHUNK = 1000
        for i in range(0, len(user_ids), CHUNK):
            chunk = user_ids[i:i + CHUNK]
            ph = ",".join(["%s"] * len(chunk))
            async with get_db() as cur:
                await cur.execute(
                    f"SELECT user_id, capital, version, "
                    f"UNIX_TIMESTAMP(updated_at) AS updated_at_epoch, "
                    f"UNIX_TIMESTAMP(expire_at)  AS expire_at_epoch "
                    f"FROM user_capital_weekly "
                    f"WHERE user_id IN ({ph}) AND expire_at > NOW()",
                    chunk,
                )
                rows = await cur.fetchall()
            for r in rows:
                self._entries[int(r["user_id"])] = CapitalEntry(
                    user_id=int(r["user_id"]),
                    capital=float(r["capital"]),
                    version=int(r["version"]),
                    updated_at=float(r["updated_at_epoch"]),
                    expire_at=float(r["expire_at_epoch"]),
                )
                loaded += 1

        logger.info(f"[capital] preload — {loaded}/{len(user_ids)} capitaux chargés en RAM")
        return loaded

    # ── Introspection ─────────────────────────────────────────────────────

    def status(self) -> dict:
        now = time.time()
        active   = sum(1 for e in self._entries.values() if not e.is_expired(now))
        expired  = len(self._entries) - active
        return {
            "total_ram":     len(self._entries),
            "active":        active,
            "expired_stale": expired,
            "ttl_days":      TTL_SECONDS // 86400,
        }

    def missing_user_ids(self, user_ids: list[int]) -> list[int]:
        """
        Utilitaire pour la campagne : renvoie la liste des users qui
        n'ont PAS de capital valide en RAM.
        """
        now = time.time()
        return [uid for uid in user_ids
                if uid not in self._entries or self._entries[uid].is_expired(now)]

    # ── Internes ──────────────────────────────────────────────────────────

    async def _get_lock(self, user_id: int) -> asyncio.Lock:
        # Petit lock registry protégé pour ne pas créer 2 locks concurrents
        lock = self._load_locks.get(user_id)
        if lock is not None:
            return lock
        async with self._locks_registry_lock:
            lock = self._load_locks.get(user_id)
            if lock is None:
                lock = asyncio.Lock()
                self._load_locks[user_id] = lock
            return lock

    async def _select(self, user_id: int) -> dict | None:
        async with get_db() as cur:
            await cur.execute(
                "SELECT user_id, capital, version, "
                "UNIX_TIMESTAMP(updated_at) AS updated_at_epoch, "
                "UNIX_TIMESTAMP(expire_at)  AS expire_at_epoch "
                "FROM user_capital_weekly WHERE user_id = %s",
                (user_id,),
            )
            row = await cur.fetchone()
        return dict(row) if row else None

    async def _upsert(self, user_id: int, capital: float, version: int,
                       updated_at_epoch: float, expire_at_epoch: float):
        async with get_db() as cur:
            await cur.execute("""
                INSERT INTO user_capital_weekly
                    (user_id, capital, version, updated_at, expire_at)
                VALUES (%s, %s, %s, FROM_UNIXTIME(%s), FROM_UNIXTIME(%s))
                AS new_vals
                ON DUPLICATE KEY UPDATE
                    capital    = new_vals.capital,
                    version    = new_vals.version,
                    updated_at = new_vals.updated_at,
                    expire_at  = new_vals.expire_at
            """, (user_id, capital, version, updated_at_epoch, expire_at_epoch))


# Instance unique pour tout le process
weekly_capital = WeeklyCapitalCache()


# ══════════════════════════════════════════════════════════════════════════════
# Migration SQL — schéma à exécuter UNE fois lors du déploiement v7.1
# ══════════════════════════════════════════════════════════════════════════════

MIGRATION_SQL = """
CREATE TABLE IF NOT EXISTS user_capital_weekly (
    user_id     BIGINT       NOT NULL,
    capital     DECIMAL(15,2) NOT NULL,
    version     INT          NOT NULL DEFAULT 1,
    updated_at  DATETIME     NOT NULL,
    expire_at   DATETIME     NOT NULL,
    PRIMARY KEY (user_id),
    INDEX idx_expire_at (expire_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
"""


async def ensure_schema():
    """À appeler une fois au démarrage."""
    async with get_db() as cur:
        await cur.execute(MIGRATION_SQL)
    logger.info("[capital] schéma user_capital_weekly OK")