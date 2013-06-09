import flask
from twilio import twiml
import logging
import smtplib
import time
import re
from psycopg2.pool import ThreadedConnectionPool

from flask import request, url_for

app = flask.Flask(__name__)


## PostgreSQL

postgres_pool = None

@app.before_first_request
def setup_postgres_pool():
    """
    Initialise the postgres connection pool

    Happens "before_first_request" rather than at module init since app.config
    could change
    """

    global postgres_pool
    postgres_pool = ThreadedConnectionPool(1, 10, app.config["POSTGRES"])

def cursor():
    """
    Get a postgres cursor for immediate use during a request

    If a cursor has not yet been used in this request, it connects to the
    database. Further cursors re-use the per-request connection.
    The connection is committed and closed at the end of the request.
    """

    assert flask.has_request_context()
    top = flask._app_ctx_stack.top
    if not hasattr(top, '_database'):
        top._database = postgres_pool.getconn()
    return top._database.cursor()

@app.teardown_appcontext
def close_db_connection(exception):
    """Commit and close the per-request postgres connection"""
    top = flask._app_ctx_stack.top
    if hasattr(top, '_database'):
        top._database.commit()
        postgres_pool.putconn(top._database)


## Logging and call_log

logger = logging.getLogger("notam")
call_logger = logging.getLogger("notam.call")

def get_sid():
    if "parent_sid" in request.args:
        # in call_human_pickup: the TwiML executes on the dialed party
        # before connecting to the call, and has a separate ID
        return request.args["parent_sid"]
    else:
        return request.form["CallSid"]

def call_log(message):
    """Log message (via logging) and add it to the call_log table"""
    assert flask.has_request_context()

    sid = get_sid()
    call_logger.info("{0} {1}".format(sid, message))

    db_msg = message.encode('ascii', 'replace')
    query = "INSERT INTO call_log (call, time, message) " \
            "VALUES (%s, NOW(), %s)"

    with cursor() as cur:
        cur.execute(query, (sid, db_msg))

def get_call_log():
    assert flask.has_request_context()

    query = "SELECT time, message FROM call_log " \
            "WHERE call = %s ORDER BY time ASC, id ASC"
    fmt = lambda time, message: \
            "{0} {1}".format(time.strftime("%H:%M:%S"), message)

    with cursor() as cur:
        cur.execute(query, (get_sid(), ))
        return "\n".join(fmt(time, message) for time, message in cur)

def email(subject, message):
    logger.debug("email: {0} {1!r}".format(subject, message))

    email = "From: {0}\r\nTo: {1}\r\nSubject: CUSF Notam Twilio {2}\r\n\r\n" \
        .format(app.config['EMAIL_FROM'], ",".join(app.config['EMAIL_TO']),
                subject) \
        + message

    server = smtplib.SMTP(app.config['EMAIL_SERVER'])
    server.sendmail(app.config['EMAIL_FROM'], app.config['EMAIL_TO'], email)
    server.quit()


## Misc

basic_phone_re = re.compile('^\\+[0-9]+$')

# TODO hack
#@app.before_request
def hack_POST_stuff():
    from werkzeug.datastructures import MultiDict
    request.form = MultiDict(request.form)
    request.form.update(request.args)


## Views

@app.route('/sms', methods=["POST"])
def sms():
    sms_from = request.form["From"]
    sms_msg = request.form["Body"]
    logger.info("SMS From {0}: {1!r}".format(sms_from, sms_msg))

    r = twiml.Response()
    return str(r)

@app.route('/call/start', methods=["POST"])
def call_start():
    call_log("Call started; from {0}".format(request.form["From"]))
    call_log("Saying 'no launches in the next three days' "
             "and offering options")

    r = twiml.Response()
    r.say("This is the Cambridge University Space Flight notam information "
            "phone number.")
    r.say("There are no launches in the next three days")
    r.pause(length=1)
    options(r)

    return str(r)

def options(r):
    g = r.gather(action=url_for("call_gathered"), timeout=30, numDigits=1)
    g.say("Press 2 to be connected to a human; otherwise "
        "please either hang up or press 1 to end the call")
    r.redirect(url_for('call_gather_timeout'))

@app.route('/call/gathered', methods=["POST"])
def call_gathered():
    d = request.form["Digits"]
    r = twiml.Response()
    if d == "1":
        call_log("Hanging up (pressed 1)")
    elif d == "2":
        call_log("Forwarding call (pressed 2)")
        r.say("Please hold. In the event that the first member called is in "
              "a lecture or otherwise busy, another will be automatically "
              "called, which could take a minute or two")
        r.redirect(url_for('call_human', index=0))
    else:
        call_log("Invalid keypress {0}; offering options".format(d))
        options(r)
    return str(r)

@app.route('/call/gather_timeout', methods=["POST"])
def call_gather_timeout():
    call_log("Timeout: no keys pressed out")
    r = twiml.Response()
    r.hangup()
    return str(r)

humans = ["+447913430431", "+447913430431", "+447913430431"]

def _dial(r, index):
    call_log("Dialing human {0} on {1}".format(index, humans[index]))

    # Make callerId be our Twilio number so people know why they're being
    # called at 7am before they pick up
    pickup_url = url_for("call_human_pickup", index=index, 
                         parent_sid=get_sid())
    d = r.dial(action=url_for("call_human_ended", index=index), 
               callerId=request.form["To"])
    d.number(humans[index], url=pickup_url)

@app.route('/call/human/<int:index>', methods=["POST"])
def call_human(index):
    r = twiml.Response()
    _dial(r, index)
    return str(r)

@app.route("/call/human/<int:index>/pickup", methods=["POST"])
def call_human_pickup(index):
    # This URL is hit before the called party is connected to the call
    # Just use it for logging
    call_log("Human {0} picked up".format(index))
    r = twiml.Response()
    return str(r)

@app.route("/call/human/<int:index>/end", methods=["POST"])
def call_human_ended(index):
    # This URL is hit when the Dial verb finishes

    status = request.form["DialCallStatus"]
    r = twiml.Response()

    if status == "completed":
        call_log("Dial (human {0}) completed successfully; hanging up"
                    .format(index))
        r.hangup()

    else:
        call_log("Dialing human {0} failed: {1}"
                    .format(index, status))

        index += 1
        if index == len(humans):
            call_log("Humans exhausted: apologising and hanging up")
            r.say("Failed to contact any members; apologies. "
                  "Please try the alternative phone number on the notam.")
            r.hangup()
        else:
            _dial(r, index)

    return str(r)

@app.route("/call/status_callback", methods=["POST"])
def call_ended():
    number = request.form["From"]
    duration = request.form["CallDuration"]
    status = request.form["CallStatus"]

    # Check that this is sane, it's going in the Subject header
    assert basic_phone_re.match(number)

    call_log("Call from {0} ended after {1} seconds with status '{2}'"
                .format(number, duration, status))
    email("Call from {0}".format(number), get_call_log())

    return "OK"

@app.route("/heartbeat")
def heartbeat():
    with cursor() as cur:
        cur.execute("SELECT TRUE")
        assert cur.fetchone()
    return "FastCGI is alive and PostgreSQL is OK"
