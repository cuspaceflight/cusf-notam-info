import flask
from twilio import twiml
import logging
import smtplib
import time
import re
import random
import datetime
from psycopg2.pool import ThreadedConnectionPool
import psycopg2.extras

from flask import request, url_for, redirect, render_template, \
                  Markup, jsonify, abort, flash, session

app = flask.Flask(__name__)


## PostgreSQL

postgres_pool = None

@app.before_first_request
def setup_postgres_pool():
    """Initialise the postgres connection pool"""

    # Happens "before_first_request" rather than at module init since
    # app.config could change

    global postgres_pool
    postgres_pool = ThreadedConnectionPool(1, 10, app.config["POSTGRES"])

def connection():
    """
    Get a connection to use in this request

    If no connection has been used in this request, it connects to the
    database. Further calls to connection() in this request context will
    get the same connection.

    The connection is committed and closed at the end of the request.
    """

    assert flask.has_request_context()
    top = flask._app_ctx_stack.top
    if not hasattr(top, '_database'):
        top._database = postgres_pool.getconn()
    return top._database

def cursor(real_dict_cursor=False):
    """
    Get a postgres cursor for immediate use during a request

    If a cursor has not yet been used in this request, it connects to the
    database. Further cursors re-use the per-request connection.

    The connection is committed and closed at the end of the request.

    If real_dict_cursor is set, a psycopg2.extras.RealDictCursor is returned
    """

    if real_dict_cursor:
        f = psycopg2.extras.RealDictCursor
        return connection().cursor(cursor_factory=f)
    else:
        return connection().cursor()

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
    """Get the active call SID from the request, using parent_sid if present"""

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
    call_logger.info("%s %s", sid, message)

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
    """Get the call SID for a call id"""

    query = "SELECT sid FROM calls WHERE id = %s"
    with cursor() as cur:
        cur.execute(query, (call_id, ))
        if cur.rowcount:
            return cur.fetchone()[0]
        else:
            raise ValueError("No such call_id")

def get_call_log_for_id(call_id, return_dicts=False):
    """
    Get the whole call log for a given call id

    If return_dicts is false, a list of (time, message) tuples is returned;
    if true {"time": time, "message": message} dicts.
    """

    query = "SELECT time, message FROM call_log " \
            "WHERE call = %s " \
            "ORDER BY time ASC, id ASC"

    with cursor(return_dicts) as cur:
        cur.execute(query, (call_id, ))
        return cur.fetchall()

def get_call_log_for_sid(sid=None, return_dicts=False):
    """
    Get the whole call log for a given call SID

    If no SID is specified, it will use the SID from the current request.

    If return_dicts is false, a list of (time, message) tuples is returned;
    if true {"time": time, "message": message} dicts.
    """

    if sid is None:
        sid = get_sid()

    query = "SELECT time, message FROM call_log " \
            "WHERE call = (SELECT id FROM calls WHERE sid = %s) " \
            "ORDER BY time ASC, id ASC"

    with cursor(return_dicts) as cur:
        cur.execute(query, (sid, ))
        return cur.fetchall()

def calls_count():
    """Count the rows in the calls table"""

    query = "SELECT COUNT(*) AS count FROM calls"

    with cursor() as cur:
        cur.execute(query)
        return cur.fetchone()[0]

def call_log_first_lines(offset=0, limit=100):
    """
    Get a list of calls and for each, their first lines in the call log

    A list of {"call": call_id, "first_time": time, "first_message": message}
    dicts is returned.
    """
    # assumes entries in the calls table have at least one line in the log

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
    """Send an email"""

    logger.debug("email: %s %r", subject, message)

    email = "From: {0}\r\nTo: {1}\r\nSubject: CUSF Notam Twilio {2}\r\n\r\n" \
        .format(app.config['EMAIL_FROM'], ",".join(app.config['EMAIL_TO']),
                subject) \
        + message

    server = smtplib.SMTP(app.config['EMAIL_SERVER'])
    server.sendmail(app.config['EMAIL_FROM'], app.config['EMAIL_TO'], email)
    server.quit()


## Other database queries

def all_humans():
    """
    Get all humans, sorted by priority then name

    A list of {"id": id, "name": name, "phone": phone, "priority": priority}
    dicts is returned.
    """

    query = "SELECT id, name, phone, priority FROM humans " \
            "ORDER BY priority ASC, name ASC " \

    # put disabled humans at the end. priority is a smallint, so...
    key = lambda h: 100000 if h["priority"] == 0 else h["priority"]

    with cursor(True) as cur:
        cur.execute(query)
        humans = cur.fetchall()
        humans.sort(key=key)
        return humans

def update_human_priority(human_id, new_priority):
    """Update the priority column of a single human"""
    query = "UPDATE humans SET priority = %s WHERE id = %s"
    with cursor() as cur:
        cur.execute(query, (new_priority, human_id))

def add_human(name, phone, priority):
    """Add a human"""
    query = "INSERT INTO humans (name, phone, priority) VALUES (%s, %s, %s)"
    with cursor() as cur:
        cur.execute(query, (name, phone, priority))

def shuffled_humans(seed):
    """
    Get all humans, sorted by priority.

    Humans with equal priorities are shuffled randomly, with an RNG
    seeded with seed.

    Returns a list of (priority, name, phone) tuples.
    """

    query = "SELECT priority, name, phone FROM humans " \
            "WHERE priority > 0 ORDER BY id ASC"

    with cursor() as cur:
        cur.execute(query)
        humans = cur.fetchall()

    rng = random.Random(seed)
    humans = [(priority + rng.uniform(0.1, 0.2), name, phone)
              for (priority, name, phone) in humans]
    humans.sort()

    return humans

_message_query = "SELECT m.id, m.active_when, m.short_name, " \
                 "       m.web_short_text, m.web_long_text, " \
                 "       m.call_text, m.forward_to, " \
                 "       h.name AS forward_name, h.phone AS forward_phone, " \
                 "       LOCALTIMESTAMP <@ active_when AS active " \
                 "FROM messages AS m " \
                 "LEFT OUTER JOIN humans AS h ON m.forward_to = h.id "

def active_message():
    """
    Get the active message, if it exists.

    Returns a {"id": i, "active_when": a, "short_name": s,
    "web_short_text": wst, "web_long_text": wlt, "call_text": ct,
    "forward_to": human_id, "forward_name": human_name,
    "forward_phone": human_phone, "active": bool} dict,
    or None if there isn't an active message.
    """

    query = _message_query + \
            "WHERE LOCALTIMESTAMP <@ active_when"

    with cursor(True) as cur:
        cur.execute(query)
        if cur.rowcount == 1:
            return cur.fetchone()
        elif cur.rowcount == 0:
            return None
        else:
            raise AssertionError("cur.rowcount should be 0 or 1")

def future_messages():
    """
    Get all messages that are active now or will be active in the future

    Returns a list of messages in the same form as active_message()
    """

    query = _message_query + \
            "WHERE TSRANGE(LOCALTIMESTAMP, NULL) && active_when " \
            "ORDER BY active_when"

    # intersecting with [localtimestamp, infinity) uses the index;
    # LOCALTIMESTAMP < UPPER(active_when) does not.

    with cursor(True) as cur:
        cur.execute(query)
        return cur.fetchall()

def get_message(message_id):
    """
    Gets a specific message by its id

    Returns a dict in the same form as active_message()
    """

    query = _message_query + \
            "WHERE m.id = %s"

    with cursor(True) as cur:
        cur.execute(query, (message_id, ))
        return cur.fetchone()

def do_delete_message(message_id):
    """Delete message by id"""
    query = "DELETE FROM messages WHERE id = %s"
    with cursor() as cur:
        cur.execute(query, (message_id, ))


## Misc

basic_phone_re = re.compile('^\\+[0-9]+$')

@app.template_global('csrf_token')
def csrf_token():
    """
    Return the CSRF token for the current session

    If not already generated, session["_csrf_token"] is set to a random
    string. The token is used for the whole life of the session.
    """

    if "_csrf_token" not in session:
        session["_csrf_token"] = hex(random.getrandbits(64))
    return session["_csrf_token"]

@app.template_global('csrf_token_input')
def csrf_token_input():
    """Returns a hidden input element with the CSRF token"""
    return Markup('<input type="hidden" name="_csrf_token" value="{0}">') \
            .format(csrf_token())

def check_csrf_token():
    """Checks that request.form["_csrf_token"] is correct, aborting if not"""
    if "_csrf_token" not in request.form:
        logger.warning("Expected CSRF Token: not present")
        abort(400)
    if request.form["_csrf_token"] != csrf_token():
        logger.warning("CSRF Token incorrect")
        abort(400)

@app.before_request
def auto_check_csrf():
    """Automatically check the CSRF token whenever data is POSTed"""
    if request.form:
        check_csrf_token()

@app.template_global('show_which_pages')
def show_which_pages(page, pages, show=5):
    """
    Work out which page numbers to display

    Pages are numbered 1 to pages, inclusive.

    page: the current page
    pages: the total number of pages.
    show: maximum number of pages to show

    show must be odd.
    """

    assert show % 2 == 1

    if pages < show:
        return range(1, pages + 1)
    elif page <= (show // 2):
        return range(1, show + 1)
    elif page > pages - (show // 2):
        return range(pages - show + 1, pages + 1)
    else:
        return range(page - (show // 2), page + (show // 2) + 1)

@app.template_global('show_which_pages_responsive')
def show_which_pages_responsive(page, pages, phone=5, tablet=7, desktop=11):
    """
    Run show_which_pages for multiple show sizes

    For each type (phone, tablet, desktop) calls
    show_which_pages(page, pages, type).
    It then takes the unions of all the page numbers returned, and builds
    (and returns) a list of (page_number, class) tuples,
    where class is 'hidden-tablet' or 'hidden-phone hidden-tablet' as
    appropriate.

    Need phone <= tablet <= desktop.
    """

    if not phone <= tablet <= desktop:
        raise ValueError("Need phone <= tablet <= desktop")

    all_pages = show_which_pages(page, pages, desktop)
    tablet_pages = set(show_which_pages(page, pages, tablet))
    phone_pages = set(show_which_pages(page, pages, phone))

    # phone_pages < tablet_pages < all_pages
    assert tablet_pages - set(all_pages) == set()
    assert phone_pages - tablet_pages == set()

    def page_class(page):
        if page in phone_pages:
            return ''
        elif page in tablet_pages:
            return 'hidden-phone'
        else:
            return 'hidden-phone hidden-tablet'

    return [(page, page_class(page)) for page in all_pages]

## Views

@app.route("/")
def home():
    return render_template("home.html", message=active_message())

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
        return redirect(url_for(request.endpoint, page=pages))

    if page > pages or page < 1:
        abort(404)

    offset = (page - 1) * page_size
    calls = call_log_first_lines(offset, page_size)

    return render_template("log_viewer.html",
                calls=calls, pages=pages, page=page)

@app.route("/log/call/<int:call>")
def log_viewer_call(call):
    try:
        sid = get_call_sid(call)
    except ValueError:
        abort(404)

    log = get_call_log_for_id(call, return_dicts=True)
    if not log:
        abort(404)

    return render_template("log_viewer_call.html", call=call, sid=sid, log=log,
                           return_to=request.args.get("return_to", None))

@app.route("/humans", methods=["GET", "POST"])
def edit_humans():
    # if the update succeeds, redirect so that the method becomes GET and
    # the refresh button works as espected.
    # flask handles the message flashing and the template fills out the form
    # with either current or failed-to-update values.

    # note that the update/insert queries will have been the first in this
    # request's transaction, so the rollback doesn't hit anything unexpected

    if request.form.get("edit_priorities", False):
        changed = 0

        try:
            for human in all_humans():
                field_name = "priority_{0}".format(human["id"])
                new_priority = int(request.form[field_name])
                if human["priority"] != new_priority:
                    update_human_priority(human["id"], new_priority)
                    changed += 1

        except psycopg2.DataError:
            flash('Priority must be in range 0-32767', 'error')
            connection().rollback()

        else:
            if changed:
                if changed == 1:
                    flash('Priority updated', 'success')
                else:
                    flash('{0} priorities updated'.format(changed), 'success')
            else:
                flash('No priorioties changed', 'warning')

            return redirect(url_for(request.endpoint))

    elif request.form.get("add_human", False):
        name = request.form["name"]
        phone = request.form["phone"]
        priority = int(request.form["priority"])

        try:
            add_human(name, phone, priority)
        except psycopg2.IntegrityError:
            flash('Invalid data: name and phone must be nonempty, unique, '
                  'phone of form "+NNNNNNNNNNNN"', 'error')
            connection().rollback()
        except psycopg2.DataError:
            flash('Priority must be in range 0-32767', 'error')
            connection().rollback()
        else:
            flash('Human added', 'success')
            return redirect(url_for(request.endpoint))

    humans = all_humans()

    priorities = set(h["priority"] for h in humans)

    try:
        priorities.remove(0)
    except KeyError:
        pass

    lowest_priorities = sorted(priorities)[:2]
    while len(lowest_priorities) < 2:
        lowest_priorities.append(None)

    return render_template("humans.html",
            humans=humans,
            lowest_priorities=lowest_priorities)

@app.route("/messages")
def list_messages():
    messages = future_messages()

    last_upper = None
    for message in messages:
        if last_upper is None: # first message
            message["gap_preceeding"] = not message["active"]
        else:
            message["gap_preceeding"] = \
                    last_upper != message["active_when"].lower

        last_upper = message["active_when"].upper

    return render_template("message_list.html", messages=messages,
            page=page, pages=pages)

@app.route("/messages/new")
@app.route("/messages/<int:message_id>/edit")
def edit_message(message_id=None):
    if message_id is None:
        default_date = datetime.datetime.today() \
                .replace(hour=0, minute=0, second=0, microsecond=0)
        active_when = {"lower": default_date, "upper": default_date}
        message = {"id": None, "active_when": active_when}
    else:
        message = get_message(message_id)
        if message is None:
            abort(404)

    return render_template("message_edit.html", **message)

@app.route("/messages/<int:message>/delete", methods=["POST"])
def delete_message(message):
    check_csrf_token()
    do_delete_message(message)
    flash("Message deleted", "success")
    return redirect(url_for('list_messages'))

@app.route("/heartbeat")
def heartbeat():
    with cursor() as cur:
        cur.execute("SELECT TRUE")
        assert cur.fetchone()
    return "FastCGI is alive and PostgreSQL is OK"

@app.route('/web.json')
def web_status():
    message = active_message()
    if not message:
        m = "No upcoming launches in the next three days"
        return jsonify(short=m, long=m)
    else:
        return jsonify(short=message["web_short_text"],
                       long=message["web_long_text"])

@app.route('/sms', methods=["POST"])
def sms():
    sms_from = request.form["From"]
    sms_msg = request.form["Body"]
    logger.info("SMS From %s: %r", sms_from, sms_msg)

    r = twiml.Response()
    return str(r)

@app.route('/call/start', methods=["POST"])
def call_start():
    call_log("Call started; from {0}".format(request.form["From"]))

    message = active_message()
    r = twiml.Response()

    if message and message["forward_to"]:
        name = message["forward_name"]
        phone = message["forward_phone"]

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

        if not message:
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
            r.say(message["call_text"])

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
    priority, name, phone = shuffled_humans(seed)[index]

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
