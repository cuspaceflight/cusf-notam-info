import flask
from twilio import twiml
import logging
import smtplib
import time
import re
import random
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

    r = twiml.Response()
    # This is the information phone number for the Cambridge University
    # Spaceflight NOTAM.
    r.play(url_for('static', filename='audio/greeting.wav'))
    r.pause(length=1)

    if False:
        call_log("Default: saying 'no launches in the next three days' "
                 "and offering options")
        # We are not planning any launches in the next three days.
        r.play(url_for('static', filename='audio/none_three_days.wav'))
    else:
        message = "There will be a launch on Saturday the 8th " \
                  "between 8am and 3pm"

        call_log("Introducing robot and saying {0!r}".format(message))
        # You will shortly hear an automated message detailing the
        # approximate time of an upcoming launch that we are planning.
        r.play(url_for('static', filename='audio/robot_intro.wav'))
        r.pause(length=1)
        r.say(message)

    r.pause(length=1)
    options(r)

    return str(r)

def options(r):
    g = r.gather(action=url_for("call_gathered"), timeout=30, numDigits=1)
    # Hopefully this automated message has answered your question, but if not,
    # please press 2 to be forwarded to a human. Otherwise, either hang up or
    # press 1 to end the call.
    g.play(url_for('static', filename='audio/options.wav'))
    r.redirect(url_for('call_gather_failed'))

@app.route('/call/gathered', methods=["POST"])
def call_gathered():
    d = request.form["Digits"]
    r = twiml.Response()
    if d == "1":
        call_log("Hanging up (pressed 1)")
    elif d == "2":
        seed = random.getrandbits(32)
        call_log("Forwarding call (pressed 2); seed {0!r}".format(seed))
        # Forwarding. In the event that the first society member contacted is
        # in a lecture or otherwise unavailable, a second member will be
        # phoned. This could take a minute or two.
        r.play(url_for('static', filename='audio/forwarding.wav'))
        r.pause(length=1)
        r.redirect(url_for('call_human', seed=seed, index=0))
    else:
        call_log("Invalid keypress {0}; offering options".format(d))
        options(r)
    return str(r)

@app.route('/call/gather_failed', methods=["POST"])
def call_gather_faield():
    call_log("Gather failed - no keys pressed; hanging up")
    r = twiml.Response()
    r.hangup()
    return str(r)

def _humans(seed):
    query = "SELECT priority, name, phone FROM humans " \
            "WHERE priority > 0"

    with cursor() as cur:
        cur.execute(query)
        humans = cur.fetchall()

    rng = random.Random(seed)
    humans = [(priority + rng.uniform(0.1, 0.2), name, phone)
              for (priority, name, phone) in humans]
    humans.sort()

    return humans

def _dial(r, seed, index):
    priority, name, phone = _humans()[index]

    call_log("Attempt {0}: {1!r} on {2}".format(index, name, phone))

    # Make callerId be our Twilio number so people know why they're being
    # called at 7am before they pick up
    pickup_url = url_for("call_human_pickup", seed=seed, index=index, 
                         parent_sid=get_sid())
    d = r.dial(action=url_for("call_human_ended", seed=seed, index=index),
               callerId=request.form["To"])
    d.number(phone, url=pickup_url)

@app.route('/call/human/<int:seed>/<int:index>', methods=["POST"])
def call_human(seed, index):
    r = twiml.Response()
    _dial(r, seed, index)
    return str(r)

@app.route("/call/human/<int:seed>/<int:index>/pickup", methods=["POST"])
def call_human_pickup(seed, index):
    # This URL is hit before the called party is connected to the call
    # Just use it for logging
    call_log("Human (attempt {0}) picked up".format(index))
    r = twiml.Response()
    return str(r)

@app.route("/call/human/<int:seed>/<int:index>/end", methods=["POST"])
def call_human_ended(seed, index):
    # This URL is hit when the Dial verb finishes

    status = request.form["DialCallStatus"]
    r = twiml.Response()

    if status == "completed":
        call_log("Dial (attempt {0}) completed successfully; hanging up"
                    .format(index))
        r.hangup()

    else:
        call_log("Dialing human (attempt {0}) failed: {1}"
                    .format(index, status))

        try:
            _dial(r, seed, index + 1)
        except IndexError:
            call_log("Humans exhausted: apologising and hanging up")
            # Unfortunately we failed to contact any members.
            # Please try the alternative phone number on the NOTAM
            r.play(url_for('static', filename='audio/humans_fail.wav'))
            r.pause(length=1)
            r.hangup()

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
