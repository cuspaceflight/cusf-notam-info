\set ON_ERROR_STOP

DROP TABLE IF EXISTS call_log;
DROP TABLE IF EXISTS humans;
DROP TABLE IF EXISTS messages;

CREATE TABLE call_log (
    id SERIAL,
    call VARCHAR(120) NOT NULL CHECK (call != ''),
    time TIMESTAMP NOT NULL,
    message VARCHAR(500) NOT NULL CHECK (message != ''),

    PRIMARY KEY (id)
);

CREATE INDEX call_log_index ON call_log (call, time, id);
-- for query:
--   SELECT message FROM call_log WHERE call = %s ORDER BY time ASC, id ASC;

CREATE TABLE humans (
    id SERIAL,
    name VARCHAR(50) NOT NULL UNIQUE CHECK (name != ''),
    phone VARCHAR(25) NOT NULL UNIQUE CHECK (phone ~ '^\+[0-9]+$'),
    -- priority == 0: disabled; otherwise lowest is first.
    priority SMALLINT NOT NULL CHECK (priority >= 0),

    PRIMARY KEY (id)
);

CREATE TABLE messages (
    id SERIAL,
    text VARCHAR(500) NOT NULL CHECK (text != ''),
    active_when TSRANGE DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,

    PRIMARY KEY (id),
    CONSTRAINT default_xor_tsrange
        CHECK (is_default = (active_when IS NULL)),
    CONSTRAINT one_default
        EXCLUDE (is_default WITH =) WHERE (is_default = TRUE),
    CONSTRAINT overlapping_range
        EXCLUDE USING gist (active_when WITH &&) WHERE (is_default = FALSE)
);

CREATE INDEX messages_active_index ON messages USING gist (active_when);

GRANT SELECT, INSERT, UPDATE, DELETE ON call_log TO "www-data";
GRANT SELECT, UPDATE ON call_log_id_seq TO "www-data";
GRANT SELECT, INSERT, UPDATE, DELETE ON humans TO "www-data";
GRANT SELECT, UPDATE ON humans_id_seq TO "www-data";
GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO "www-data";
GRANT SELECT, UPDATE ON messages_id_seq TO "www-data";

INSERT INTO humans (name, phone, priority) VALUES
    ('Daniel Richman', '+447913430431', 1);
