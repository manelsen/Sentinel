SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS groups (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_group_id TEXT NOT NULL,
    display_name TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(platform, external_group_id)
);

CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    display_name TEXT,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    UNIQUE(platform, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    external_message_id TEXT,
    message_type TEXT NOT NULL,
    raw_text TEXT,
    received_at TEXT NOT NULL,
    sent_at TEXT,
    reply_to_message_id TEXT,
    quoted_message_id TEXT,
    has_media INTEGER NOT NULL DEFAULT 0,
    media_type TEXT,
    media_path TEXT,
    ingest_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id),
    FOREIGN KEY (user_id) REFERENCES users(id),
    FOREIGN KEY (reply_to_message_id) REFERENCES messages(id),
    FOREIGN KEY (quoted_message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS audio_transcriptions (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    transcript_text TEXT,
    language TEXT,
    confidence REAL,
    duration_seconds REAL,
    status TEXT NOT NULL,
    error_message TEXT,
    started_at TEXT,
    finished_at TEXT,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS normalized_messages (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    analysis_text TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    contains_profanity INTEGER NOT NULL DEFAULT 0,
    contains_direct_address INTEGER NOT NULL DEFAULT 0,
    char_count INTEGER NOT NULL,
    token_estimate INTEGER,
    language TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS message_features (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    caps_ratio REAL,
    exclamation_count INTEGER,
    question_count INTEGER,
    direct_attack_score REAL,
    profanity_score REAL,
    sarcasm_hint_score REAL,
    imperative_score REAL,
    reply_intensity_score REAL,
    negativity_score REAL,
    detail_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS analysis_windows (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    window_type TEXT NOT NULL,
    window_start_at TEXT NOT NULL,
    window_end_at TEXT NOT NULL,
    message_count INTEGER NOT NULL,
    distinct_user_count INTEGER NOT NULL,
    window_definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id)
);

CREATE TABLE IF NOT EXISTS window_messages (
    window_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    PRIMARY KEY (window_id, message_id),
    FOREIGN KEY (window_id) REFERENCES analysis_windows(id),
    FOREIGN KEY (message_id) REFERENCES messages(id)
);

CREATE TABLE IF NOT EXISTS window_features (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    feature_version TEXT NOT NULL,
    messages_per_minute REAL,
    reply_concentration_score REAL,
    dyadic_exchange_score REAL,
    participant_concentration_score REAL,
    escalation_velocity_score REAL,
    hostility_density_score REAL,
    sustained_back_and_forth_score REAL,
    audio_burst_score REAL,
    heuristic_risk_score REAL,
    feature_details_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (window_id) REFERENCES analysis_windows(id)
);

CREATE TABLE IF NOT EXISTS llm_classifications (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    request_payload_json TEXT NOT NULL,
    response_raw_text TEXT NOT NULL,
    response_json TEXT,
    parse_status TEXT NOT NULL,
    classification_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (window_id) REFERENCES analysis_windows(id)
);

CREATE TABLE IF NOT EXISTS incident_assessments (
    id TEXT PRIMARY KEY,
    window_id TEXT NOT NULL,
    llm_classification_id TEXT,
    risk_score REAL NOT NULL,
    severity TEXT NOT NULL,
    conflict_present INTEGER NOT NULL,
    trigger_message_id TEXT,
    participants_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    summary_short TEXT NOT NULL,
    summary_long TEXT,
    recommended_action TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (window_id) REFERENCES analysis_windows(id),
    FOREIGN KEY (llm_classification_id) REFERENCES llm_classifications(id)
);

CREATE TABLE IF NOT EXISTS alerts (
    id TEXT PRIMARY KEY,
    incident_assessment_id TEXT NOT NULL,
    alert_channel TEXT NOT NULL,
    alert_status TEXT NOT NULL,
    alert_payload_json TEXT NOT NULL,
    sent_at TEXT,
    acknowledged_at TEXT,
    acknowledged_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_assessment_id) REFERENCES incident_assessments(id)
);

CREATE TABLE IF NOT EXISTS moderator_feedback (
    id TEXT PRIMARY KEY,
    incident_assessment_id TEXT NOT NULL,
    feedback_type TEXT NOT NULL,
    feedback_note TEXT,
    reviewer_id TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (incident_assessment_id) REFERENCES incident_assessments(id)
);

CREATE TABLE IF NOT EXISTS daily_reports (
    id TEXT PRIMARY KEY,
    group_id TEXT NOT NULL,
    report_date TEXT NOT NULL,
    report_markdown TEXT NOT NULL,
    report_payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (group_id) REFERENCES groups(id),
    UNIQUE(group_id, report_date)
);

CREATE VIRTUAL TABLE IF NOT EXISTS message_search USING fts5(
    message_id UNINDEXED,
    group_id UNINDEXED,
    user_id UNINDEXED,
    analysis_text
);

CREATE INDEX IF NOT EXISTS idx_messages_group_sent_at ON messages(group_id, sent_at, received_at);
CREATE INDEX IF NOT EXISTS idx_messages_user_sent_at ON messages(user_id, sent_at, received_at);
CREATE INDEX IF NOT EXISTS idx_transcriptions_message ON audio_transcriptions(message_id);
CREATE INDEX IF NOT EXISTS idx_normalized_message ON normalized_messages(message_id);
CREATE INDEX IF NOT EXISTS idx_message_features_message ON message_features(message_id);
CREATE INDEX IF NOT EXISTS idx_window_group_time ON analysis_windows(group_id, window_end_at);
CREATE INDEX IF NOT EXISTS idx_incidents_window ON incident_assessments(window_id);
CREATE INDEX IF NOT EXISTS idx_alerts_incident ON alerts(incident_assessment_id);
CREATE INDEX IF NOT EXISTS idx_feedback_incident ON moderator_feedback(incident_assessment_id);
"""
