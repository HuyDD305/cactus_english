CREATE TABLE session_info
(
    id                  SERIAL PRIMARY KEY,
    session_id          VARCHAR(255) NOT NULL UNIQUE,
    student_name        VARCHAR(255) NOT NULL,
    student_hash        VARCHAR(255),              
    page_load_time      TIMESTAMP    NOT NULL,
    submission_time     TIMESTAMP    NOT NULL,
    num_questions       INTEGER      NOT NULL,
    passing_level       REAL         NOT NULL,
    ip_address          INET,                      
    user_agent          TEXT,                      
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE quiz_log
(
    id                  SERIAL PRIMARY KEY,
    session_id          VARCHAR(255) REFERENCES session_info(session_id),
    question_number     INTEGER,
    question_id         INTEGER,
    question            VARCHAR(255),
    user_answers        TEXT,
    correct_answers     TEXT,
    is_correct          BOOLEAN,
    first_modified_time TIMESTAMP,
    last_modified_time  TIMESTAMP,
    copy_paste_attempts INTEGER DEFAULT 0,        
    tab_switches        INTEGER DEFAULT 0,        
    time_spent_seconds  INTEGER,                  
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
-- Optional: Security events table for detailed logging
CREATE TABLE security_events
(
    id              SERIAL PRIMARY KEY,
    session_id      VARCHAR(255) REFERENCES session_info(session_id),
    event_type      VARCHAR(50) NOT NULL,         
    event_details   JSONB,                        
    timestamp       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    ip_address      INET,
    user_agent      TEXT
);
-- Indexes for better performance
CREATE INDEX idx_session_info_student ON session_info(student_name);
CREATE INDEX idx_session_info_hash ON session_info(student_hash);
CREATE INDEX idx_session_info_time ON session_info(submission_time);
CREATE INDEX idx_quiz_log_session ON quiz_log(session_id);
CREATE INDEX idx_quiz_log_correct ON quiz_log(is_correct);
CREATE INDEX idx_security_events_session ON security_events(session_id);
CREATE INDEX idx_security_events_type ON security_events(event_type);
