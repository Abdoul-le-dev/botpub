-- ═══════════════════════════════════════════════════════════════════════════
-- Migration broadcast v2 — additive, non-destructive
-- À exécuter une seule fois. Toutes les tables sont créées avec IF NOT EXISTS,
-- donc la migration est idempotente.
--
-- Ne touche PAS aux tables existantes (users, categories, broadcast_history).
-- ═══════════════════════════════════════════════════════════════════════════

-- Stats détaillées par diffusion (en plus de broadcast_history conservé pour compat).
CREATE TABLE IF NOT EXISTS `broadcast_stats` (
    `id`                     INT NOT NULL AUTO_INCREMENT,
    `tag`                    VARCHAR(255)  DEFAULT NULL,
    `category`               VARCHAR(255)  DEFAULT NULL,
    `format`                 VARCHAR(32)   DEFAULT NULL,
    `started_at`             DATETIME      DEFAULT NULL,
    `finished_at`            DATETIME      DEFAULT NULL,
    `duration_seconds`       INT           DEFAULT NULL,
    `total`                  INT           DEFAULT 0,
    `sent`                   INT           DEFAULT 0,
    `errors`                 INT           DEFAULT 0,
    `blocked`                INT           DEFAULT 0,
    `deleted`                INT           DEFAULT 0,
    `network_errors`         INT           DEFAULT 0,
    `flood_errors`           INT           DEFAULT 0,
    `unknown_errors`         INT           DEFAULT 0,
    `success_rate`           DECIMAL(5,2)  DEFAULT NULL,
    `average_msg_per_second` DECIMAL(6,2)  DEFAULT NULL,
    `max_msg_per_second`     DECIMAL(6,2)  DEFAULT NULL,
    `min_msg_per_second`     DECIMAL(6,2)  DEFAULT NULL,
    PRIMARY KEY (`id`),
    KEY `idx_started_at` (`started_at`),
    KEY `idx_tag` (`tag`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

-- Cache persistant des file_id Telegram : évite de re-uploader entre broadcasts.
-- Clé = (chemin local, format) car un même fichier peut être envoyé en photo
-- ou en document et Telegram retourne un file_id différent pour chaque format.
CREATE TABLE IF NOT EXISTS `broadcast_media_cache` (
    `local_path`       VARCHAR(500) NOT NULL,
    `format`           VARCHAR(32)  NOT NULL,
    `telegram_file_id` VARCHAR(255) NOT NULL,
    `created_at`       DATETIME     NOT NULL,
    `last_used_at`     DATETIME     NOT NULL,
    PRIMARY KEY (`local_path`, `format`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
