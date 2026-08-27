-- SQLite şeması (bkz. docs/architecture-plan.md §12).
-- Embedding'ler MVP'de BLOB (float32 dizisi) olarak tutulur; benzerlik
-- hesaplaması uygulama tarafında (numpy cosine similarity) yapılır.
-- model_name + dim her embedding satırında tutulur ki farklı embedding
-- modellerinden gelen vektörler yanlışlıkla karşılaştırılmasın (bkz. §14).

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS accounts (
    id                  TEXT PRIMARY KEY,
    provider            TEXT NOT NULL,          -- gmail | outlook | google_calendar | ms_calendar
    account_type        TEXT NOT NULL,          -- personal | school | work
    email               TEXT NOT NULL,
    oauth_token_ref     TEXT,                   -- şifrelenmiş token'a referans (token'ın kendisi değil)
    scopes              TEXT,                   -- JSON dizi
    connected_at        TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'active'  -- active | disconnected | error
);

CREATE TABLE IF NOT EXISTS email_threads (
    thread_id           TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    participants        TEXT,                   -- JSON dizi
    languages_seen      TEXT,                   -- JSON dizi
    last_message_at     TEXT
);

CREATE TABLE IF NOT EXISTS email_messages (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    provider            TEXT NOT NULL,
    message_id          TEXT NOT NULL,
    thread_id           TEXT REFERENCES email_threads(thread_id),
    subject             TEXT,
    sender              TEXT,
    recipients          TEXT,                   -- JSON dizi
    received_at         TEXT,
    detected_language   TEXT,
    body_excerpt        TEXT,                   -- tam gövde değil; minimum retention (bkz. §13)
    labels              TEXT,                   -- JSON dizi (örn. Gmail CATEGORY_PROMOTIONS) — deterministik filtre için
    processed           INTEGER NOT NULL DEFAULT 0,
    retention_expires_at TEXT,
    UNIQUE (account_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_email_messages_thread ON email_messages(thread_id);

CREATE TABLE IF NOT EXISTS calendar_events_cache (
    id                  TEXT PRIMARY KEY,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    provider            TEXT NOT NULL,
    calendar_id         TEXT,
    event_id            TEXT NOT NULL,
    title               TEXT,
    start_datetime      TEXT,
    end_datetime        TEXT,
    timezone            TEXT,
    location            TEXT,
    raw_json            TEXT,               -- Sağlayıcının ham event dict'i (Google VEYA Outlook şeklinde — bkz. parse_calendar_event)
    last_synced_at      TEXT,
    UNIQUE (account_id, calendar_id, event_id)
);
CREATE INDEX IF NOT EXISTS idx_cal_cache_range ON calendar_events_cache(account_id, start_datetime, end_datetime);

-- Bir (account_id, calendar_id) için en son canlı list_events() çağrısının
-- kapsadığı aralık + ne zaman yapıldığı — bkz. src/services/calendar_cache.py.
-- Tek satır: yeni bir istek yalnızca bu aralığın İÇİNDE kalıyorsa VE TTL
-- dolmadıysa cache'ten sunuluyor, aksi halde canlıya düşülüp bu satır o
-- yeni (daha geniş/farklı) aralıkla değiştiriliyor.
CREATE TABLE IF NOT EXISTS calendar_sync_state (
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    calendar_id         TEXT NOT NULL,
    range_start         TEXT NOT NULL,
    range_end           TEXT NOT NULL,
    synced_at           TEXT NOT NULL,
    PRIMARY KEY (account_id, calendar_id)
);

CREATE TABLE IF NOT EXISTS candidate_events (
    candidate_id            TEXT PRIMARY KEY,
    source_type              TEXT NOT NULL,      -- conversation | email
    source_references        TEXT,               -- JSON dizi
    source_languages         TEXT,               -- JSON dizi
    event_type                TEXT NOT NULL,
    title                     TEXT,
    start_datetime            TEXT,
    end_datetime              TEXT,
    timezone                  TEXT,
    duration_minutes          INTEGER,
    location                  TEXT,
    online_meeting_url        TEXT,
    participants              TEXT,              -- JSON dizi
    description               TEXT,
    importance                TEXT,
    reminders                 TEXT,               -- JSON dizi
    preparation_time_minutes  INTEGER,
    travel_time_minutes       INTEGER,
    recurrence                TEXT,
    missing_fields            TEXT,               -- JSON dizi
    ambiguous_fields          TEXT,               -- JSON dizi
    confidence                REAL DEFAULT 0.0,
    status                    TEXT NOT NULL DEFAULT 'DETECTED',
    extraction_reason         TEXT,
    retrieved_policy_ids      TEXT,               -- JSON dizi
    retrieved_correction_ids  TEXT,               -- JSON dizi
    google_event_id           TEXT,               -- takvime yazıldığında/güncellendiğinde dolar
    previous_snapshot         TEXT,               -- JSON, UPDATE_SUGGESTED iken önceki alan değerleri
    created_at                TEXT NOT NULL,
    updated_at                TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_candidate_status ON candidate_events(status);
CREATE INDEX IF NOT EXISTS idx_candidate_start ON candidate_events(start_datetime);

CREATE TABLE IF NOT EXISTS candidate_sources (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id        TEXT NOT NULL REFERENCES candidate_events(candidate_id),
    email_message_id    TEXT NOT NULL REFERENCES email_messages(id),
    relation_type       TEXT NOT NULL DEFAULT 'origin',  -- origin | update | related
    created_at          TEXT NOT NULL,
    UNIQUE (candidate_id, email_message_id)
);

CREATE TABLE IF NOT EXISTS personal_policies (
    policy_id            TEXT PRIMARY KEY,
    category              TEXT NOT NULL,
    scope                 TEXT NOT NULL,          -- global | event_type | sender | account
    natural_language_rule TEXT NOT NULL,
    language              TEXT NOT NULL,
    structured_conditions TEXT,                   -- JSON obje
    structured_action     TEXT,                   -- JSON obje
    priority              INTEGER NOT NULL DEFAULT 0,
    version               INTEGER NOT NULL DEFAULT 1,
    active                INTEGER NOT NULL DEFAULT 1,
    approved_by_user      INTEGER NOT NULL DEFAULT 1,
    source                TEXT NOT NULL,          -- manual | correction
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policies_active_scope ON personal_policies(active, scope, category);

CREATE TABLE IF NOT EXISTS policy_versions (
    version_id          TEXT PRIMARY KEY,
    policy_id           TEXT NOT NULL REFERENCES personal_policies(policy_id),
    version              INTEGER NOT NULL,
    snapshot             TEXT NOT NULL,           -- JSON: o andaki tam policy hali
    created_at           TEXT NOT NULL,
    superseded_by        TEXT REFERENCES policy_versions(version_id)
);

CREATE TABLE IF NOT EXISTS user_corrections (
    correction_id         TEXT PRIMARY KEY,
    candidate_id          TEXT REFERENCES candidate_events(candidate_id),
    original_input        TEXT,
    original_output       TEXT NOT NULL,          -- JSON
    user_feedback_text    TEXT NOT NULL,
    corrected_output      TEXT NOT NULL,          -- JSON
    correction_scope      TEXT NOT NULL DEFAULT 'single_event',
    event_type            TEXT,
    account_scope         TEXT,
    sender_scope          TEXT,
    language              TEXT NOT NULL,
    approved_for_future_use INTEGER NOT NULL DEFAULT 0,
    derived_policy_id     TEXT REFERENCES personal_policies(policy_id),
    correction_type       TEXT,                    -- NULL (alan düzeltmesi) | 'classification'
    created_at            TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id           TEXT NOT NULL REFERENCES personal_policies(policy_id),
    embedding           BLOB NOT NULL,
    model_name          TEXT NOT NULL,
    dim                 INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_policy_emb_policy ON policy_embeddings(policy_id);

CREATE TABLE IF NOT EXISTS correction_embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    correction_id       TEXT NOT NULL REFERENCES user_corrections(correction_id),
    embedding           BLOB NOT NULL,
    model_name          TEXT NOT NULL,
    dim                 INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_correction_emb_correction ON correction_embeddings(correction_id);

CREATE TABLE IF NOT EXISTS email_embeddings (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    email_message_id    TEXT NOT NULL REFERENCES email_messages(id),
    chunk_index         INTEGER NOT NULL DEFAULT 0,
    chunk_text          TEXT NOT NULL,
    embedding           BLOB NOT NULL,
    model_name          TEXT NOT NULL,
    dim                 INTEGER NOT NULL,
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_email_emb_message ON email_embeddings(email_message_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    id                  TEXT PRIMARY KEY,
    preference_key      TEXT NOT NULL,
    value               TEXT NOT NULL,           -- JSON
    derived_from        TEXT,                    -- correction_id | 'manual'
    approved_by_user    INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
    id                  TEXT PRIMARY KEY,
    candidate_id        TEXT NOT NULL REFERENCES candidate_events(candidate_id),
    action              TEXT NOT NULL,           -- approve | edit | reject
    payload_snapshot    TEXT,                    -- JSON
    created_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_states (
    provider                        TEXT NOT NULL,
    account_id                      TEXT NOT NULL REFERENCES accounts(id),
    last_sync_at                    TEXT,
    provider_cursor_or_history_id   TEXT,
    PRIMARY KEY (provider, account_id)
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id                  TEXT PRIMARY KEY,
    actor               TEXT NOT NULL,           -- user | system
    action              TEXT NOT NULL,
    entity_type         TEXT NOT NULL,
    entity_id           TEXT NOT NULL,
    reason              TEXT,
    source_policy_id    TEXT REFERENCES personal_policies(policy_id),
    source_correction_id TEXT REFERENCES user_corrections(correction_id),
    created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_logs(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS localization_preferences (
    account_id          TEXT PRIMARY KEY REFERENCES accounts(id),
    ui_language         TEXT NOT NULL DEFAULT 'tr',
    date_format_pref    TEXT,
    timezone            TEXT
);

-- Chatbox: kalıcı konuşma durumu + geçmişi (bkz. plan "Web Chatbox").
-- Bir oturum = tarayıcı sekmesi başına, aktif hesaba bağlı, tek seferde
-- yalnızca BİR akış (create_event XOR query_calendar XOR update_event)
-- yürüten durum makinesi. flow/step state_json'dan denormalize — bozuk/
-- yarım kalmış bir oturumun ham SQL ile (JSON parse etmeden) görülebilmesi
-- için, candidate_events.status'un neden ayrı bir sütun olduğu gerekçesiyle
-- aynı.
CREATE TABLE IF NOT EXISTS chat_sessions (
    session_id      TEXT PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    flow            TEXT,                         -- NULL | create_event | query_calendar | update_event
    step            TEXT,
    state_json      TEXT NOT NULL DEFAULT '{}',    -- ChatState.model_dump(mode="json")
    created_at      TEXT NOT NULL,
    updated_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES chat_sessions(session_id),
    role            TEXT NOT NULL,                 -- user | assistant
    text            TEXT NOT NULL,
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id);
