-- ════════════════════════════════════════════════════════════════════════
-- migration_relances.sql
-- Tables de configuration pour le système de relances automatiques.
--
-- Design : la config (message, actif) et les créneaux d'envoi (heure,
-- jour) sont séparés en deux tables. Pour l'instant chaque relance n'a
-- qu'un seul créneau, mais cette séparation permet d'ajouter plusieurs
-- créneaux par catégorie plus tard sans migration de schéma.
--
-- broadcast_history (existante) reste le journal d'EXÉCUTION des envois
-- (sent/errors/dates) — ces tables ne le dupliquent pas, elles ne
-- contiennent que la CONFIGURATION éditable depuis le dashboard.
-- ════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS formation_validation (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    email           VARCHAR(255) NOT NULL,
    is_active       TINYINT(1) NOT NULL DEFAULT 1,
    created_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

