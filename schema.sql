\set ON_ERROR_STOP

DROP TABLE IF EXISTS call_log;
DROP TABLE IF EXISTS calls;
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS humans;

DROP FUNCTION IF EXISTS messages_past_insert() CASCADE;
DROP FUNCTION IF EXISTS messages_past_update() CASCADE;
DROP FUNCTION IF EXISTS messages_past_delete() CASCADE;

CREATE TABLE calls (
    id SERIAL,
    sid VARCHAR(120) NOT NULL UNIQUE CHECK (sid != ''),

    PRIMARY KEY (id)
);

CREATE TABLE call_log (
    id SERIAL,
    call INTEGER REFERENCES calls (id),
    time TIMESTAMP NOT NULL,
    message VARCHAR(500) NOT NULL CHECK (message != ''),

    PRIMARY KEY (id)
);

CREATE INDEX call_log_single_call_log_index ON call_log (call, time, id);
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
    call_text VARCHAR(500) CHECK (call_text IS NULL OR call_text != ''),
    forward_to INTEGER REFERENCES humans (id),
    active_when TSRANGE DEFAULT NULL,

    PRIMARY KEY (id),
    CONSTRAINT overlapping_range EXCLUDE USING gist (active_when WITH &&),
    CONSTRAINT active_when_finite
        CHECK (LOWER_INF(active_when) = FALSE AND
               UPPER_INF(active_when) = FALSE),
    CONSTRAINT active_when_closed_open
        CHECK (ISEMPTY(active_when) = FALSE AND
               LOWER_INC(active_when) = TRUE AND
               UPPER_INC(active_when) = FALSE),
    CONSTRAINT active_when_integer_seconds
        CHECK (DATE_TRUNC('SECOND', LOWER(active_when)) = LOWER(active_when)
               AND
               DATE_TRUNC('SECOND', UPPER(active_when)) = UPPER(active_when)),
    CONSTRAINT forward_xor_text
        CHECK ((call_text IS NULL) = (forward_to IS NOT NULL))
);

CREATE FUNCTION messages_past_insert()
    RETURNS TRIGGER AS
    $$
        BEGIN
            IF CURRENT_USER != 'www-data' THEN
                RETURN NEW;
            END IF;

            -- forbid adding a new row that is active at any time in the past
            IF NEW.active_when && TSRANGE(NULL, LOCALTIMESTAMP) THEN
                RAISE 'Cannot add a message that changes the past.';
            END IF;

            RETURN NEW;
        END
    $$
    LANGUAGE plpgsql;

CREATE TRIGGER messages_past_insert_trigger
    BEFORE INSERT
    ON messages
    FOR EACH ROW EXECUTE PROCEDURE messages_past_insert();

CREATE FUNCTION messages_past_update()
    RETURNS TRIGGER AS
    $$
        BEGIN
            IF CURRENT_USER != 'www-data' THEN
                RETURN NEW;
            END IF;

            IF OLD.active_when <@ TSRANGE(NULL, LOCALTIMESTAMP) THEN
                -- forbid modifying rows completely in the past
                IF OLD != NEW THEN
                    RAISE
                        'Cannot change a row that is completely in the past';
                END IF;
            ELSIF LOCALTIMESTAMP <@ OLD.active_when THEN
                -- forbid modifying anything other than the upper bound of
                -- active_when if the row is active
                OLD.active_when =
                    TSRANGE(LOWER(OLD.active_when), UPPER(NEW.active_when));
                IF OLD != NEW THEN
                    RAISE 'Can only change the upper bound of an active row.';
                END IF;

                -- but don't allow the upper bound to be pushed into the past
                IF  UPPER(NEW.active_when) < LOCALTIMESTAMP THEN
                    RAISE 'Cannot move the upper bound into the past.';
                END IF;
            ELSE
                -- else the message is in the future, so forbid it from being
                -- moved into the past
                IF NEW.active_when && TSRANGE(NULL, LOCALTIMESTAMP) THEN
                    RAISE 'Cannot move a message into the past.';
                END IF;
            END IF;

            RETURN NEW;
        END
    $$
    LANGUAGE plpgsql;

CREATE TRIGGER messages_past_update_trigger
    BEFORE UPDATE
    ON messages
    FOR EACH ROW EXECUTE PROCEDURE messages_past_update();

CREATE FUNCTION messages_past_delete()
    RETURNS TRIGGER AS
    $$
        BEGIN
            IF CURRENT_USER != 'www-data' THEN
                RETURN OLD;
            END IF;

            -- forbid deleting a row that is active at any time in the past
            IF OLD.active_when && TSRANGE(NULL, LOCALTIMESTAMP) THEN
                RAISE 'Cannot change the past.';
            END IF;

            RETURN OLD;
        END
    $$
    LANGUAGE plpgsql;

CREATE TRIGGER messages_past_delete_trigger
    BEFORE DELETE
    ON messages
    FOR EACH ROW EXECUTE PROCEDURE messages_past_delete();

CREATE INDEX messages_active_index ON messages USING gist (active_when);

-- allow adding to the call log
GRANT SELECT, INSERT ON calls TO "www-data";
GRANT SELECT, UPDATE ON calls_id_seq TO "www-data";
GRANT SELECT, INSERT ON call_log TO "www-data";
GRANT SELECT, UPDATE ON call_log_id_seq TO "www-data";

-- allow adding humans and modifying existing humans' priorities
GRANT SELECT, INSERT ON humans TO "www-data";
GRANT SELECT, UPDATE ON humans_id_seq TO "www-data";
GRANT UPDATE (priority) ON humans TO "www-data";

-- allow adding and updating messages. Triggers protect the past
GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO "www-data";
GRANT SELECT, UPDATE ON messages_id_seq TO "www-data";
