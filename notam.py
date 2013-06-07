from flask import Flask, request
from twilio import twiml
import logging
import smtplib

app = Flask(__name__)
lang = {}

logger = logging.getLogger("notam")

_url = lambda path: app.config["URL"].format(path)

def call_log(message):
    sid = request.form["CallSid"]
    number = request.form["From"]
    logger.info("call ({0}, {1}) {2}".format(sid, number, message))

def email(subject, message):
    logger.debug("email: {0} {1!r}".format(subject, message))

    fromaddr = "cusf-notam-twilio@yocto.danielrichman.co.uk"
    toaddr = "main@danielrichman.co.uk"
    headers = "From: {0}\nTo: {1}\nSubject: CUSF Notam Twilio {2}\n\n"
    email = headers.format(fromaddr, toaddr, subject) + message
    email = email.replace("\n", "\r\n")

    server = smtplib.SMTP('localhost')
    server.sendmail(fromaddr, [toaddr], email)
    server.quit()

@app.route('/sms', methods=["POST"])
def sms():
    sms_from = request.form["From"]
    sms_msg = request.form["Body"]
    logger.info("SMS From {0}: {1!r}".format(sms_from, sms_msg))
    r = twiml.Response()
    return str(r)

@app.route('/call', methods=["POST"])
def call():
    call_log("started")
    r = twiml.Response()
    r.say("This is the Cambridge University Space Flight notam information "
            "phone number.", **lang)
    r.say("There are no launches in the next three days", **lang)
    r.pause(length=1)
    options(r)
    # timeout:
    r.hangup()
    return str(r)

def options(r):
    g = r.gather(action=URL.format("call_option"), timeout=30, numDigits=1)
    g.say("Press 2 to be connected to a human; otherwise "
        "please either hang up or press 1 to end the call", **lang)

@app.route('/call_option', methods=["POST"])
def call_option():
    d = request.form["Digits"]
    r = twiml.Response()
    if d == "1":
        call_log("ended by option")
    elif d == "2":
        call_log("forwarding call")
        r.say("Connecting")
        r.dial("+447913430431", action=URL.format("dialed"))
    else:
        options(r)
    return str(r)

@app.route("/dialed", methods=["POST"])
def dialed():
    s = request.form["DialCallStatus"]
    r = twiml.Response()
    if s == "completed":
        call_log("call completed")
        r.hangup()
    else:
        call_log("call failed: {0}".format(s))
        r.say("Call failed. Please try the alternative phone number on "
                "the notam.", **lang)
    return str(r)

@app.route("/call_ended", methods=["POST"])
def call_ended():
    status = request.form["CallStatus"]
    duration = request.form["CallDuration"]
    number = request.form["From"]
    call_log("status callback {0}; {1} seconds".format(status, duration))
    email("Call", "call from {0} completed after {1} seconds"
            .format(number, duration))
    r = twiml.Response()
    r.hangup()
    return str(r)

@app.route("/heartbeat")
def heartbeat():
    return "FastCGI is alive"
