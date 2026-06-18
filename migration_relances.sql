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

CREATE TABLE IF NOT EXISTS relance (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    name_categorie  VARCHAR(100) NOT NULL UNIQUE,
    message         TEXT         NOT NULL,
    is_active       TINYINT(1)   NOT NULL DEFAULT 1,
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP
                                 ON UPDATE CURRENT_TIMESTAMP,

    -- Pas de FK stricte vers categories_meta : une relance peut être
    -- préconfigurée avant que la catégorie n'ait encore de membres,
    -- et categories_meta peut évoluer indépendamment.
    INDEX idx_relance_categorie (name_categorie)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


CREATE TABLE IF NOT EXISTS relance_schedule (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    relance_id  INT      NOT NULL,
    heure_envoi TIME     NOT NULL,          -- heure locale, GMT+1 (Europe/Paris)
    is_active   TINYINT(1) NOT NULL DEFAULT 1,
    created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (relance_id) REFERENCES relance(id) ON DELETE CASCADE,
    INDEX idx_schedule_relance (relance_id),
    INDEX idx_schedule_heure (heure_envoi)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;


-- ── Seed initial : une ligne par catégorie existante, un créneau à 08:00 ──
-- Idempotent (INSERT IGNORE sur la contrainte UNIQUE name_categorie).

INSERT IGNORE INTO relance (name_categorie, message, is_active) VALUES
    ('clients_actifs',  'Salut +prenom 👋 Ton abonnement FDK VIP est actif, profite bien des signaux du jour !', 0),
    ('clients_j7',      "Salut +prenom, ton abonnement FDK VIP expire dans +jours_restants jours. Pense à le renouveler pour ne pas perdre l'accès aux signaux 📈", 1),
    ('clients_j3',      'Hey +prenom ⚠️ Il ne reste que +jours_restants jours avant la fin de ton abonnement. Renouvelle maintenant pour garder ton accès.', 1),
    ('clients_j1',      "+prenom, ton abonnement expire demain. Dernière chance de renouveler avant la coupure d'accès aux signaux.", 1),
    ('clients_expires', "Salut +prenom, ton abonnement FDK VIP est arrivé à expiration. Reviens quand tu veux, tes signaux t'attendent 👀", 1);

-- Créneau par défaut (08:00) pour chacune des 5 relances ci-dessus.
INSERT INTO relance_schedule (relance_id, heure_envoi, is_active)
SELECT r.id, '08:00:00', 1
FROM relance r
WHERE NOT EXISTS (
    SELECT 1 FROM relance_schedule rs WHERE rs.relance_id = r.id
);