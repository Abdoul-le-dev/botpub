"""
telegram_page/automatisation/growth_tables.py
Initialisation idempotente des tables Growth Hub.
NE PAS recréer la table 'subscriptions' déjà existante.
"""

import sqlite3

DB = "preinscriptions.db"


def init_growth_tables():
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        -- Liens d'invitation trackés
        CREATE TABLE IF NOT EXISTS invite_links (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            start_param   TEXT    NOT NULL UNIQUE,
            auto_category TEXT,
            promo_code    TEXT,
            quota_max     INTEGER,
            quota_used    INTEGER DEFAULT 0,
            expires_at    TEXT,
            source        TEXT    DEFAULT 'direct',
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        -- Stats des clics / inscriptions par lien
        CREATE TABLE IF NOT EXISTS invite_link_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id     INTEGER NOT NULL REFERENCES invite_links(id),
            user_id     INTEGER,
            event       TEXT    NOT NULL CHECK(event IN ('click','register','subscribe')),
            occurred_at TEXT    DEFAULT (datetime('now'))
        );

        -- Config déclencheur IA (singleton)
        CREATE TABLE IF NOT EXISTS ia_trigger_config (
            id             INTEGER PRIMARY KEY CHECK(id=1),
            trigger_type   TEXT NOT NULL DEFAULT 'form'
                           CHECK(trigger_type IN ('form','immediate','messages','trade')),
            messages_count INTEGER DEFAULT 5,
            updated_at     TEXT DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO ia_trigger_config (id, trigger_type) VALUES (1, 'form');

        -- Jobs d'automation
        CREATE TABLE IF NOT EXISTS automation_jobs (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            name           TEXT    NOT NULL,
            trig_type      TEXT    NOT NULL CHECK(trig_type IN ('time','cond','event')),
            freq           TEXT,
            run_time       TEXT,
            cond_field     TEXT,
            cond_value     TEXT,
            cond_extra     TEXT,
            event_type     TEXT,
            target         TEXT    NOT NULL DEFAULT 'all',
            action_type    TEXT    NOT NULL,
            action_content TEXT,
            is_active      INTEGER DEFAULT 1,
            last_run_at    TEXT,
            next_run_at    TEXT,
            exec_count     INTEGER DEFAULT 0,
            err_count      INTEGER DEFAULT 0,
            created_at     TEXT    DEFAULT (datetime('now'))
        );

        -- Log des exécutions de jobs
        CREATE TABLE IF NOT EXISTS automation_logs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id      INTEGER NOT NULL REFERENCES automation_jobs(id),
            started_at  TEXT    DEFAULT (datetime('now')),
            finished_at TEXT,
            total       INTEGER DEFAULT 0,
            sent        INTEGER DEFAULT 0,
            errors      INTEGER DEFAULT 0,
            status      TEXT    DEFAULT 'running'
                        CHECK(status IN ('running','success','partial','failed')),
            notes       TEXT
        );

        -- Plans d'abonnement Growth Hub
        CREATE TABLE IF NOT EXISTS subscription_plans (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            price_usd     REAL    NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 30,
            trial_days    INTEGER DEFAULT 0,
            categories    TEXT    DEFAULT '[]',
            description   TEXT,
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        -- Abonnements Growth Hub (DISTINCT de 'subscriptions' existante)
        CREATE TABLE IF NOT EXISTS growth_subscriptions (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            member_name  TEXT,
            plan_id      INTEGER NOT NULL REFERENCES subscription_plans(id),
            status       TEXT    NOT NULL DEFAULT 'active'
                         CHECK(status IN ('active','trial','expiring','expired','cancelled')),
            price_paid   REAL    DEFAULT 0,
            promo_code   TEXT,
            started_at   TEXT    DEFAULT (datetime('now')),
            expires_at   TEXT,
            cancelled_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_gsub_user   ON growth_subscriptions(user_id);
        CREATE INDEX IF NOT EXISTS idx_gsub_status ON growth_subscriptions(status, expires_at);

        -- Codes promotionnels
        CREATE TABLE IF NOT EXISTS promo_codes (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            code            TEXT    NOT NULL UNIQUE,
            discount_type   TEXT    NOT NULL CHECK(discount_type IN ('percent','fixed')),
            discount_value  REAL    NOT NULL,
            plan_id         INTEGER REFERENCES subscription_plans(id),
            quota_max       INTEGER,
            current_uses    INTEGER DEFAULT 0,
            first_time_only INTEGER DEFAULT 1,
            is_active       INTEGER DEFAULT 1,
            expires_at      TEXT,
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        -- Config offres automatiques (singleton)
        CREATE TABLE IF NOT EXISTS auto_promo_config (
            id                 INTEGER PRIMARY KEY CHECK(id=1),
            anniversary_active INTEGER DEFAULT 0,
            anniversary_pct    REAL    DEFAULT 15,
            winback_active     INTEGER DEFAULT 0,
            winback_pct        REAL    DEFAULT 20,
            upgrade_active     INTEGER DEFAULT 0,
            upgrade_pct        REAL    DEFAULT 30,
            updated_at         TEXT    DEFAULT (datetime('now'))
        );
        INSERT OR IGNORE INTO auto_promo_config (id) VALUES (1);

        -- Scores d'engagement membres
        CREATE TABLE IF NOT EXISTS engagement_scores (
            user_id    INTEGER PRIMARY KEY,
            score      INTEGER DEFAULT 0,
            updated_at TEXT    DEFAULT (datetime('now'))
        );

        -- Segments dynamiques
        CREATE TABLE IF NOT EXISTS segments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            tag           TEXT    NOT NULL,
            conditions    TEXT    NOT NULL DEFAULT '[]',
            auto_action   TEXT,
            member_count  INTEGER DEFAULT 0,
            last_computed TEXT,
            created_at    TEXT    DEFAULT (datetime('now'))
        );

        -- Membres de chaque segment
        CREATE TABLE IF NOT EXISTS segment_members (
            segment_id INTEGER NOT NULL REFERENCES segments(id),
            user_id    INTEGER NOT NULL,
            added_at   TEXT    DEFAULT (datetime('now')),
            PRIMARY KEY(segment_id, user_id)
        );

        -- Prospects du pipeline CRM
        CREATE TABLE IF NOT EXISTS crm_prospects (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER,
            name       TEXT    NOT NULL,
            source     TEXT    DEFAULT 'direct',
            link_id    INTEGER REFERENCES invite_links(id),
            col        TEXT    DEFAULT 'nouveau'
                       CHECK(col IN ('nouveau','engage','offre','abonne','vip')),
            score      INTEGER DEFAULT 0,
            created_at TEXT    DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    print("[growth_tables] Tables Growth Hub initialisées.")