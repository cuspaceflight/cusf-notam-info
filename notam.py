import flask
from twilio import twiml
import logging
import smtplib
import time
import re
import random
from psycopg2.pool import ThreadedConnectionPool
import psycopg2.extras

from flask import request, url_for, redirect, render_template, \
                  Markup, jsonify, abort

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

def cursor(real_dict_cursor=False):
    """
    Get a postgres cursor for immediate use during a request

    If a cursor has not yet been used in this request, it connects to the
    database. Further cursors re-use the per-request connection.
    The connection is committed and closed at the end of the request.

    args and kwargs are passed to database.cursor()
    """

    assert flask.has_request_context()
    top = flask._app_ctx_stack.top
    if not hasattr(top, '_database'):
        top._database = postgres_pool.getconn()

    if real_dict_cursor:
        f = psycopg2.extras.RealDictCursor
        return top._database.cursor(cursor_factory=f)
    else:
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
    assert flask.has_request_context()

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

    query1 = "SELECT id FROM calls WHERE sid = %s"
    query2 = "INSERT INTO calls (sid) VALUES (%s) RETURNING id"
    query3 = "INSERT INTO call_log (call, time, message) " \
             "VALUES (%s, LOCALTIMESTAMP, %s)"

    with cursor() as cur:
        cur.execute(query1, (sid, ))
        if not cur.rowcount:
            cur.execute(query2, (sid, ))
        call_id = cur.fetchone()[0]
        cur.execute(query3, (call_id, db_msg))

def get_call_sid(call_id):
    query = "SELECT sid FROM calls WHERE id = %s"
    with cursor() as cur:
        cur.execute(query, (call_id, ))
        if cur.rowcount:
            return cur.fetchone()[0]
        else:
            raise ValueError("No such call_id")

def get_call_log_for_id(call_id, return_dict=False):
    query = "SELECT time, message FROM call_log " \
            "WHERE call = %s " \
            "ORDER BY time ASC, id ASC"

    with cursor(return_dict) as cur:
        cur.execute(query, (call_id, ))
        return cur.fetchall()

def get_call_log_for_sid(sid=None, return_dict=False):
    if sid is None:
        sid = get_sid()

    query = "SELECT time, message FROM call_log " \
            "WHERE call = (SELECT id FROM calls WHERE sid = %s) " \
            "ORDER BY time ASC, id ASC"

    with cursor(return_dict) as cur:
        cur.execute(query, (sid, ))
        return cur.fetchall()

def calls_count():
    query = "SELECT count(*) AS count FROM calls"

    with cursor() as cur:
        cur.execute(query)
        return cur.fetchone()[0]

def call_log_first_lines(offset=0, limit=100):
    query = "SELECT " \
            "DISTINCT ON (call) " \
            "   call, time AS first_time, " \
            "   message AS first_message " \
            "FROM call_log " \
            "ORDER BY call ASC, time ASC, id ASC " \
            "LIMIT %s OFFSET %s"

    with cursor(True) as cur:
        cur.execute(query, (limit, offset))
        return cur.fetchall()

def email(subject, message):
    logger.debug("email: {0} {1!r}".format(subject, message))

    email = "From: {0}\r\nTo: {1}\r\nSubject: CUSF Notam Twilio {2}\r\n\r\n" \
        .format(app.config['EMAIL_FROM'], ",".join(app.config['EMAIL_TO']),
                subject) \
        + message

    server = smtplib.SMTP(app.config['EMAIL_SERVER'])
    server.sendmail(app.config['EMAIL_FROM'], app.config['EMAIL_TO'], email)
    server.quit()

def humans(seed):
    query = "SELECT priority, name, phone FROM humans " \
            "WHERE priority > 0"

    with cursor() as cur:
        cur.execute(query)
        all_humans = cur.fetchall()

    rng = random.Random(seed)
    all_humans = [(priority + rng.uniform(0.1, 0.2), name, phone)
                  for (priority, name, phone) in all_humans]
    all_humans.sort()

    return all_humans

def message():
    query = "SELECT m.active_when, m.short_name, " \
            "       m.web_short_text, m.web_long_text, " \
            "       m.call_text, m.forward_to, " \
            "       h.name AS forward_name, h.phone AS forward_phone " \
            "FROM messages AS m " \
            "LEFT OUTER JOIN humans AS h ON m.forward_to = h.id " \
            "WHERE LOCALTIMESTAMP <@ active_when"

    with cursor(True) as cur:
        cur.execute(query)
        if cur.rowcount == 1:
            return cur.fetchone()
        elif cur.rowcount == 0:
            return None
        else:
            raise AssertionError("cur.rowcount should be 0 or 1")

## Misc

basic_phone_re = re.compile('^\\+[0-9]+$')


## Views

@app.route("/")
def home():
    return render_template("home.html", message=message())

@app.route("/log")
@app.route("/log/<int:page>")
def log_viewer(page=None):
    page_size = 100
    count = calls_count()

    if count == 0:
        if page is not None:
            abort(404)
        else:
            return render_template("log_viewer_empty.html")

    pages = count / page_size
    if count % page_size:
        pages += 1

    if page is None:
        return redirect(url_for('log_viewer', page=pages))

    if page > pages or page < 1:
        abort(404)

    offset = (page - 1) * page_size
    calls = call_log_first_lines(offset, page_size)

    return render_template("log_viewer.html",
                calls=calls, pages=pages, page_num=page)

@app.route("/log/call/<int:call>")
def log_viewer_call(call):
    try:
        sid = get_call_sid(call)
    except ValueError:
        abort(404)

    log = get_call_log_for_id(call, return_dict=True)
    if not log:
        abort(404)

    return render_template("log_viewer_call.html",
                page_title="Call {0}".format(call),
                call=call, sid=sid, log=log)

@app.route("/humans")
def edit_humans():
    return render_template("humans.html")

@app.route("/messages")
def edit_messages():
    return render_template("messages.html")

@app.route("/heartbeat")
def heartbeat():
    with cursor() as cur:
        cur.execute("SELECT TRUE")
        assert cur.fetchone()
    return "FastCGI is alive and PostgreSQL is OK"

@app.route('/web.json')
def web_status():
    active_message = message()
    if not active_message:
        m = "No upcoming launches in the next three days"
        return jsonify(short=m, long=m)
    else:
        return jsonify(short=active_message["web_short_text"],
                       long=active_message["web_long_text"])

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

    active_message = message()
    r = twiml.Response()

    if active_message and active_message["forward_to"]:
        name = active_message["forward_name"]
        phone = active_message["forward_phone"]

        call_log("Forwarding call straight to {0!r} on {1}"
            .format(name, phone))

        pickup_url = url_for("call_forward_pickup", parent_sid=get_sid())
        d = r.dial(action=url_for("call_forward_ended"),
                   callerId=request.form["To"])
        d.number(phone, url=url_for("call_forward_pickup"))

    else:
        # This is the information phone number for the Cambridge University
        # Spaceflight NOTAM.
        r.play(url_for('static', filename='audio/greeting.wav'))
        r.pause(length=1)

        if not active_message:
            call_log("Saying 'no launches in the next three days' "
                     "and offering options")
            # We are not planning any launches in the next three days.
            r.play(url_for('static', filename='audio/none_three_days.wav'))
        else:
            call_log("Introducing robot and saying {0!r}".format(call_text))
            # You will shortly hear an automated message detailing the
            # approximate time of an upcoming launch that we are planning.
            r.play(url_for('static', filename='audio/robot_intro.wav'))
            r.pause(length=1)
            r.say(active_message["call_text"])

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
        call_log("Trying humans (pressed 2); seed {0!r}".format(seed))
        # Forwarding. In the event that the first society member contacted is
        # in a lecture or otherwise unavailable, a second member will be
        # phoned. This could take a minute or two.
        r.play(url_for('static', filename='audio/forwarding.wav'))
        r.pause(length=1)
        # call_human(seed, 0)
        dial(r, seed, 0)

    else:
        call_log("Invalid keypress {0}; offering options".format(d))
        options(r)

    return str(r)

@app.route('/call/gather_failed', methods=["POST"])
def call_gather_failed():
    call_log("Gather failed - no keys pressed; hanging up")
    r = twiml.Response()
    r.hangup()
    return str(r)

def dial(r, seed, index):
    priority, name, phone = humans(seed)[index]

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
    dial(r, seed, index)
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
            dial(r, seed, index + 1)
        except IndexError:
            call_log("Humans exhausted: apologising and hanging up")
            # Unfortunately we failed to contact any members.
            # Please try the alternative phone number on the NOTAM
            r.play(url_for('static', filename='audio/humans_fail.wav'))
            r.pause(length=1)
            r.hangup()

    return str(r)

@app.route("/call/forward/pickup", methods=["POST"])
def call_forward_pickup():
    call_log("Forwarded call picked up")
    r = twiml.Response()
    return str(r)

@app.route("/call/forward/ended", methods=["POST"])
def call_forward_ended():
    status = request.form["DialCallStatus"]
    if status == "completed":
        call_log("Forwarded call completed successfully. Hanging up.")
    else:
        call_log("Forwarded call failed: {0}. Hanging up.".format(status))

    r = twiml.Response()
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

    fmt = lambda time, message: \
            "{0} {1}".format(time.strftime("%H:%M:%S"), message)
    lines = (fmt(time, message) for time, message in get_call_log_for_sid())
    call_log_str = "\n".join(lines)
    email("Call from {0}".format(number), call_log_str)

    return "OK"
