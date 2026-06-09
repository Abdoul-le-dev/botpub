-- ============================================================
--  SCHÉMA COMPLET  –  Migration SQLite → MySQL
--  Source : preincristion.db
--  Généré automatiquement
-- ============================================================

SET FOREIGN_KEY_CHECKS = 0;
SET NAMES utf8mb4;

-- ------------------------------------------------------------
-- 1. users
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS users (
    id           INT            NOT NULL AUTO_INCREMENT,
    name         VARCHAR(255)   NOT NULL,
    phone        VARCHAR(50)    NOT NULL,
    country      VARCHAR(100),
    created_at   DATETIME       NOT NULL,
    telegram_id  BIGINT         UNIQUE,
    contexte_user TEXT,
    email        VARCHAR(255),
    motivation   TEXT,
    level        VARCHAR(100),
    why          TEXT,
    what         TEXT,
    expectations TEXT,
    discover     TEXT,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 2. messages
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS messages (
    id              INT          NOT NULL AUTO_INCREMENT,
    user_id         INT          NOT NULL,
    message_id      INT,
    message_text    TEXT         NOT NULL,
    answer          TEXT,
    created_at      DATETIME     NOT NULL,
    broadcast_id    INT          DEFAULT NULL,
    media_url       TEXT         DEFAULT NULL,
    status          VARCHAR(30)  DEFAULT 'received',
    error_message   TEXT         DEFAULT NULL,
    direction       VARCHAR(20)  DEFAULT 'inbound',
    answered_by     VARCHAR(100) DEFAULT NULL,
    replied_to_id   INT          DEFAULT NULL,
    message_type    VARCHAR(30)  DEFAULT 'text',
    ia_enabled      TINYINT(1)   DEFAULT 0,
    read_at         DATETIME     DEFAULT NULL,
    delivered_at    DATETIME     DEFAULT NULL,
    requires_admin  TINYINT(1)   DEFAULT 0,
    is_testimonial  TINYINT(1)   DEFAULT 0,
    PRIMARY KEY (id),
    INDEX idx_msg_user    (user_id),
    INDEX idx_msg_created (created_at DESC),
    INDEX idx_msg_type    (message_type),
    INDEX idx_msg_bcast   (broadcast_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 3. categories
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories (
    id             INT          NOT NULL AUTO_INCREMENT,
    id_user        INT          NOT NULL,
    name_categorie VARCHAR(255) NOT NULL,
    created_at     DATETIME     NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 4. usersdefault
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS usersdefault (
    id         INT          NOT NULL AUTO_INCREMENT,
    user_id    VARCHAR(100) NOT NULL,
    created_at DATETIME     NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 5. videos
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS videos (
    id         INT          NOT NULL AUTO_INCREMENT,
    video_name VARCHAR(255) NOT NULL,
    file_id    TEXT,
    created_at DATETIME     NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 6. categorie_exercice
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categorie_exercice (
    id           INT         NOT NULL AUTO_INCREMENT,
    nom          VARCHAR(255) NOT NULL,
    admin_verify TINYINT(1)  DEFAULT 0,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 7. exercice
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exercice (
    id           INT  NOT NULL AUTO_INCREMENT,
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL,
    explanation  TEXT,
    categorie_id INT  NOT NULL,
    PRIMARY KEY (id),
    CONSTRAINT fk_exercice_cat FOREIGN KEY (categorie_id)
        REFERENCES categorie_exercice(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 8. resultat_student_question
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resultat_student_question (
    id           INT         NOT NULL AUTO_INCREMENT,
    id_user      INT         NOT NULL,
    categorie_id INT         NOT NULL,
    question_id  INT         NOT NULL,
    answer       TEXT,
    time_start   DATETIME    NOT NULL,
    time_end     DATETIME    NOT NULL,
    second_time  TINYINT(1)  DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT fk_rsq_cat  FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id) ON DELETE CASCADE,
    CONSTRAINT fk_rsq_exo  FOREIGN KEY (question_id)  REFERENCES exercice(id)           ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 9. resultat_student_day
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS resultat_student_day (
    id           INT        NOT NULL AUTO_INCREMENT,
    id_user      INT        NOT NULL,
    categorie_id INT        NOT NULL,
    time_start   DATETIME   NOT NULL,
    time_end     DATETIME   NOT NULL,
    note         FLOAT,
    second_time  TINYINT(1) DEFAULT 0,
    PRIMARY KEY (id),
    CONSTRAINT fk_rsd_cat FOREIGN KEY (categorie_id) REFERENCES categorie_exercice(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 10. args
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS args (
    id      INT          NOT NULL AUTO_INCREMENT,
    id_user INT          NOT NULL,
    args    TEXT         NOT NULL,
    use_it  TINYINT(1)   NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 11. participants
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS participants (
    id             INT          NOT NULL AUTO_INCREMENT,
    nom            VARCHAR(255) NOT NULL,
    email          VARCHAR(255) NOT NULL,
    token          VARCHAR(255) NOT NULL UNIQUE,
    mail_envoyer   TINYINT(1)   DEFAULT 0,
    token_utilise  TINYINT(1)   DEFAULT 0,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 12. mail_valide
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mail_valide (
    id                      INT          NOT NULL AUTO_INCREMENT,
    user                    VARCHAR(255) NOT NULL UNIQUE,
    psw                     VARCHAR(255) NOT NULL,
    nbre_mail_envoyer_jrs   INT,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 13. exam
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam (
    id          INT          NOT NULL AUTO_INCREMENT,
    exam_name   VARCHAR(255) NOT NULL,
    id_part_one INT          NOT NULL,
    id_part_two INT          NOT NULL,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 14. exam_user
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS exam_user (
    id         INT          NOT NULL AUTO_INCREMENT,
    exam_id    VARCHAR(100) NOT NULL,
    id_user    INT          NOT NULL,
    email      VARCHAR(255) NOT NULL,
    user_name  VARCHAR(255) NOT NULL,
    last_name  VARCHAR(255) NOT NULL,
    note_one   INT          DEFAULT 0,
    time_one   DATETIME     DEFAULT NULL,
    note_two   INT          DEFAULT 0,
    time_two   DATETIME     DEFAULT NULL,
    qr_code    TEXT,
    created_at DATETIME     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 15. participants_2nd
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS participants_2nd (
    id            INT          NOT NULL AUTO_INCREMENT,
    nom           VARCHAR(255) NOT NULL,
    email         VARCHAR(255) NOT NULL,
    token         VARCHAR(255) NOT NULL UNIQUE,
    mail_envoyer  TINYINT(1)   DEFAULT 0,
    token_utilise TINYINT(1)   DEFAULT 0,
    created_at    DATETIME     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 16. broadcast_history
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS broadcast_history (
    id          INT          NOT NULL AUTO_INCREMENT,
    tag         TEXT,
    category    TEXT,
    format      TEXT,
    message     TEXT,
    total       INT,
    sent        INT,
    errors      INT,
    started_at  DATETIME,
    finished_at DATETIME,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 17. categories_meta
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories_meta (
    id             INT          NOT NULL AUTO_INCREMENT,
    name_categorie VARCHAR(255) NOT NULL UNIQUE,
    color          VARCHAR(20)  DEFAULT '#38bdf8',
    description    TEXT,
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 18. category_rules
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS category_rules (
    id             INT          NOT NULL AUTO_INCREMENT,
    name_categorie VARCHAR(255) NOT NULL,
    trigger_type   VARCHAR(30)  NOT NULL,
    trigger_value  TEXT,
    is_active      TINYINT(1)   DEFAULT 1,
    created_at     DATETIME     DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    CONSTRAINT fk_cr_cat FOREIGN KEY (name_categorie)
        REFERENCES categories_meta(name_categorie) ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 19. conversations
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS conversations (
    id              INT        NOT NULL AUTO_INCREMENT,
    user_id         BIGINT     NOT NULL UNIQUE,
    ia_enabled      TINYINT(1) DEFAULT 1,
    is_blocked      TINYINT(1) DEFAULT 0,
    unread_count    INT        DEFAULT 0,
    last_message_id INT        DEFAULT NULL,
    last_activity   DATETIME   DEFAULT NULL,
    pinned          TINYINT(1) DEFAULT 0,
    archived        TINYINT(1) DEFAULT 0,
    note_admin      TEXT       DEFAULT NULL,
    created_at      DATETIME   DEFAULT NOW(),
    updated_at      DATETIME   DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_conv_user     (user_id),
    INDEX idx_conv_activity (last_activity DESC),
    INDEX idx_conv_unread   (unread_count DESC),
    CONSTRAINT fk_conv_user FOREIGN KEY (user_id)
        REFERENCES users(telegram_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 20. trade_comments
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_comments (
    id         INT      NOT NULL AUTO_INCREMENT,
    trade_id   INT      NOT NULL,
    user_id    INT      NOT NULL,
    comment    TEXT     NOT NULL,
    created_at DATETIME DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT fk_tc_trade FOREIGN KEY (trade_id) REFERENCES trade_journal(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 21. subscriptions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscriptions (
    id            INT          NOT NULL AUTO_INCREMENT,
    user_id       BIGINT       NOT NULL,
    plan          VARCHAR(100) NOT NULL,
    duration_days INT          NOT NULL,
    started_at    DATETIME     NOT NULL,
    expires_at    DATETIME     NOT NULL,
    status        VARCHAR(30)  DEFAULT 'active',
    note          TEXT         DEFAULT NULL,
    created_at    DATETIME     DEFAULT NOW(),
    updated_at    DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_sub_user    (user_id),
    INDEX idx_sub_status  (status),
    INDEX idx_sub_expires (expires_at),
    CONSTRAINT fk_sub_user FOREIGN KEY (user_id)
        REFERENCES users(telegram_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 22. signals
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signals (
    id               INT          NOT NULL AUTO_INCREMENT,
    pair             VARCHAR(20)  NOT NULL,
    direction        VARCHAR(10)  NOT NULL,
    timeframe        VARCHAR(10)  DEFAULT 'H4',
    entry_price      FLOAT        NOT NULL,
    tp1              FLOAT,
    tp2              FLOAT,
    sl               FLOAT,
    note             TEXT,
    screenshot_url   TEXT,
    category         VARCHAR(100) DEFAULT 'clients_actifs',
    status           VARCHAR(20)  DEFAULT 'open',
    close_price      FLOAT,
    close_result     VARCHAR(20),
    close_screenshot TEXT,
    result_pips      FLOAT,
    result_percent   FLOAT,
    published_at     DATETIME     DEFAULT NOW(),
    closed_at        DATETIME,
    lot_suggested    FLOAT,
    broadcast_id     INT,
    PRIMARY KEY (id),
    INDEX idx_signal_status (status),
    INDEX idx_signal_pub    (published_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 23. trade_journal
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_journal (
    id               INT         NOT NULL AUTO_INCREMENT,
    signal_id        INT         NOT NULL,
    user_id          INT         NOT NULL,
    participated     TINYINT(1)  DEFAULT 1,
    entry_price      FLOAT,
    exit_price       FLOAT,
    result_pips      FLOAT,
    result_percent   FLOAT,
    gain_usd         FLOAT,
    lot_used         FLOAT,
    behavior         VARCHAR(30),
    screenshot_url   TEXT,
    capital_before   FLOAT,
    capital_after    FLOAT,
    submitted_at     DATETIME    DEFAULT NOW(),
    status           VARCHAR(20) DEFAULT 'closed',
    PRIMARY KEY (id),
    UNIQUE KEY uq_tj (signal_id, user_id),
    INDEX idx_tj_user   (user_id),
    INDEX idx_tj_signal (signal_id),
    CONSTRAINT fk_tj_signal FOREIGN KEY (signal_id) REFERENCES signals(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 24. forms
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS forms (
    id            INT          NOT NULL AUTO_INCREMENT,
    name          VARCHAR(255) NOT NULL,
    command       VARCHAR(100) NOT NULL UNIQUE,
    type          VARCHAR(50)  NOT NULL,
    trigger_type  VARCHAR(50)  NOT NULL DEFAULT 'command',
    trigger_value TEXT,
    intro         TEXT    DEFAULT NULL,
    outro         TEXT    DEFAULT NULL,
    fields        LONGTEXT,
    actions       LONGTEXT,
    conditions    LONGTEXT,
    quiz_config   LONGTEXT,
    options       LONGTEXT,
    actif         TINYINT(1)   NOT NULL DEFAULT 1,
    cree_le       DATETIME     DEFAULT NOW(),
    modifie_le    DATETIME     DEFAULT NOW(),
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    DATETIME,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 25. form_sessions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS form_sessions (
    id          INT          NOT NULL AUTO_INCREMENT,
    form_id     INT          NOT NULL,
    telegram_id BIGINT       NOT NULL,
    step_index  INT          NOT NULL DEFAULT 0,
    status      VARCHAR(30)  NOT NULL DEFAULT 'in_progress',
    score       INT          NOT NULL DEFAULT 0,
    started_at  DATETIME     DEFAULT NOW(),
    updated_at  DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    UNIQUE KEY uq_fs (form_id, telegram_id),
    INDEX idx_sessions_user (telegram_id),
    INDEX idx_sessions_form (form_id),
    CONSTRAINT fk_fs_form FOREIGN KEY (form_id) REFERENCES forms(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 26. form_submissions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS form_submissions (
    id           INT      NOT NULL AUTO_INCREMENT,
    session_id   INT      NOT NULL UNIQUE,
    form_id      INT      NOT NULL,
    telegram_id  BIGINT   NOT NULL,
    score_final  INT      DEFAULT 0,
    score_max    INT      DEFAULT 0,
    pct          INT      DEFAULT 0,
    actions_done LONGTEXT    DEFAULT NULL,
    submitted_at DATETIME DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_submissions_form (form_id),
    CONSTRAINT fk_fsub_session FOREIGN KEY (session_id) REFERENCES form_sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 27. form_responses
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS form_responses (
    id          INT          NOT NULL AUTO_INCREMENT,
    session_id  INT          NOT NULL,
    form_id     INT          NOT NULL,
    telegram_id BIGINT       NOT NULL,
    field_id    INT          NOT NULL,
    field_type  VARCHAR(50)  NOT NULL,
    value       TEXT,
    is_correct  TINYINT(1),
    points      INT          DEFAULT 0,
    answered_at DATETIME     DEFAULT NOW(),
    field_label TEXT    DEFAULT NULL,
    created_at  DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_responses_sess (session_id),
    CONSTRAINT fk_fr_session FOREIGN KEY (session_id) REFERENCES form_sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 28. signal_participations
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS signal_participations (
    id           INT         NOT NULL AUTO_INCREMENT,
    signal_id    INT         NOT NULL,
    user_id      INT         NOT NULL,
    response     VARCHAR(10) NOT NULL,
    responded_at DATETIME    DEFAULT NOW(),
    PRIMARY KEY (id),
    UNIQUE KEY uq_sp (signal_id, user_id),
    INDEX idx_sp_signal (signal_id),
    INDEX idx_sp_user   (user_id),
    CONSTRAINT fk_sp_signal FOREIGN KEY (signal_id) REFERENCES signals(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 29. followup_comments
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS followup_comments (
    id              INT         NOT NULL AUTO_INCREMENT,
    signal_id       INT         NOT NULL,
    type            VARCHAR(20) NOT NULL,
    message         TEXT        NOT NULL,
    screenshot_url  TEXT,
    broadcast_id    INT,
    sent_at         DATETIME    DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT fk_fc_signal FOREIGN KEY (signal_id) REFERENCES signals(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 30. trading_pairs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trading_pairs (
    id             INT          NOT NULL AUTO_INCREMENT,
    symbol         VARCHAR(20)  NOT NULL UNIQUE,
    category       VARCHAR(20)  DEFAULT 'forex',
    pip_value      FLOAT        NOT NULL DEFAULT 10.0,
    decimals       INT          DEFAULT 5,
    binance_symbol VARCHAR(50),
    is_active      TINYINT(1)   DEFAULT 1,
    note           TEXT,
    created_at     DATETIME     DEFAULT NOW(),
    updated_at     DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 31. member_capital
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS member_capital (
    id          INT          NOT NULL AUTO_INCREMENT,
    user_id     INT          NOT NULL,
    capital     FLOAT        NOT NULL,
    type        VARCHAR(20)  DEFAULT 'gains',
    declared_at DATETIME     DEFAULT NOW(),
    source      VARCHAR(50)  DEFAULT 'form',
    PRIMARY KEY (id),
    INDEX idx_capital_user (user_id, declared_at DESC)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 32. ai_bilans
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ai_bilans (
    id           INT          NOT NULL AUTO_INCREMENT,
    week_label   VARCHAR(100) NOT NULL,
    week_start   DATETIME     NOT NULL,
    week_end     DATETIME     NOT NULL,
    target       VARCHAR(50)  DEFAULT 'journalised',
    total_sent   INT          DEFAULT 0,
    open_rate    FLOAT,
    broadcast_id INT,
    generated_at DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 33. invite_links
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invite_links (
    id            INT          NOT NULL AUTO_INCREMENT,
    name          VARCHAR(255) NOT NULL,
    start_param   VARCHAR(255) NOT NULL UNIQUE,
    auto_category TEXT,
    promo_code    VARCHAR(100),
    quota_max     INT,
    quota_used    INT          DEFAULT 0,
    expires_at    DATETIME,
    source        VARCHAR(50)  DEFAULT 'direct',
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    DATETIME     DEFAULT NOW(),
    form_id       INT,
    PRIMARY KEY (id),
    CONSTRAINT fk_il_form FOREIGN KEY (form_id) REFERENCES forms(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 34. invite_link_stats
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS invite_link_stats (
    id          INT         NOT NULL AUTO_INCREMENT,
    link_id     INT         NOT NULL,
    user_id     INT,
    event       VARCHAR(20) NOT NULL,
    occurred_at DATETIME    DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT fk_ils_link FOREIGN KEY (link_id) REFERENCES invite_links(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 35. ia_trigger_config
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ia_trigger_config (
    id             INT         NOT NULL DEFAULT 1,
    trigger_type   VARCHAR(20) NOT NULL DEFAULT 'form',
    messages_count INT         DEFAULT 5,
    updated_at     DATETIME    DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 36. automation_jobs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS automation_jobs (
    id             INT          NOT NULL AUTO_INCREMENT,
    name           VARCHAR(255) NOT NULL,
    trig_type      VARCHAR(20)  NOT NULL,
    freq           VARCHAR(50),
    run_time       VARCHAR(20),
    cond_field     VARCHAR(100),
    cond_value     TEXT,
    cond_extra     TEXT,
    event_type     VARCHAR(50),
    target         VARCHAR(50)  NOT NULL DEFAULT 'all',
    action_type    VARCHAR(50)  NOT NULL,
    action_content TEXT,
    is_active      TINYINT(1)   DEFAULT 1,
    last_run_at    DATETIME,
    next_run_at    DATETIME,
    exec_count     INT          DEFAULT 0,
    err_count      INT          DEFAULT 0,
    created_at     DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 37. automation_logs
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS automation_logs (
    id          INT         NOT NULL AUTO_INCREMENT,
    job_id      INT         NOT NULL,
    started_at  DATETIME    DEFAULT NOW(),
    finished_at DATETIME,
    total       INT         DEFAULT 0,
    sent        INT         DEFAULT 0,
    errors      INT         DEFAULT 0,
    status      VARCHAR(20) DEFAULT 'running',
    notes       TEXT,
    PRIMARY KEY (id),
    CONSTRAINT fk_al_job FOREIGN KEY (job_id) REFERENCES automation_jobs(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 38. subscription_plans
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_plans (
    id            INT          NOT NULL AUTO_INCREMENT,
    name          VARCHAR(255) NOT NULL,
    price_usd     FLOAT        NOT NULL,
    duration_days INT          NOT NULL DEFAULT 30,
    trial_days    INT          DEFAULT 0,
    categories    TEXT    DEFAULT NULL,
    description   TEXT,
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 39. growth_subscriptions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS growth_subscriptions (
    id           INT          NOT NULL AUTO_INCREMENT,
    user_id      INT,
    member_name  VARCHAR(255),
    plan_id      INT          NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'active',
    price_paid   FLOAT        DEFAULT 0,
    promo_code   VARCHAR(100),
    started_at   DATETIME     DEFAULT NOW(),
    expires_at   DATETIME,
    cancelled_at DATETIME,
    PRIMARY KEY (id),
    INDEX idx_gsub_user   (user_id),
    INDEX idx_gsub_status (status, expires_at),
    CONSTRAINT fk_gs_plan FOREIGN KEY (plan_id) REFERENCES subscription_plans(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 40. promo_codes
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS promo_codes (
    id              INT          NOT NULL AUTO_INCREMENT,
    code            VARCHAR(100) NOT NULL UNIQUE,
    discount_type   VARCHAR(20)  NOT NULL,
    discount_value  FLOAT        NOT NULL,
    plan_id         INT,
    quota_max       INT,
    current_uses    INT          DEFAULT 0,
    first_time_only TINYINT(1)   DEFAULT 1,
    is_active       TINYINT(1)   DEFAULT 1,
    expires_at      DATETIME,
    created_at      DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT fk_pc_plan FOREIGN KEY (plan_id) REFERENCES subscription_plans(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 41. auto_promo_config
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS auto_promo_config (
    id                 INT       NOT NULL DEFAULT 1,
    anniversary_active TINYINT(1) DEFAULT 0,
    anniversary_pct    FLOAT      DEFAULT 15,
    winback_active     TINYINT(1) DEFAULT 0,
    winback_pct        FLOAT      DEFAULT 20,
    upgrade_active     TINYINT(1) DEFAULT 0,
    upgrade_pct        FLOAT      DEFAULT 30,
    updated_at         DATETIME   DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 42. ia_prompts
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ia_prompts (
    id            INT          NOT NULL AUTO_INCREMENT,
    name          VARCHAR(255) NOT NULL,
    description   TEXT,
    content       TEXT,
    return_format VARCHAR(20)  NOT NULL DEFAULT 'text',
    is_active     TINYINT(1)   DEFAULT 1,
    created_at    DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 43. ia_functions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ia_functions (
    id          INT          NOT NULL AUTO_INCREMENT,
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    code        TEXT,
    is_active   TINYINT(1)   DEFAULT 1,
    created_at  DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 44. subscription_info
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS subscription_info (
    id            INT          NOT NULL AUTO_INCREMENT,
    plan          VARCHAR(100) NOT NULL,
    duration_days INT          NOT NULL,
    started_at    DATETIME     NOT NULL,
    expires_at    DATETIME     NOT NULL,
    status        VARCHAR(30)  DEFAULT 'pending',
    note          TEXT         DEFAULT NULL,
    order_id      VARCHAR(100),
    name          VARCHAR(255),
    email         VARCHAR(255),
    phone         VARCHAR(50),
    country_code  VARCHAR(10),
    billing_cycle VARCHAR(30),
    amount_usd    FLOAT,
    currency      VARCHAR(10),
    amount_local  FLOAT,
    aggregator    VARCHAR(50),
    paid_at       DATETIME,
    created_at    DATETIME     DEFAULT NOW(),
    updated_at    DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 45. categories_backup
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS categories_backup (
    id             INT,
    id_user        INT,
    name_categorie TEXT,
    created_at     TEXT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 46. gold_seasons
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_seasons (
    id                  INT          NOT NULL AUTO_INCREMENT,
    name                VARCHAR(255) NOT NULL,
    description         TEXT,
    start_date          DATETIME     NOT NULL,
    end_date            DATETIME,
    initial_capital_ref FLOAT,
    status              VARCHAR(20)  DEFAULT 'active',
    total_trades        INT          DEFAULT 0,
    wins                INT          DEFAULT 0,
    losses              INT          DEFAULT 0,
    created_by          BIGINT       DEFAULT 571718066,
    created_at          DATETIME     DEFAULT NOW(),
    closed_at           DATETIME,
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 47. gold_tp_rules
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_tp_rules (
    id                      INT          NOT NULL AUTO_INCREMENT,
    rule_name               VARCHAR(255) NOT NULL,
    tp_level                INT          NOT NULL,
    min_capital             FLOAT        NOT NULL DEFAULT 0,
    max_capital             FLOAT,
    risk_pct                FLOAT        NOT NULL DEFAULT 1.0,
    message_tp1_reached     TEXT,
    message_tp2_reached     TEXT,
    message_tp3_reached     TEXT,
    message_sl_touched      TEXT,
    message_breakeven       TEXT,
    message_partial_close   TEXT,
    message_teaser          TEXT,
    message_confirmation    TEXT,
    is_active               TINYINT(1)   DEFAULT 1,
    created_at              DATETIME     DEFAULT NOW(),
    updated_at              DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 48. gold_trade_sessions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_trade_sessions (
    id                    INT          NOT NULL AUTO_INCREMENT,
    signal_id             INT,
    season_id             INT,
    pair                  VARCHAR(20)  NOT NULL DEFAULT 'XAU/USD',
    direction             VARCHAR(10)  NOT NULL,
    entry_price           FLOAT        NOT NULL,
    tp1                   FLOAT,
    tp2                   FLOAT,
    tp3                   FLOAT,
    sl                    FLOAT        NOT NULL,
    sl_pips               FLOAT,
    tp1_pips              FLOAT,
    tp2_pips              FLOAT,
    tp3_pips              FLOAT,
    timeframe             VARCHAR(10)  DEFAULT 'M15',
    confidence_level      INT          DEFAULT 3,
    note                  TEXT,
    screenshot_url        TEXT,
    current_phase         VARCHAR(20)  DEFAULT 'teaser',
    live_price_last       FLOAT,
    live_price_updated_at DATETIME,
    tp1_reached_at        DATETIME,
    tp2_reached_at        DATETIME,
    tp3_reached_at        DATETIME,
    sl_touched_at         DATETIME,
    total_members_in      INT          DEFAULT 0,
    total_lots_engaged    FLOAT        DEFAULT 0,
    estimated_loss_sl     FLOAT        DEFAULT 0,
    estimated_gain_tp1    FLOAT        DEFAULT 0,
    estimated_gain_tp2    FLOAT        DEFAULT 0,
    estimated_gain_tp3    FLOAT        DEFAULT 0,
    aggregates_updated_at DATETIME,
    teaser_sent_at        DATETIME,
    opened_at             DATETIME,
    closed_at             DATETIME,
    created_at            DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_gts_season (season_id),
    INDEX idx_gts_phase  (current_phase),
    CONSTRAINT fk_gts_signal FOREIGN KEY (signal_id)  REFERENCES signals(id),
    CONSTRAINT fk_gts_season FOREIGN KEY (season_id)  REFERENCES gold_seasons(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 49. gold_user_sessions
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_user_sessions (
    id             INT         NOT NULL AUTO_INCREMENT,
    session_id     INT         NOT NULL,
    user_id        INT         NOT NULL,
    step           VARCHAR(20) NOT NULL DEFAULT 'teaser',
    capital_input  FLOAT,
    updated_at     DATETIME    DEFAULT NOW(),
    PRIMARY KEY (id),
    UNIQUE KEY uq_gus (session_id, user_id),
    INDEX idx_gus_session (session_id),
    INDEX idx_gus_user    (user_id),
    CONSTRAINT fk_gus_session FOREIGN KEY (session_id) REFERENCES gold_trade_sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 50. gold_member_entries
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_member_entries (
    id                   INT          NOT NULL AUTO_INCREMENT,
    session_id           INT          NOT NULL,
    user_id              INT          NOT NULL,
    season_id            INT,
    capital_declared     FLOAT        NOT NULL,
    risk_pct             FLOAT        NOT NULL DEFAULT 1.0,
    risk_usd             FLOAT        NOT NULL,
    lot_calculated       FLOAT        NOT NULL,
    tp_level_assigned    INT          NOT NULL,
    perte_sl             FLOAT        NOT NULL,
    gain_tp1             FLOAT        NOT NULL,
    gain_tp2             FLOAT,
    gain_tp3             FLOAT,
    exit_price           FLOAT,
    exit_tp_level        INT,
    result_pips          FLOAT,
    result_usd           FLOAT,
    capital_before       FLOAT,
    capital_after        FLOAT,
    followed_instruction TINYINT(1)   DEFAULT NULL,
    behavior             VARCHAR(30),
    step_reached         VARCHAR(30)  DEFAULT 'confirmed',
    confirmed_at         DATETIME     DEFAULT NOW(),
    exited_at            DATETIME,
    PRIMARY KEY (id),
    UNIQUE KEY uq_gme (session_id, user_id),
    INDEX idx_gme_session (session_id),
    INDEX idx_gme_user    (user_id),
    CONSTRAINT fk_gme_session FOREIGN KEY (session_id) REFERENCES gold_trade_sessions(id),
    CONSTRAINT fk_gme_season  FOREIGN KEY (season_id)  REFERENCES gold_seasons(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 51. gold_flow_events
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold_flow_events (
    id         INT          NOT NULL AUTO_INCREMENT,
    session_id INT          NOT NULL,
    user_id    INT          NOT NULL,
    event_type VARCHAR(50)  NOT NULL,
    payload    TEXT,
    created_at DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    INDEX idx_gfe_session (session_id),
    CONSTRAINT fk_gfe_session FOREIGN KEY (session_id) REFERENCES gold_trade_sessions(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 52. simulation_accounts
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulation_accounts (
    id               INT          NOT NULL AUTO_INCREMENT,
    name             VARCHAR(255) NOT NULL,
    description      TEXT,
    initial_capital  FLOAT        NOT NULL,
    current_capital  FLOAT        NOT NULL,
    currency         VARCHAR(10)  DEFAULT 'USD',
    risk_pct_default FLOAT        DEFAULT 1.0,
    total_trades     INT          DEFAULT 0,
    wins             INT          DEFAULT 0,
    losses           INT          DEFAULT 0,
    max_drawdown_pct FLOAT        DEFAULT 0,
    peak_capital     FLOAT,
    is_active        TINYINT(1)   DEFAULT 1,
    season_id        INT,
    created_by       BIGINT       DEFAULT 571718066,
    created_at       DATETIME     DEFAULT NOW(),
    updated_at       DATETIME     DEFAULT NOW(),
    PRIMARY KEY (id),
    CONSTRAINT fk_sa_season FOREIGN KEY (season_id) REFERENCES gold_seasons(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- 53. simulation_trades
-- ------------------------------------------------------------
CREATE TABLE IF NOT EXISTS simulation_trades (
    id               INT          NOT NULL AUTO_INCREMENT,
    account_id       INT          NOT NULL,
    session_id       INT          NOT NULL,
    season_id        INT,
    entry_price      FLOAT        NOT NULL,
    tp1              FLOAT,
    tp2              FLOAT,
    tp3              FLOAT,
    sl               FLOAT        NOT NULL,
    direction        VARCHAR(10)  NOT NULL,
    capital_before   FLOAT        NOT NULL,
    risk_pct         FLOAT        NOT NULL,
    risk_usd         FLOAT        NOT NULL,
    lot_used         FLOAT        NOT NULL,
    tp_level_target  INT          NOT NULL,
    perte_sl         FLOAT        NOT NULL,
    gain_tp1         FLOAT        NOT NULL,
    gain_tp2         FLOAT,
    gain_tp3         FLOAT,
    exit_price       FLOAT,
    exit_tp_level    INT,
    result_pips      FLOAT,
    result_usd       FLOAT,
    capital_after    FLOAT,
    status           VARCHAR(20)  DEFAULT 'open',
    opened_at        DATETIME     DEFAULT NOW(),
    closed_at        DATETIME,
    PRIMARY KEY (id),
    INDEX idx_sim_account (account_id),
    INDEX idx_sim_session (session_id),
    CONSTRAINT fk_st_account FOREIGN KEY (account_id) REFERENCES simulation_accounts(id),
    CONSTRAINT fk_st_session FOREIGN KEY (session_id) REFERENCES gold_trade_sessions(id),
    CONSTRAINT fk_st_season  FOREIGN KEY (season_id)  REFERENCES gold_seasons(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ------------------------------------------------------------
-- Trigger : équivalent de trg_upsert_conv
-- ------------------------------------------------------------
DELIMITER $$
CREATE TRIGGER trg_upsert_conv
AFTER INSERT ON messages
FOR EACH ROW
BEGIN
    INSERT INTO conversations (user_id, last_message_id, last_activity, unread_count, updated_at)
    VALUES (NEW.user_id, NEW.id, NEW.created_at, 1, NEW.created_at)
    ON DUPLICATE KEY UPDATE
        last_message_id = NEW.id,
        last_activity   = NEW.created_at,
        unread_count    = CASE
                            WHEN NEW.direction = 'inbound' THEN unread_count + 1
                            ELSE unread_count
                          END,
        updated_at      = NEW.created_at;
END$$
DELIMITER ;

SET FOREIGN_KEY_CHECKS = 1;
-- ============================================================
-- FIN DU SCHÉMA  –  53 tables + 1 trigger
-- ============================================================