CREATE TABLE call_log (
    id SERIAL,
    call VARCHAR(120) NOT NULL CHECK (call != ''),
    time BIGINT NOT NULL CHECK (time >= 0),
    message VARCHAR(500) NOT NULL CHECK (message != ''),

    PRIMARY KEY (id)
);

CREATE INDEX call_log_index ON call_log ( call, time );
-- SELECT message FROM call_log WHERE call = %s ORDER BY time ASC;

CREATE TABLE humans (
    id SERIAL,
    name VARCHAR(50) NOT NULL CHECK (name != ''),
    phone VARCHAR(25) NOT NULL CHECK (phone ~ '+[0-9]+$'),
    priority SMALLINT NOT NULL,

    PRIMARY KEY (id)
);

CREATE TABLE messages (
    id SERIAL,
    -- if audio is not null, text is its description or transcription
    text VARCHAR(500) NOT NULL CHECK (text != ''),
    audio BYTEA DEFAULT NULL,
    is_default BOOLEAN DEFAULT FALSE,
    active_until BIGINT DEFAULT NULL,

    PRIMARY KEY (id)
);

GRANT SELECT, INSERT, UPDATE, DELETE ON call_log TO "www-data";
GRANT SELECT, UPDATE ON call_log_id_seq TO "www-data";
GRANT SELECT, INSERT, UPDATE, DELETE ON humans TO "www-data";
GRANT SELECT, UPDATE ON humans_id_seq TO "www-data";
GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO "www-data";
GRANT SELECT, UPDATE ON messages_id_seq TO "www-data";
