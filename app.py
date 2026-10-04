"""
MÍNG · Small AI for smallholder farmers (hackathon prototype)
Run:  pip install -r requirements.txt && streamlit run app.py

Principles: offline-first · no farmer registry (parcel = local random code) ·
human-in-the-loop · fail-safe (abstain instead of guess) for BOTH diagnosis and prices.
"""
import html, json, os, sqlite3, time, uuid
from datetime import datetime
import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from PIL import Image

DB = "ming_local.db"
MODEL_PATH = "models/coffee_leaf_int8.tflite"      # real model (optional); see README plan
LABELS = ["healthy", "rust", "leaf_miner", "brown_leaf_spot", "cercospora"]   # BRACOL classes
PRICES_CSV = "data/wfp_food_prices.csv"            # HDX WFP export: date,market,commodity,price,currency

# ---- Safety thresholds (calibrate on a held-out FIELD set, not studio images) ----
MIN_CONF, MIN_MARGIN = 0.75, 0.25
MIN_SHARPNESS, MIN_BRIGHT, MAX_BRIGHT = 40.0, 50, 215
HIGH_RISK = {"rust"}                # economically critical -> always recommend human confirmation
PRICE_MAX_AGE_DAYS, PRICE_MIN_POINTS = 120, 3

T = {
 "en": dict(lang_tag="en-US", title="Míng — Parcel assistant", signal="📶 I have signal",
  parcel="Parcel code", noreg="No name or ID required.", tab1="🍃 My leaf", tab2="💰 Fair price", tab3="📤 Pending",
  tip="Photograph ONE leaf, good light, no hand shadow.", cam="Photo", up="…or upload a photo",
  demo="DEMO MODE: no trained model in /models. Results are illustrative only.",
  looks="Looks like", conf="confidence", unsure="🤔 I'm not sure. Please ask an extension worker before acting.",
  retake="Retake the photo: closer, more light, hold still.", model_guess="Model's guess (NOT confirmed)",
  ask="📨 Ask a technician", saved="Saved on this phone. It will be sent when there is signal.",
  market="Nearest market", product="Crop", offer="Price the buyer offers (per kg)", compare="Compare",
  sms_note="Short text ready for SMS / WhatsApp.", listen="Listen",
  v_low="This offer is BELOW the recent reference range ({lo:.2f}–{hi:.2f} {cur}/kg). Compare with other buyers before selling.",
  v_fair="This offer is within the recent reference range ({lo:.2f}–{hi:.2f} {cur}/kg).",
  v_high="This offer is ABOVE the recent reference range.",
  v_unsure="🤔 Not enough recent price data. Ask your cooperative or extension worker.",
  price_caveat="Reference only, not a guaranteed price. Quality, moisture and transport matter.",
  syn="Prices are SYNTHETIC (demo). Put a real HDX/WFP CSV in data/.", sync="🔄 Sync now",
  synced="Synced (simulated). Production: POST to API / SMS queue of the extension worker.",
  nosync="No signal. Data stays safely on the phone.",
  foot="Support tool. It does not replace an agronomist. No personal data collected.",
  healthy_adv="No disease seen on this leaf. Check other plants in the plot.",
  adv="Remove heavily affected leaves, improve airflow/shade management, avoid excess moisture. A technician must confirm any treatment.",
  names=dict(healthy="Healthy leaf", rust="Coffee leaf rust", leaf_miner="Leaf miner",
             brown_leaf_spot="Brown leaf spot", cercospora="Cercospora (frogeye)"),
  r_quality="photo quality: {x}", r_conf="low confidence ({x:.0%})", r_margin="model is torn between two diseases",
  r_risk="high-impact disease: needs human confirmation", q_blur="blurry", q_dark="too dark", q_bright="too bright",
  wx_fav="Recent weather is FAVOURABLE for rust: inspect plants.", wx_nofav="Weather not especially favourable for rust.",
  wx_none="No weather data (offline)."),
 "es": dict(lang_tag="es-ES", title="Míng — Asistente de parcela", signal="📶 Tengo señal",
  parcel="Código de parcela", noreg="No se pide nombre ni documento.", tab1="🍃 Mi hoja", tab2="💰 Precio justo", tab3="📤 Pendientes",
  tip="Fotografíe UNA hoja, buena luz, sin sombra de la mano.", cam="Foto", up="…o suba una foto",
  demo="MODO DEMO: no hay modelo entrenado en /models. Resultado solo ilustrativo.",
  looks="Parece", conf="confianza", unsure="🤔 No estoy seguro. Consulte a un extensionista antes de actuar.",
  retake="Repita la foto: más cerca, más luz, sin mover el celular.", model_guess="Sugerencia del modelo (NO confirmada)",
  ask="📨 Pedir ayuda a un técnico", saved="Guardado en este teléfono. Se enviará cuando haya señal.",
  market="Mercado cercano", product="Cultivo", offer="Precio que ofrece el comprador (por kg)", compare="Comparar",
  sms_note="Texto corto listo para SMS / WhatsApp.", listen="Escuchar",
  v_low="Esta oferta está POR DEBAJO del rango de referencia reciente ({lo:.2f}–{hi:.2f} {cur}/kg). Compare con otros compradores antes de vender.",
  v_fair="Esta oferta está dentro del rango de referencia reciente ({lo:.2f}–{hi:.2f} {cur}/kg).",
  v_high="Esta oferta está POR ENCIMA del rango de referencia reciente.",
  v_unsure="🤔 No hay datos de precios recientes suficientes. Consulte a su cooperativa o extensionista.",
  price_caveat="Solo referencia, no precio garantizado. Importan calidad, humedad y transporte.",
  syn="Precios SINTÉTICOS (demo). Ponga un CSV real de HDX/WFP en data/.", sync="🔄 Sincronizar",
  synced="Sincronizado (simulado). Producción: POST a API / cola SMS del extensionista.",
  nosync="Sin señal. Los datos siguen guardados en el teléfono.",
  foot="Herramienta de apoyo. No reemplaza al agrónomo. No recoge datos personales.",
  healthy_adv="No se ve enfermedad en esta hoja. Revise otras plantas de la parcela.",
  adv="Retire hojas muy afectadas, mejore ventilación/sombra, evite exceso de humedad. Un técnico debe confirmar cualquier tratamiento.",
  names=dict(healthy="Hoja sana", rust="Roya del café", leaf_miner="Minador de la hoja",
             brown_leaf_spot="Mancha marrón", cercospora="Cercospora (ojo de gallo)"),
  r_quality="calidad de foto: {x}", r_conf="confianza baja ({x:.0%})", r_margin="el modelo duda entre dos enfermedades",
  r_risk="enfermedad de alto impacto: requiere confirmación humana", q_blur="borrosa", q_dark="muy oscura", q_bright="con demasiado brillo",
  wx_fav="El clima reciente es FAVORABLE para roya: revise sus plantas.", wx_nofav="Clima sin condiciones especialmente favorables para roya.",
  wx_none="Sin datos de clima (modo offline)."),
}

# ------------------------------------------------------------ offline store-and-forward
def db():
    con = sqlite3.connect(DB)
    con.execute("CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, ts TEXT, parcel TEXT, kind TEXT, payload TEXT, synced INTEGER DEFAULT 0)")
    con.execute("CREATE TABLE IF NOT EXISTS cache(k TEXT PRIMARY KEY, v TEXT, ts REAL)")
    con.execute("CREATE TABLE IF NOT EXISTS decisions(ts TEXT, kind TEXT, decided INTEGER, top_conf REAL)")  # no images, no PII
    return con

def enqueue(parcel, kind, payload):
    con = db()
    con.execute("INSERT INTO outbox VALUES(?,?,?,?,?,0)", (uuid.uuid4().hex[:8], datetime.now().isoformat(timespec="seconds"),
                parcel, kind, json.dumps(payload, ensure_ascii=False)))
    con.commit(); con.close()

def log_decision(kind, decided, conf):
    con = db(); con.execute("INSERT INTO decisions VALUES(?,?,?,?)", (datetime.now().isoformat(timespec="seconds"), kind, int(decided), conf))
    con.commit(); con.close()

# ------------------------------------------------------------ edge vision
def quality_check(img):
    g = np.asarray(img.convert("L").resize((256, 256)), dtype=np.float32)
    lap = g[1:-1, 1:-1] * 4 - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    sharp, bright = float(lap.var()), float(g.mean())
    p = []
    if sharp < MIN_SHARPNESS: p.append("q_blur")
    if bright < MIN_BRIGHT: p.append("q_dark")
    if bright > MAX_BRIGHT: p.append("q_bright")
    return p, sharp, bright

def softmax(x):
    e = np.exp(x - x.max()); return e / e.sum()

@st.cache_resource
def load_tflite():
    if not os.path.exists(MODEL_PATH): return None
    try:
        try: from tflite_runtime.interpreter import Interpreter
        except ImportError: from tensorflow.lite.python.interpreter import Interpreter
        it = Interpreter(model_path=MODEL_PATH); it.allocate_tensors(); return it
    except Exception:
        return None

def predict(img):
    """Real TFLite INT8 model if present, else a colour heuristic (DEMO ONLY, not a trained model)."""
    it = load_tflite()
    if it is not None:
        i, o = it.get_input_details()[0], it.get_output_details()[0]
        h, w = i["shape"][1:3]
        x = np.asarray(img.convert("RGB").resize((w, h)), dtype=np.float32)
        x = x.astype(np.uint8) if i["dtype"] == np.uint8 else x / 255.0
        it.set_tensor(i["index"], x[None]); it.invoke()
        p = it.get_tensor(o["index"])[0].astype(np.float32)
        return (softmax(p) if (p.sum() > 1.01 or p.min() < 0) else p), "MODEL"
    a = np.asarray(img.convert("HSV").resize((128, 128)), dtype=np.float32) / 255.0
    H, S, V = a[..., 0] * 360, a[..., 1], a[..., 2]
    orange = ((H > 15) & (H < 45) & (S > .5) & (V > .5)).mean()
    brown = ((H > 10) & (H < 40) & (S > .3) & (V < .45)).mean()
    green = ((H > 70) & (H < 160) & (S > .25)).mean()
    lg = np.array([green * 6, orange * 14, brown * 3, brown * 10, brown * 6]) + np.random.default_rng(0).normal(0, .05, 5)
    return softmax(lg), "DEMO"

def triage(probs, problems):
    """Fail-safe gate: decide whether to show a diagnosis or abstain and escalate to a human."""
    o = np.argsort(probs)[::-1]
    top, sec, label = float(probs[o[0]]), float(probs[o[1]]), LABELS[o[0]]
    reasons = []
    if problems: reasons.append(("r_quality", dict(x="/".join(problems))))
    if top < MIN_CONF: reasons.append(("r_conf", dict(x=top)))
    if top - sec < MIN_MARGIN: reasons.append(("r_margin", {}))
    certain = not reasons
    if certain and label in HIGH_RISK: reasons.append(("r_risk", {}))   # shown, but with human confirmation
    return dict(label=label, conf=top, certain=certain, escalate=bool(reasons), reasons=reasons,
                top2=[(LABELS[i], float(probs[i])) for i in o[:2]])

# ------------------------------------------------------------ prices (WFP/HDX)
@st.cache_data
def load_prices():
    if os.path.exists(PRICES_CSV):
        d = pd.read_csv(PRICES_CSV, parse_dates=["date"])
        if "currency" not in d: d["currency"] = "LCU"
        return d, False
    rng = np.random.default_rng(1)   # SYNTHETIC demo data
    rows = [(d, m, "Coffee (dry parchment)", round(float(7.5 + b + .6 * np.sin(i / 2) + rng.normal(0, .25)), 2), "LCU")
            for m, b in [("Market A", 0), ("Market B", -.4), ("Market C", .3)]
            for i, d in enumerate(pd.date_range(end=pd.Timestamp.today().normalize().replace(day=1), periods=12, freq="MS"))]
    return pd.DataFrame(rows, columns=["date", "market", "commodity", "price", "currency"]), True

def fair_price(df, market, commodity, months=6):
    s = df[(df.market == market) & (df.commodity == commodity)].sort_values("date")
    if len(s) < PRICE_MIN_POINTS: return None
    s = s.tail(months)
    if (pd.Timestamp.today() - s.date.max()).days > PRICE_MAX_AGE_DAYS: return None   # stale -> abstain
    q = s.price.quantile
    return dict(lo=float(q(.25)), hi=float(q(.75)), n=len(s), cur=str(s.currency.iloc[-1]))

def price_verdict(offer, fp):
    if fp is None: return "unsure"
    if offer < fp["lo"]: return "low"
    return "fair" if offer <= fp["hi"] else "high"

# ------------------------------------------------------------ agro-climate (NASA POWER, cached)
def climate(lat, lon):
    key = f"pw_{lat:.2f}_{lon:.2f}"
    con = db(); row = con.execute("SELECT v, ts FROM cache WHERE k=?", (key,)).fetchone()
    if row and time.time() - row[1] < 3 * 86400:
        con.close(); return json.loads(row[0])
    try:
        import requests
        s = (datetime.now() - pd.Timedelta(days=21)).strftime("%Y%m%d"); e = datetime.now().strftime("%Y%m%d")
        u = (f"https://power.larc.nasa.gov/api/temporal/daily/point?parameters=T2M,RH2M&community=AG"
             f"&longitude={lon}&latitude={lat}&start={s}&end={e}&format=JSON")
        d = requests.get(u, timeout=8).json()["properties"]["parameter"]
        out = {k: float(np.mean([v for v in d[k].values() if v > -900])) for k in ("T2M", "RH2M")}
        con.execute("INSERT OR REPLACE INTO cache VALUES(?,?,?)", (key, json.dumps(out), time.time())); con.commit(); con.close()
        return out
    except Exception:
        con.close(); return json.loads(row[0]) if row else None

def speak(text, tag):   # browser TTS: works offline with local voices, zero server cost
    a = html.escape(json.dumps(text), quote=True)
    components.html(f"<button style='font-size:18px;padding:10px 16px' onclick=\"var u=new SpeechSynthesisUtterance({a});"
                    f"u.lang='{tag}';speechSynthesis.cancel();speechSynthesis.speak(u)\">🔊</button>", height=56)

# ------------------------------------------------------------ UI
st.set_page_config(page_title="Míng", page_icon="🌱", layout="centered")
lang = st.sidebar.selectbox("Language / Idioma", ["en", "es"]); t = T[lang]
lat = st.sidebar.number_input("Latitude (optional)", value=0.0, format="%.2f")
lon = st.sidebar.number_input("Longitude (optional)", value=0.0, format="%.2f")
con = db(); r = con.execute("SELECT COUNT(*), SUM(decided) FROM decisions WHERE kind='diagnosis'").fetchone(); con.close()
if r[0]: st.sidebar.metric("Diagnosis coverage (rest escalated)", f"{(r[1] or 0) / r[0]:.0%}", help=f"{r[0]} photos analysed")
if "parcel" not in st.session_state: st.session_state.parcel = "P-" + uuid.uuid4().hex[:6].upper()

st.title(f"🌱 {t['title']}")
c1, c2 = st.columns(2)
online = c1.toggle(t["signal"], value=False)
c2.caption(f"{t['parcel']}: **{st.session_state.parcel}**  \n{t['noreg']}")
tab1, tab2, tab3 = st.tabs([t["tab1"], t["tab2"], t["tab3"]])

with tab1:
    st.write(t["tip"])
    f = st.camera_input(t["cam"]) or st.file_uploader(t["up"], type=["jpg", "jpeg", "png"])
    if f:
        img = Image.open(f).convert("RGB"); st.image(img, width=240)
        problems, sharp, bright = quality_check(img)
        probs, mode = predict(img); res = triage(probs, problems)
        log_decision("diagnosis", res["certain"], res["conf"])
        if mode == "DEMO": st.warning(t["demo"])
        name = t["names"][res["label"]]
        if res["certain"]:
            st.success(f"{t['looks']}: **{name}** ({t['conf']} {res['conf']:.0%})")
            st.write(t["healthy_adv"] if res["label"] == "healthy" else t["adv"])
            speak(f"{t['looks']} {name}.", t["lang_tag"])
        else:
            st.error(t["unsure"]); speak(t["unsure"].replace("🤔 ", ""), t["lang_tag"])
        for k, kw in res["reasons"]:
            if "x" in kw and k == "r_quality": kw = dict(x=", ".join(t[q] for q in kw["x"].split("/")))
            st.write("• " + t[k].format(**kw))
        if problems: st.info(t["retake"])
        elif not res["certain"]:
            st.caption(f"{t['model_guess']}: " + ", ".join(f"{t['names'][l]} {p:.0%}" for l, p in res["top2"]))
        if online:
            c = climate(lat, lon)
            st.caption("🌦️ " + (t["wx_none"] if not c else (t["wx_fav"] if c["RH2M"] > 80 and 20 <= c["T2M"] <= 26 else t["wx_nofav"])) + " (NASA POWER)")
        sms = f"MING {st.session_state.parcel}: " + (name if res["certain"] else "NOT SURE - needs review") + f" ({res['conf']:.0%})"
        st.code(sms[:160], language=None); st.caption(t["sms_note"])
        if res["escalate"] and st.button(t["ask"]):
            enqueue(st.session_state.parcel, "phyto_query", dict(top2=res["top2"], sharp=sharp, bright=bright, lat=lat, lon=lon, mode=mode))
            st.success(t["saved"])

with tab2:
    df, syn = load_prices()
    if syn: st.warning(t["syn"])
    market = st.selectbox(t["market"], sorted(df.market.unique()))
    crop = st.selectbox(t["product"], sorted(df.commodity.unique()))
    offer = st.number_input(t["offer"], min_value=0.0, value=6.5, step=0.1)
    if st.button(t["compare"]):
        fp = fair_price(df, market, crop); v = price_verdict(offer, fp)
        msg = t["v_" + v].format(**fp) if fp else t["v_unsure"]
        {"low": st.error, "fair": st.success, "high": st.success, "unsure": st.info}[v](msg)
        speak(msg.replace("🤔 ", ""), t["lang_tag"])
        if fp: st.caption(f"n={fp['n']} · " + t["price_caveat"])
        log_decision("price", v != "unsure", 1.0 if fp else 0.0)
        enqueue(st.session_state.parcel, "price_check", dict(market=market, crop=crop, offer=offer, verdict=v))
    st.line_chart(df[(df.market == market) & (df.commodity == crop)].set_index("date")["price"])

with tab3:
    con = db(); st.dataframe(pd.read_sql("SELECT id, ts, kind, synced FROM outbox ORDER BY ts DESC", con), use_container_width=True)
    if st.button(t["sync"]):
        if online: con.execute("UPDATE outbox SET synced=1 WHERE synced=0"); con.commit(); st.success(t["synced"])
        else: st.warning(t["nosync"])
    con.close()
st.divider(); st.caption(t["foot"])
