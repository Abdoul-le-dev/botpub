
=== ai_bilans ===
CREATE TABLE ai_bilans (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                week_label   TEXT    NOT NULL,
                week_start   TEXT    NOT NULL,
                week_end     TEXT    NOT NULL,
                target       TEXT    DEFAULT 'journalised',
                total_sent   INTEGER DEFAULT 0,
                open_rate    REAL,
                broadcast_id INTEGER,
                generated_at TEXT    DEFAULT (datetime('now'))
            )

=== args ===
CREATE TABLE args (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    id_user INTEGER NOT NULL,
    args TEXT NOT NULL,
    use_it INTEGER NOT NULL CHECK(use_it IN (0, 1))
    )

=== auto_promo_config ===
CREATE TABLE auto_promo_config (
            id                 INTEGER PRIMARY KEY CHECK(id=1),
            anniversary_active INTEGER DEFAULT 0,
            anniversary_pct    REAL    DEFAULT 15,
            winback_active     INTEGER DEFAULT 0,
            winback_pct        REAL    DEFAULT 20,
            upgrade_active     INTEGER DEFAULT 0,
            upgrade_pct        REAL    DEFAULT 30,
            updated_at         TEXT    DEFAULT (datetime('now'))
        )

=== automation_jobs ===
CREATE TABLE automation_jobs (
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
        )

=== automation_logs ===
CREATE TABLE automation_logs (
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
        )

=== broadcast_history ===
CREATE TABLE broadcast_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                tag         TEXT,
                category    TEXT,
                format      TEXT,
                message     TEXT,
                total       INTEGER,
                sent        INTEGER,
                errors      INTEGER,
                started_at  TEXT,
                finished_at TEXT
            )

=== categorie_exercice ===
CREATE TABLE categorie_exercice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nom TEXT NOT NULL,
            admin_verify BOOLEAN DEFAULT 0
        )

=== categories ===
CREATE TABLE categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            name_categorie TEXT NOT NULL,
            created_at TEXT NOT NULL
        )

=== categories_meta ===
CREATE TABLE categories_meta (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie  VARCHAR(255) NOT NULL UNIQUE,
                color           VARCHAR(20)  DEFAULT '#38bdf8',
                description     TEXT,
                created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP
            )

=== category_rules ===
CREATE TABLE category_rules (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name_categorie  VARCHAR(255) NOT NULL,
                trigger_type    VARCHAR(30)  NOT NULL,
                -- 'link' | 'inactivity' | 'survey' | 'subscription' | 'trade_perf' | 'keyword' | 'no_open'
                trigger_value   TEXT,
                is_active       BOOLEAN      DEFAULT TRUE,
                created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (name_categorie)
                    REFERENCES categories_meta(name_categorie)
                    ON DELETE CASCADE
                    ON UPDATE CASCADE
            )

=== conversations ===
    CREATE TABLE conversations (
                    id               INTEGER  PRIMARY KEY AUTOINCREMENT,
                    user_id          INTEGER  NOT NULL UNIQUE,
                    ia_enabled       INTEGER  DEFAULT 1,
                    is_blocked       INTEGER  DEFAULT 0,
                    unread_count     INTEGER  DEFAULT 0,
                    last_message_id  INTEGER  DEFAULT NULL,
                    last_activity    TEXT     DEFAULT NULL,
                    pinned           INTEGER  DEFAULT 0,
                    archived         INTEGER  DEFAULT 0,
                    note_admin       TEXT     DEFAULT NULL,
                    created_at       TEXT     DEFAULT (datetime('now')),
                    updated_at       TEXT     DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
                )

=== exam ===
CREATE TABLE exam (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_name TEXT NOT NULL,
        id_part_one INTEGER NOT NULL,
        id_part_two INTEGER NOT NULL
    )

=== exam_user ===
CREATE TABLE exam_user (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        exam_id TEXT NOT NULL,
        id_user INTEGER NOT NULL,
        email TEXT NOT NULL,
        user_name TEXT NOT NULL,
        last_name TEXT NOT NULL,
        note_one INTEGER DEFAULT 0,
        time_one TEXT DEFAULT NULL,
        note_two INTEGER DEFAULT 0,
        time_two TEXT DEFAULT NULL,
        qr_code TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )

=== exercice ===
CREATE TABLE exercice (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            explanation TEXT,
            categorie_id INTEGER NOT NULL,
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE
        )

=== followup_comments ===
CREATE TABLE followup_comments (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id    INTEGER NOT NULL REFERENCES signals(id),
                type         TEXT    NOT NULL
                                     CHECK(type IN ('update','invalidation','secure','encourage')),
                message      TEXT    NOT NULL,
                screenshot_url TEXT,
                broadcast_id INTEGER,
                sent_at      TEXT    DEFAULT (datetime('now'))
            )

=== form_responses ===
CREATE TABLE form_responses (
                id          INTEGER  PRIMARY KEY AUTOINCREMENT,
                session_id  INTEGER  NOT NULL REFERENCES form_sessions(id),
                form_id     INTEGER  NOT NULL,
                telegram_id INTEGER  NOT NULL,
                field_id    INTEGER  NOT NULL,
                field_type  TEXT     NOT NULL,
                value       TEXT,
                is_correct  INTEGER,
                points      INTEGER  DEFAULT 0,
                answered_at DATETIME DEFAULT (datetime('now')),
                field_label TEXT     DEFAULT '',
                created_at  DATETIME DEFAULT (datetime('now'))
            )

=== form_sessions ===
CREATE TABLE form_sessions (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                form_id       INTEGER NOT NULL REFERENCES forms(id),
                telegram_id   INTEGER NOT NULL,
                step_index    INTEGER NOT NULL DEFAULT 0,   -- index dans fields[]
                status        TEXT    NOT NULL DEFAULT 'in_progress',  -- in_progress | completed | abandoned
                score         INTEGER NOT NULL DEFAULT 0,
                started_at    DATETIME DEFAULT (datetime('now')),
                updated_at    DATETIME DEFAULT (datetime('now')),
                UNIQUE(form_id, telegram_id)               -- une session active par user/formulaire
            )

=== form_submissions ===
CREATE TABLE form_submissions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id      INTEGER NOT NULL UNIQUE REFERENCES form_sessions(id),
                form_id         INTEGER NOT NULL,
                telegram_id     INTEGER NOT NULL,
                score_final     INTEGER DEFAULT 0,
                score_max       INTEGER DEFAULT 0,
                pct             INTEGER DEFAULT 0,
                actions_done    TEXT    DEFAULT '[]',   -- JSON list des actions exécutées
                submitted_at    DATETIME DEFAULT (datetime('now'))
            )

=== forms ===
CREATE TABLE forms (
                id            INTEGER  PRIMARY KEY AUTOINCREMENT,
                name          TEXT     NOT NULL,
                command       TEXT     NOT NULL UNIQUE,
                type          TEXT     NOT NULL,
                trigger_type  TEXT     NOT NULL DEFAULT 'command',
                trigger_value TEXT,
                intro         TEXT     DEFAULT '',
                outro         TEXT     DEFAULT '',
                fields        TEXT     NOT NULL DEFAULT '[]',
                actions       TEXT     NOT NULL DEFAULT '[]',
                conditions    TEXT     NOT NULL DEFAULT '[]',
                quiz_config   TEXT     NOT NULL DEFAULT '{}',
                options       TEXT     NOT NULL DEFAULT '{}',
                actif         INTEGER  NOT NULL DEFAULT 1,
                cree_le       DATETIME DEFAULT (datetime('now')),
                modifie_le    DATETIME DEFAULT (datetime('now')),
                is_active     INTEGER  DEFAULT 1,
                created_at    TEXT
            )

=== growth_subscriptions ===
CREATE TABLE growth_subscriptions (
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
        )

=== ia_functions ===
CREATE TABLE ia_functions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT    NOT NULL,
            description TEXT,
            code        TEXT    NOT NULL DEFAULT '',
            is_active   INTEGER DEFAULT 1,
            created_at  TEXT    DEFAULT (datetime('now'))
        )

=== ia_prompts ===
CREATE TABLE ia_prompts (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            description   TEXT,
            content       TEXT    NOT NULL DEFAULT '',
            return_format TEXT    NOT NULL DEFAULT 'text'
                          CHECK(return_format IN ('text','json','list','markdown')),
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT (datetime('now'))
        )

=== ia_trigger_config ===
CREATE TABLE ia_trigger_config (
            id             INTEGER PRIMARY KEY CHECK(id=1),
            trigger_type   TEXT NOT NULL DEFAULT 'form'
                           CHECK(trigger_type IN ('form','immediate','messages','trade')),
            messages_count INTEGER DEFAULT 5,
            updated_at     TEXT DEFAULT (datetime('now'))
        )

=== invite_link_stats ===
CREATE TABLE invite_link_stats (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            link_id     INTEGER NOT NULL REFERENCES invite_links(id),
            user_id     INTEGER,
            event       TEXT    NOT NULL CHECK(event IN ('click','register','subscribe')),
            occurred_at TEXT    DEFAULT (datetime('now'))
        )

=== invite_links ===
CREATE TABLE invite_links (
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
        , form_id INTEGER REFERENCES forms(id))

=== mail_valide ===
CREATE TABLE mail_valide (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user TEXT UNIQUE NOT NULL,
            psw TEXT NOT NULL,
            nbre_mail_envoyer_jrs INTEGER
        )

=== member_capital ===
CREATE TABLE member_capital (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                capital      REAL    NOT NULL,
                type         TEXT    DEFAULT 'gains'
                                     CHECK(type IN ('gains','withdrawal','loss','initial')),
                declared_at  TEXT    DEFAULT (datetime('now')),
                source       TEXT    DEFAULT 'form'
            )

=== messages ===
CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            message_id INTEGER,
            message_text TEXT NOT NULL,
            answer TEXT,
            created_at TEXT NOT NULL
        , broadcast_id INTEGER DEFAULT NULL, media_url TEXT    DEFAULT NULL, status TEXT    DEFAULT 'received', error_message TEXT    DEFAULT NULL, direction      TEXT    DEFAULT 'inbound', answered_by    TEXT    DEFAULT NULL, replied_to_id  INTEGER DEFAULT NULL, message_type   TEXT    DEFAULT 'text', ia_enabled     INTEGER DEFAULT 0, read_at        TEXT    DEFAULT NULL, delivered_at   TEXT    DEFAULT NULL, requires_admin INTEGER DEFAULT 0, is_testimonial INTEGER DEFAULT 0)

=== participants ===
CREATE TABLE participants (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        mail_envoyer BOOLEAN DEFAULT 0,
        token_utilise BOOLEAN DEFAULT 0
    )

=== participants_2nd ===
CREATE TABLE participants_2nd (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nom TEXT NOT NULL,
        email TEXT NOT NULL,
        token TEXT UNIQUE NOT NULL,
        mail_envoyer BOOLEAN DEFAULT 0,
        token_utilise BOOLEAN DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    )

=== promo_codes ===
CREATE TABLE promo_codes (
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
        )

=== resultat_student_day ===
CREATE TABLE resultat_student_day (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            categorie_id INTEGER NOT NULL,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            note REAL,
            second_time BOOLEAN DEFAULT 0,
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE

        )

=== resultat_student_question ===
CREATE TABLE resultat_student_question (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            id_user INTEGER NOT NULL,
            categorie_id INTEGER NOT NULL,
            question_id INTEGER NOT NULL,
            answer TEXT,
            time_start TEXT NOT NULL,
            time_end TEXT NOT NULL,
            second_time BOOLEAN DEFAULT 0,
            FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id)
                ON DELETE CASCADE,
            FOREIGN KEY (question_id) REFERENCES exercice(id)
                ON DELETE CASCADE
        )

=== signal_participations ===
CREATE TABLE signal_participations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id   INTEGER NOT NULL REFERENCES signals(id),
                user_id     INTEGER NOT NULL,
                response    TEXT    NOT NULL CHECK(response IN ('in','out')),
                responded_at TEXT   DEFAULT (datetime('now')),
                UNIQUE(signal_id, user_id)
            )

=== signals ===
CREATE TABLE signals (
                id               INTEGER  PRIMARY KEY AUTOINCREMENT,
                pair             TEXT     NOT NULL,
                direction        TEXT     NOT NULL CHECK(direction IN ('long','short')),
                timeframe        TEXT     DEFAULT 'H4',
                entry_price      REAL     NOT NULL,
                tp1              REAL,
                tp2              REAL,
                sl               REAL,
                note             TEXT,
                screenshot_url   TEXT,
                category         TEXT     DEFAULT 'clients_actifs',
                status           TEXT     DEFAULT 'open'
                                          CHECK(status IN ('open','closed','cancelled')),
                close_price      REAL,
                close_result     TEXT     CHECK(close_result IN ('tp','sl','partial','cancelled') OR close_result IS NULL),
                close_screenshot TEXT,
                result_pips      REAL,
                result_percent   REAL,
                published_at     TEXT     DEFAULT (datetime('now')),
                closed_at        TEXT,
                lot_suggested    REAL,
                broadcast_id     INTEGER
            )

=== sqlite_sequence ===
CREATE TABLE sqlite_sequence(name,seq)

=== subscription_plans ===
CREATE TABLE subscription_plans (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            price_usd     REAL    NOT NULL,
            duration_days INTEGER NOT NULL DEFAULT 30,
            trial_days    INTEGER DEFAULT 0,
            categories    TEXT    DEFAULT '[]',
            description   TEXT,
            is_active     INTEGER DEFAULT 1,
            created_at    TEXT    DEFAULT (datetime('now'))
        )

=== subscriptions ===
CREATE TABLE subscriptions (
                id            INTEGER  PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER  NOT NULL,
                plan          TEXT     NOT NULL,
                duration_days INTEGER  NOT NULL,
                started_at    TEXT     NOT NULL,
                expires_at    TEXT     NOT NULL,
                status        TEXT     DEFAULT 'active',
                note          TEXT     DEFAULT NULL,
                created_at    TEXT     DEFAULT (datetime('now')),
                updated_at    TEXT     DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )

=== trade_comments ===
CREATE TABLE trade_comments (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id    INTEGER NOT NULL,
                user_id     INTEGER NOT NULL,
                comment     TEXT    NOT NULL,
                created_at  TEXT    DEFAULT (datetime('now')),
                FOREIGN KEY (trade_id) REFERENCES trade_journal(id) ON DELETE CASCADE
            )

=== trade_journal ===
CREATE TABLE trade_journal (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id        INTEGER NOT NULL REFERENCES signals(id),
                user_id          INTEGER NOT NULL,
                participated     INTEGER DEFAULT 1,
                entry_price      REAL,
                exit_price       REAL,
                result_pips      REAL,
                result_percent   REAL,
                gain_usd         REAL,
                lot_used         REAL,
                behavior         TEXT    CHECK(behavior IN ('disciplined','early_exit','sl_skip','passive') OR behavior IS NULL),
                screenshot_url   TEXT,
                capital_before   REAL,
                capital_after    REAL,
                submitted_at     TEXT    DEFAULT (datetime('now')),
                status           TEXT    DEFAULT 'closed'
                                         CHECK(status IN ('open','closed')),
                UNIQUE(signal_id, user_id)
            )

=== trading_pairs ===
CREATE TABLE trading_pairs (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol         TEXT    NOT NULL UNIQUE,
                category       TEXT    DEFAULT 'forex'
                                       CHECK(category IN ('forex','crypto','indices','commodities')),
                pip_value      REAL    NOT NULL DEFAULT 10.0,
                decimals       INTEGER DEFAULT 5,
                binance_symbol TEXT,
                is_active      INTEGER DEFAULT 1,
                note           TEXT,
                created_at     TEXT    DEFAULT (datetime('now')),
                updated_at     TEXT    DEFAULT (datetime('now'))
            )

=== users ===
CREATE TABLE users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT NOT NULL,
            country TEXT,
            created_at TEXT NOT NULL
        , telegram_id INTEGER, contexte_user TEXT, email TEXT, motivation TEXT, level TEXT, why TEXT, what TEXT, expectations TEXT, discover TEXT)

=== usersdefault ===
CREATE TABLE usersdefault (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            created_at TEXT NOT NULL
        )

=== videos ===
CREATE TABLE videos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            video_name TEXT NOT NULL,
            file_id TEXT,
            created_at TEXT NOT NULL
        )