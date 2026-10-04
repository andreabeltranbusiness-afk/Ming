"""
Gateway webhook. Run:  MING_SALT=<random secret> flask --app server run --port 5000

USSD (Africa's Talking-style form fields: sessionId, serviceCode, phoneNumber, text)
  -> reply must be plain text starting with "CON " (session continues) or "END " (session closes).
SMS  (Twilio-style: From, Body) -> TwiML reply. For Africa's Talking SMS, read `from`/`text` and send the
  reply through their send-SMS API instead. Adapt only this file; core.py is gateway-agnostic.
Keep the endpoint behind HTTPS and the gateway's IP allow-list / signature check in production.
"""
from xml.sax.saxutils import escape
from flask import Flask, Response, request
from core import Store, handle_sms, handle_ussd

app = Flask(__name__)
store = Store()

@app.post("/ussd")
def ussd():
    f = request.form
    msg, end = handle_ussd(f.get("text", ""), f.get("phoneNumber", ""), store)
    return Response(("END " if end else "CON ") + msg, mimetype="text/plain")

@app.post("/sms")
def sms():
    reply = handle_sms(request.form.get("Body", ""), request.form.get("From", ""), store)
    return Response(f"<Response><Message>{escape(reply)}</Message></Response>", mimetype="text/xml")

@app.get("/health")
def health():
    return {"ok": True}
