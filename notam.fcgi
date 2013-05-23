#!/opt/cusf-notam-twilio/venv/bin/python

from flup.server.fcgi import WSGIServer
from notam import app

if __name__ == "__main__":
    WSGIServer(app).run() #, bindAddress='/var/run/lighttpd/notam.sock').run()
