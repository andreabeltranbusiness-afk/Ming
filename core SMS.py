"""
MING · feature-phone channel (USSD + SMS). No camera, no smartphone, no app.

Design rules
- STATELESS dialogue: USSD gateways send the cumulative input ("1*2*1"); SMS replies repeat the path
  ("DX 1 2 1"). No server session to lose when the network drops.
- Small AI = a tiny, auditable symptom decision tree (no black box, runs anywhere).
- Fail-safe: any "not sure" or contradictory answer -> "ask a technician". Outputs say "POSSIBLE", never "is".
- No agrochemical advice, no doses. Human-in-the-loop: nothing leaves the phone/operator without the farmer's "1 = send".
- ASCII only (GSM-7 safe: accents would force 70-char Unicode SMS). USSD screen <= 182 chars, SMS <= 160.

CLI:  python core.py test | queue | stats
"""
import hashlib, os, re, sqlite3, sys, tempfile
from datetime import datetime
import numpy as np
import pandas as pd

DB = os.getenv("MING_DB", "ming_ussd.db")
SALT = os.getenv("MING_SALT", "change-me")
DEFAULT_LANG = os.getenv("MING_LANG", "en")
PRICES_CSV = os.getenv("MING_PRICES", "data/wfp_food_prices.csv")   # HDX WFP export: date,market,commodity,price[,currency]
CROP = os.getenv("MING_CROP", "coffee")
PRICE_MAX_AGE_DAYS, PRICE_MIN_POINTS = 120, 3
USSD_MAX, SMS_MAX = 182, 160
RULES_VERSION = "v0-placeholder"      # VALIDATE the tree below with an agronomist before any field use
HIGH_RISK = {"rust"}                  # queued with high priority (still only with farmer consent)

# ---------------------------------------------------------------- symptom tree (digit "0" = not sure, everywhere)
NODES = {
    "where":   {"1": "leaf", "2": "berry", "3": "R:whole", "0": "R:unsure"},
    "leaf":    {"1": "rust_c", "2": "R:miner", "3": "spot_c", "4": "R:unclear", "0": "R:unsure"},
    "rust_c":  {"1": "R:rust", "2": "R:unclear", "0": "R:unsure"},
    "spot_c":  {"1": "R:eyespot", "2": "R:unclear", "0": "R:unsure"},
    "berry":   {"1": "borer_c", "2": "R:unclear", "3": "R:unclear", "0": "R:unsure"},
    "borer_c": {"1": "R:borer", "2": "R:unclear", "0": "R:unsure"},
}
DECIDED = {"rust", "miner", "eyespot", "borer"}   # results that name a possible problem; the rest are abstentions

T = {
 "en": dict(
  menu="Ming\n1 Check my plants\n2 Check a price offer\n3 Ask a technician\n4 Language",
  q=dict(
   where="Where is the problem?\n1 Leaves\n2 Berries\n3 Branches/whole plant\n0 Not sure",
   leaf="What do you see on the leaves?\n1 Orange powder\n2 White winding trails\n3 Round brown spots\n4 Even yellowing\n0 Not sure",
   rust_c="Is the powder on the UNDERSIDE and does it rub off on your fingers?\n1 Yes\n2 No\n0 Not sure",
   spot_c="Do the spots have a grey or white centre?\n1 Yes\n2 No\n0 Not sure",
   berry="What do you see on berries?\n1 Small round hole\n2 Dark or rotten, no hole\n3 Berries drop early\n0 Not sure",
   borer_c="Is there fine brown dust near the hole?\n1 Yes\n2 No\n0 Not sure"),
  res=dict(
   rust="Possible coffee rust. NOT certain: a technician must confirm. Early reporting helps.",
   miner="Possible leaf miner. NOT certain: a technician must confirm.",
   eyespot="Possible brown eye spot (Cercospora). NOT certain: a technician must confirm.",
   borer="Possible coffee berry borer. NOT certain: a technician must confirm.",
   unclear="Not sure. The signs do not match one problem. Please ask a technician.",
   unsure="Not sure - please ask a technician. Do not apply any product without advice.",
   whole="I cannot assess branches or whole plants from here. Please ask a technician."),
  send_prompt="1 Send to technician (they may contact this number)\n0 End",
  sms_send="To send to a technician reply: DX {path}", reply="Reply: DX {path}",
  sent="Sent. A technician will contact you. Thank you.", bye="Thank you. Dial again anytime.",
  invalid="Invalid choice. Please dial again.",
  tech="Request a call from a technician?\n1 Yes (they may contact this number)\n0 No",
  lang_menu="Language\n1 English\n2 Espanol", lang_ok="Language: English.",
  p_market="Choose market:", p_offer="Buyer's offer per kg (number only):", p_reply="Reply: PRICE <n> <offer>",
  v_low="Offer is BELOW the recent reference ({lo:.2f}-{hi:.2f} {cur}/kg). Compare with other buyers before selling. Reference only.",
  v_fair="Offer is WITHIN the recent reference ({lo:.2f}-{hi:.2f} {cur}/kg). Reference only, not a guaranteed price.",
  v_high="Offer is ABOVE the recent reference ({lo:.2f}-{hi:.2f} {cur}/kg).",
  v_unsure="Not sure - not enough recent price data. Ask your cooperative or technician.",
  demo_tag=" [DEMO DATA]",
  sms_help="MING: DX = check plants | PRICE = check offer | TECH = call me | LANG EN/ES"),
 "es": dict(
  menu="Ming\n1 Revisar mis plantas\n2 Revisar oferta de precio\n3 Pedir un tecnico\n4 Idioma",
  q=dict(
   where="Donde esta el problema?\n1 Hojas\n2 Granos\n3 Ramas/toda la planta\n0 No estoy seguro",
   leaf="Que ve en las hojas?\n1 Polvo naranja\n2 Caminos blancos sinuosos\n3 Manchas marrones redondas\n4 Amarillo parejo\n0 No estoy seguro",
   rust_c="El polvo esta en el ENVES y se pega a los dedos?\n1 Si\n2 No\n0 No estoy seguro",
   spot_c="Las manchas tienen centro gris o blanco?\n1 Si\n2 No\n0 No estoy seguro",
   berry="Que ve en los granos?\n1 Agujero pequeno redondo\n2 Oscuros o podridos sin agujero\n3 Caen temprano\n0 No estoy seguro",
   borer_c="Hay polvo marron fino cerca del agujero?\n1 Si\n2 No\n0 No estoy seguro"),
  res=dict(
   rust="Posible roya del cafe. NO es seguro: un tecnico debe confirmar. Avisar temprano ayuda.",
   miner="Posible minador de la hoja. NO es seguro: un tecnico debe confirmar.",
   eyespot="Posible cercospora (mancha de hierro). NO es seguro: un tecnico debe confirmar.",
   borer="Posible broca del cafe. NO es seguro: un tecnico debe confirmar.",
   unclear="No estoy seguro. Las senales no coinciden con un solo problema. Consulte a un tecnico.",
   unsure="No estoy seguro - consulte a un tecnico. No aplique productos sin consejo.",
   whole="No puedo evaluar ramas o toda la planta desde aqui. Consulte a un tecnico."),
  send_prompt="1 Enviar a tecnico (pueden contactar este numero)\n0 Salir",
  sms_send="Para enviar a un tecnico responda: DX {path}", reply="Responda: DX {path}",
  sent="Enviado. Un tecnico le contactara. Gracias.", bye="Gracias. Marque de nuevo cuando quiera.",
  invalid="Opcion invalida. Marque de nuevo.",
  tech="Pedir llamada de un tecnico?\n1 Si (pueden contactar este numero)\n0 No",
  lang_menu="Idioma\n1 English\n2 Espanol", lang_ok="Idioma: Espanol.",
  p_market="Elija mercado:", p_offer="Oferta del comprador por kg (solo numero):", p_reply="Responda: PRICE <n> <oferta>",
  v_low="Oferta POR DEBAJO de la referencia reciente ({lo:.2f}-{hi:.2f} {cur}/kg). Compare con otros compradores antes de vender. Solo referencia.",
  v_fair="Oferta DENTRO de la referencia reciente ({lo:.2f}-{hi:.2f} {cur}/kg). Solo referencia, no precio garantizado.",
  v_high="Oferta POR ENCIMA de la referencia reciente ({lo:.2f}-{hi:.2f} {cur}/kg).",
  v_unsure="No estoy seguro - faltan datos de precios recientes. Consulte a su cooperativa o tecnico.",
  demo_tag=" [DATOS DEMO]",
  sms_help="MING: DX = revisar plantas | PRICE = revisar oferta | TECH = llamenme | LANG EN/ES"),
}

# ---------------------------------------------------------------- storage (SQLite: prefs, consented escalations, decision log)
class Store:
    def __init__(self, path=DB):
        self.path, self._prices = path, None
        self._run("CREATE TABLE IF NOT EXISTS prefs(h TEXT PRIMARY KEY, lang TEXT)")
        self._run("""CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, phone TEXT,
                     channel TEXT, kind TEXT, priority TEXT, payload TEXT, status TEXT DEFAULT 'pending')""")
        self._run("CREATE TABLE IF NOT EXISTS decisions(ts TEXT, channel TEXT, kind TEXT, decided INTEGER)")  # no phone, no PII

    def _run(self, sql, args=(), fetch=False):
        con = sqlite3.connect(self.path)
        try:
            rows = con.execute(sql, args).fetchall() if fetch else (con.execute(sql, args) and None)
            con.commit(); return rows
        finally:
            con.close()

    @staticmethod
    def _h(phone): return hashlib.sha256((SALT + phone).encode()).hexdigest()[:12]
    def get_lang(self, phone):
        r = self._run("SELECT lang FROM prefs WHERE h=?", (self._h(phone),), True)
        return r[0][0] if r else DEFAULT_LANG
    def set_lang(self, phone, lang): self._run("INSERT OR REPLACE INTO prefs VALUES(?,?)", (self._h(phone), lang))
    def log(self, ch, kind, decided):
        self._run("INSERT INTO decisions VALUES(?,?,?,?)", (_now(), ch, kind, int(decided)))
    def enqueue(self, phone, ch, kind, priority, payload):   # raw phone stored ONLY here, after explicit "1 = send"
        self._run("INSERT INTO outbox(ts,phone,channel,kind,priority,payload) VALUES(?,?,?,?,?,?)",
                  (_now(), phone, ch, kind, priority, str(payload)))
    def queue(self, status="pending"):
        return self._run("SELECT id,ts,phone,channel,kind,priority,payload,status FROM outbox WHERE status=? "
                         "ORDER BY priority='high' DESC, id", (status,), True)
    def resolve(self, i): self._run("UPDATE outbox SET status='resolved' WHERE id=?", (i,))

    def prices(self):
        if self._prices is None:
            if os.path.exists(PRICES_CSV):
                d, syn = pd.read_csv(PRICES_CSV, parse_dates=["date"]), False
                if "currency" not in d: d["currency"] = "LCU"
            else:   # SYNTHETIC demo data
                rng = np.random.default_rng(1)
                months = pd.date_range(end=pd.Timestamp.today().normalize().replace(day=1), periods=12, freq="MS")
                d = pd.DataFrame([(m, mk, "Coffee (dry parchment)", round(float(7.5 + b + .6 * np.sin(i / 2) + rng.normal(0, .25)), 2), "LCU")
                                  for mk, b in [("Market A", 0), ("Market B", -.4), ("Market C", .3)] for i, m in enumerate(months)],
                                 columns=["date", "market", "commodity", "price", "currency"])
                syn = True
            self._prices = (d[d.commodity.str.lower().str.contains(CROP.lower())], syn)
        return self._prices
    def markets(self): return sorted(self.prices()[0].market.unique())[:5]
    def fair(self, market):
        d = self.prices()[0]; s = d[d.market == market].sort_values("date")
        if len(s) < PRICE_MIN_POINTS: return None
        s = s.tail(6)
        if (pd.Timestamp.today() - s.date.max()).days > PRICE_MAX_AGE_DAYS: return None   # stale -> abstain
        return dict(lo=float(s.price.quantile(.25)), hi=float(s.price.quantile(.75)), cur=str(s.currency.iloc[-1]))

def _now(): return datetime.now().isoformat(timespec="seconds")
def _flat(x): return re.sub(r"\s*\n\s*", " ", x)

# ---------------------------------------------------------------- dialogue engine
def _walk(a):
    node = "where"
    for i, x in enumerate(a):
        nxt = NODES[node].get(x)
        if nxt is None: return ("invalid",)
        if nxt.startswith("R:"): return ("r", nxt[2:], a[i + 1:], a[:i + 1])
        node = nxt
    return ("q", node)

def _dx(a, t, phone, store, ch):
    w = _walk(a)
    if w[0] == "invalid": return t["invalid"], True
    if w[0] == "q":
        txt = t["q"][w[1]]
        return (_flat(txt) + " " + t["reply"].format(path=" ".join(list(a) + ["<n>"])) if ch == "sms" else txt), False
    _, code, rest, path = w
    if rest and rest[0] == "1":      # explicit consent to contact a human
        store.enqueue(phone, ch, "phyto_query", "high" if code in HIGH_RISK else "normal",
                      dict(rules=RULES_VERSION, path=path, result=code))
        return t["sent"], True
    if rest: return t["bye"], True
    if not rest: store.log(ch, "diagnosis", code in DECIDED)
    if ch == "ussd": return t["res"][code] + "\n" + t["send_prompt"], False
    return t["res"][code] + " " + t["sms_send"].format(path=" ".join(path + ["1"])), False

def _price(a, t, store, ch):
    mk = store.markets()
    if not mk: return t["v_unsure"], True
    if not a:
        lst = "\n".join(f"{i + 1} {m[:14]}" for i, m in enumerate(mk))
        return (_flat(t["p_market"] + "\n" + lst) + " " + t["p_reply"] if ch == "sms" else t["p_market"] + "\n" + lst), False
    try: m = mk[int(a[0]) - 1]; assert int(a[0]) >= 1
    except Exception: return t["invalid"], True
    if len(a) == 1: return (t["p_reply"] if ch == "sms" else t["p_offer"]), False
    try: offer = float(a[1].replace(",", ".")); assert 0 < offer < 100000
    except Exception: return t["invalid"], True
    fp = store.fair(m)
    key = "unsure" if fp is None else ("low" if offer < fp["lo"] else "fair" if offer <= fp["hi"] else "high")
    msg = t["v_" + key].format(**fp) if fp else t["v_unsure"]
    if store.prices()[1]: msg += t["demo_tag"]
    store.log(ch, "price", key != "unsure")
    return msg, True

def handle_ussd(text, phone, store):
    """text = cumulative input, e.g. '' / '1' / '1*2*1'. Returns (message, session_ended)."""
    t = T[store.get_lang(phone)]
    a = text.split("*") if text else []
    if not a: return t["menu"], False
    top, rest = a[0], a[1:]
    if top == "1": return _dx(rest, t, phone, store, "ussd")
    if top == "2": return _price(rest, t, store, "ussd")
    if top == "3":
        if not rest: return t["tech"], False
        if rest[0] == "1": store.enqueue(phone, "ussd", "callback", "normal", {}); return t["sent"], True
        return t["bye"], True
    if top == "4":
        if not rest: return t["lang_menu"], False
        if rest[0] in ("1", "2"):
            lang = "en" if rest[0] == "1" else "es"; store.set_lang(phone, lang); return T[lang]["lang_ok"], True
    return t["invalid"], True

def handle_sms(body, phone, store):
    """Commands: DX [answers..] | PRICE [n] [offer] | TECH | LANG EN/ES | HELP. Returns reply (<=160 chars)."""
    w = body.strip().upper().split(); cmd, args = (w[0], w[1:]) if w else ("HELP", [])
    t = T[store.get_lang(phone)]
    if cmd == "DX": msg, _ = _dx(args, t, phone, store, "sms")
    elif cmd == "PRICE": msg, _ = _price(args, t, store, "sms")
    elif cmd == "TECH": store.enqueue(phone, "sms", "callback", "normal", {}); msg = t["sent"]
    elif cmd == "LANG" and args and args[0] in ("EN", "ES"):
        lang = args[0].lower(); store.set_lang(phone, lang); msg = T[lang]["lang_ok"]
    else: msg = t["sms_help"]
    return _flat(msg)

# ---------------------------------------------------------------- self-test (length, ASCII, tree integrity)
def _all_paths(node="where", pre=()):
    for a, n in NODES[node].items():
        if n.startswith("R:"): yield list(pre) + [a]
        else: yield from _all_paths(n, pre + (a,))

def selftest():
    s = Store(os.path.join(tempfile.mkdtemp(), "t.db")); worst = {"ussd": 0, "sms": 0}; n = 0
    for lang in T:
        t = T[lang]
        assert set(t["q"]) == {k for k in NODES}, "every node needs a question text"
        assert set(t["res"]) >= {v[2:] for d in NODES.values() for v in d.values() if v.startswith("R:")}
        for p in _all_paths():
            for k in range(len(p) + 1):
                for ch, lim in (("ussd", USSD_MAX), ("sms", SMS_MAX)):
                    txt, _ = _dx(p[:k], t, "+1", s, ch); txt = _flat(txt) if ch == "sms" else txt
                    assert len(txt) <= lim and txt.isascii(), (lang, ch, p[:k], len(txt))
                    worst[ch] = max(worst[ch], len(txt)); n += 1
        for key in ("v_low", "v_fair", "v_high"):
            m = t[key].format(lo=99999.99, hi=99999.99, cur="LCU") + t["demo_tag"]
            assert len(m) <= SMS_MAX and m.isascii(), (lang, key, len(m)); worst["sms"] = max(worst["sms"], len(m))
        for k in ("menu", "tech", "lang_menu", "v_unsure", "sms_help", "p_reply"): assert t[k].isascii() and len(t[k]) <= USSD_MAX
    print(f"OK: {n} screens checked. Longest USSD={worst['ussd']}/{USSD_MAX}, longest SMS={worst['sms']}/{SMS_MAX}")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"
    if cmd == "test": selftest()
    elif cmd == "queue":
        for r in Store().queue(): print(r)
    elif cmd == "stats":
        r = Store()._run("SELECT kind, COUNT(*), SUM(decided) FROM decisions GROUP BY kind", fetch=True)
        for k, c, d in r: print(f"{k}: {c} requests, answered {d / c:.0%}, abstained/escalated {1 - d / c:.0%}")
