"""
gold_cache.py — Cache mémoire du flux Gold (v6).

Objectif : ZÉRO lecture SQL dans le chemin chaud (clic disclaimer, clic
teaser, saisie capital, confirmation). Tout est chargé UNE FOIS au moment
du broadcast (ou au démarrage du bot), puis lu en RAM. MySQL ne sert plus
qu'à la persistance.

Contenu du cache :
  - session Gold active            (gold_trade_sessions)
  - règles TP + tous les messages  (gold_tp_rules)
  - prénoms des destinataires      (users)
  - dernier capital déclaré        (gold_member_entries)

Invalidation :
  - reload() est appelé : au démarrage, à chaque broadcast, et par les
    routes admin qui créent/modifient une session ou une règle TP.
  - set_phase() met à jour la phase en RAM ; l'UPDATE SQL correspondant
    est poussé dans le buffer par l'appelant (gold_buffer).

Usage :
    from telegram_page.gold.gold_cache import signal_cache
    await signal_cache.reload()               # au démarrage / broadcast
    session = signal_cache.get_session()      # 0 SQL, < 1 µs
"""

import asyncio
import logging
import time

from db import get_db

logger = logging.getLogger(__name__)

_CHUNK = 1000  # taille des IN (...) pour les préchargements massifs


class SignalCache:

    def __init__(self):
        self.session: dict | None = None
        self.rules: dict[int, dict] = {}            # tp_level -> règle complète
        self._capital_ranges: list[tuple] = []      # (min, max|None, tp_level, risk_pct)
        self.prenoms: dict[int, str] = {}           # user_id -> prénom
        self.last_capitals: dict[int, float] = {}   # user_id -> dernier capital
        self.loaded_at: float = 0.0
        self._lock = asyncio.Lock()

    # ── Chargement ────────────────────────────────────────────────────────

    async def reload(self, session_id: int | None = None):
        """Recharge session + règles TP. 2 requêtes SQL, point final."""
        async with self._lock:
            async with get_db() as cur:
                if session_id:
                    await cur.execute(
                        "SELECT * FROM gold_trade_sessions WHERE id = %s",
                        (session_id,)
                    )
                else:
                    await cur.execute("""
                        SELECT * FROM gold_trade_sessions
                        WHERE current_phase IN ('teaser', 'open')
                        ORDER BY created_at DESC LIMIT 1
                    """)
                row = await cur.fetchone()
                self.session = dict(row) if row else None

                await cur.execute(
                    "SELECT * FROM gold_tp_rules WHERE is_active = 1"
                )
                rules = [dict(r) for r in await cur.fetchall()]

            self.rules = {int(r["tp_level"]): r for r in rules}
            self._capital_ranges = sorted(
                (
                    float(r["min_capital"]),
                    float(r["max_capital"]) if r["max_capital"] is not None else None,
                    int(r["tp_level"]),
                    float(r["risk_pct"]),
                )
                for r in rules
            )
            self.loaded_at = time.time()
            logger.info(
                f"[gold_cache] reload OK — session="
                f"{self.session['id'] if self.session else None}, "
                f"{len(self.rules)} règles TP"
            )

    async def preload_users(self, user_ids: list[int]):
        """
        Précharge prénoms + dernier capital pour TOUS les destinataires
        d'un broadcast. ~2 requêtes par tranche de 1000 users, exécutées
        UNE fois AVANT le pic — au lieu de 2 SELECT par utilisateur
        pendant le pic.
        """
        self.prenoms.clear()
        self.last_capitals.clear()
        if not user_ids:
            return

        async with get_db() as cur:
            for i in range(0, len(user_ids), _CHUNK):
                chunk = user_ids[i:i + _CHUNK]
                ph = ",".join(["%s"] * len(chunk))

                await cur.execute(
                    f"SELECT telegram_id, name FROM users "
                    f"WHERE telegram_id IN ({ph})", chunk
                )
                for r in await cur.fetchall():
                    name = (r["name"] or "").strip()
                    if name:
                        p = name.split()[0]
                        if 1 <= len(p) <= 20:
                            self.prenoms[int(r["telegram_id"])] = p

                # Trié par confirmed_at ASC → le dernier écrase les précédents
                await cur.execute(
                    f"SELECT user_id, capital_declared FROM gold_member_entries "
                    f"WHERE user_id IN ({ph}) ORDER BY confirmed_at ASC", chunk
                )
                for r in await cur.fetchall():
                    if r["capital_declared"] is not None:
                        self.last_capitals[int(r["user_id"])] = float(r["capital_declared"])

        logger.info(
            f"[gold_cache] preload_users OK — {len(self.prenoms)} prénoms, "
            f"{len(self.last_capitals)} capitaux"
        )

    # ── Auto-refresh ──────────────────────────────────────────────────────

    def start_auto_refresh(self, interval: int = 30):
        """
        Recharge session + règles toutes les `interval` secondes (2 requêtes
        légères). Indispensable si l'API FastAPI tourne dans un AUTRE process
        que le bot : les sessions créées/fermées via l'API deviennent
        visibles côté bot en ≤ interval secondes.
        """
        async def _loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await self.reload()   # sans arg → dernière session ouverte
                except Exception as e:
                    logger.warning(f"[gold_cache] auto-refresh échoué: {e}")

        asyncio.create_task(_loop())
        logger.info(f"[gold_cache] auto-refresh démarré ({interval}s)")

    # ── Lectures (0 SQL) ──────────────────────────────────────────────────

    def get_session(self) -> dict | None:
        return self.session

    def is_open(self) -> bool:
        return bool(self.session) and self.session["current_phase"] in ("teaser", "open")

    def set_phase(self, phase: str):
        """Mise à jour RAM seulement — l'UPDATE SQL passe par le buffer."""
        if self.session:
            self.session["current_phase"] = phase

    def tp_level_for_capital(self, capital: float) -> tuple[int, float]:
        """Remplace get_tp_level_for_capital() — plus aucun SELECT par confirmation."""
        for mn, mx, level, risk in self._capital_ranges:
            if mn <= capital and (mx is None or capital <= mx):
                return level, risk
        if capital < 500:
            return 1, 1.0
        elif capital < 2000:
            return 2, 1.5
        return 3, 2.0

    def rule_messages(self, tp_level: int) -> dict:
        """Remplace get_rule_messages() — plus aucun SELECT par notification."""
        return self.rules.get(int(tp_level), {})

    def prenom(self, user_id: int) -> str:
        return self.prenoms.get(user_id, "")

    def last_capital(self, user_id: int) -> float | None:
        return self.last_capitals.get(user_id)


# Instance unique pour tout le process
signal_cache = SignalCache()