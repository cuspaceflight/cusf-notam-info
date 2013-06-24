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
    -- priority = 0: disabled; otherwise lowest is first.
    priority SMALLINT NOT NULL CHECK (priority >= 0),

    PRIMARY KEY (id)
);

CREATE TABLE messages (
    id SERIAL,
    short_name VARCHAR(40) NOT NULL CHECK (short_name != ''),
    web_short_text VARCHAR(500) NOT NULL CHECK (web_short_text != ''),
    web_long_text VARCHAR(2000) NOT NULL CHECK (web_long_text != ''),
    -- call_text = NULL: pass call straight to humans
    call_text VARCHAR(500) CHECK (call_text IS NULL OR call_text != ''),
    active_when TSRANGE DEFAULT NULL,

    PRIMARY KEY (id),
    CONSTRAINT overlapping_range EXCLUDE USING gist (active_when WITH &&),
    CONSTRAINT active_when_finite
        CHECK (LOWER_INF(active_when) = FALSE AND
               UPPER_INF(active_when) = FALSE),
    CONSTRAINT active_when_closed_open
        CHECK (LOWER_INC(active_when) = TRUE AND
               UPPER_INC(active_when) = FALSE)
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
