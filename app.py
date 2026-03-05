#!/usr/bin/env python3
import datetime, time, os, requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)
VERSION = "v4.3"
FMP_KEY = os.environ.get("FMP_KEY", "uASFvYQpbnihotCISCs3ACmDgE639el4")
FMP_URL = "https://financialmodelingprep.com/api/v3"

SECTORES_UTILITY = ["Utilities"]
SECTORES_REIT    = ["Real Estate"]

SANITY = {
    "payout":    (0, 200), "roe": (-50, 200), "nd_ebitda": (-5, 20),
    "per": (0, 150), "pfcf": (0, 150), "ev_ebitda": (0, 80),
    "bpa_cagr": (-50, 150), "dy": (0, 25), "dgr_tasa": (-20, 50),
}

def sanity(valor, clave, decimals=1):
    if valor is None: return None
    lo, hi = SANITY.get(clave, (-999999, 999999))
    return round(valor, decimals) if lo <= valor <= hi else None

def fmp_get(endpoint, params=None):
    p = {"apikey": FMP_KEY}
    if params: p.update(params)
    r = requests.get(f"{FMP_URL}/{endpoint}", params=p, timeout=15)
    return r.json()

def safe_float(v):
    if v in (None, "None", "-", "", "N/A"): return None
    try: return float(v)
    except: return None

# ── Semáforos ──────────────────────────────────────────────────────────────────
def s_dgr_anos(v):
    if v is None: return "grey"
    return "green" if v >= 10 else "amber" if v >= 2 else "red"

def s_dgr_tasa(v):
    if v is None: return "grey"
    return "green" if v >= 6 else "amber" if v >= 3 else "red"

def s_payout(v, sector=""):
    if v is None: return "grey"
    util = sector in SECTORES_UTILITY + SECTORES_REIT
    if util: return "green" if v <= 80 else "amber" if v <= 95 else "red"
    return "green" if 30 <= v <= 60 else "amber" if v < 80 else "red"

def s_roe(v, sector=""):
    if v is None: return "grey"
    m = 8 if sector in SECTORES_UTILITY else 15
    return "green" if v >= m else "amber" if v >= m*0.7 else "red"

def s_nd_ebitda(v, sector=""):
    if v is None: return "grey"
    if sector in SECTORES_UTILITY: return "green" if v < 4 else "amber" if v < 6 else "red"
    return "green" if v < 2 else "amber" if v < 3 else "red"

def s_bpa_cagr(v):
    if v is None: return "grey"
    return "green" if v >= 5 else "amber" if v >= 0 else "red"

def s_dy(actual, historico):
    if actual is None: return "grey"
    if historico is None: return "green" if actual >= 3.5 else "amber" if actual >= 2.5 else "red"
    r = actual / historico
    return "green" if r >= 1.05 else "amber" if r >= 0.85 else "red"

def s_per(v, sector=""):
    if v is None: return "grey"
    ref = 20 if sector in SECTORES_UTILITY else 18
    return "green" if v < ref*0.85 else "amber" if v < ref*1.15 else "red"

def s_pfcf(v):
    if v is None: return "grey"
    return "green" if v < 18 else "amber" if v < 25 else "red"

def s_ev_ebitda(v, sector=""):
    if v is None: return "grey"
    t = 16 if sector in SECTORES_UTILITY else 12
    return "green" if v < t else "amber" if v < t*1.3 else "red"

def s_mos(v):
    if v is None: return "grey"
    return "green" if v >= 20 else "amber" if v >= 10 else "red"

def calcular_veredicto(datos):
    sector = datos.get("sector", "")
    sq = [s_dgr_anos(datos.get("dgr_anos")), s_payout(datos.get("payout"), sector),
          s_roe(datos.get("roe"), sector), s_nd_ebitda(datos.get("nd_ebitda"), sector),
          s_bpa_cagr(datos.get("bpa_cagr"))]
    se = [s_dy(datos.get("dy"), datos.get("dy_historico")), s_per(datos.get("per"), sector),
          s_pfcf(datos.get("pfcf")), s_ev_ebitda(datos.get("ev_ebitda"), sector), s_mos(datos.get("mos"))]
    def score(s):
        t = len([x for x in s if x != "grey"])
        return (s.count("green")/t if t else 0), (s.count("red")/t if t else 0)
    qg, qr = score(sq); eg, er = score(se)
    dgr_ok = s_dgr_anos(datos.get("dgr_anos")) in ["green","amber"]
    dy_ok  = s_dy(datos.get("dy"), datos.get("dy_historico")) in ["green","amber"]
    deu_ok = s_nd_ebitda(datos.get("nd_ebitda"), sector) in ["green","amber"]
    if qr >= 0.5: return "ESPERAR","La calidad del negocio no cumple los criterios mínimos para dividendo creciente."
    if er >= 0.6 and qg < 0.4: return "ESPERAR","Precio elevado y calidad insuficiente. Sin margen de seguridad."
    if qg >= 0.55 and eg >= 0.5 and dgr_ok and dy_ok and deu_ok: return "COMPRAR","Buena calidad con precio atractivo respecto a su historial. Oportunidad clara."
    if qg >= 0.4 and er < 0.5: return "VIGILAR","Empresa de calidad pero el precio no ofrece suficiente margen de seguridad."
    if eg >= 0.5 and qg < 0.55: return "VIGILAR","Precio interesante pero la calidad del negocio no es suficientemente sólida."
    return "VIGILAR","Perfil mixto. Analizar con más detalle antes de decidir."

# ── Fetch FMP ──────────────────────────────────────────────────────────────────
def fetch_auto(ticker_str):
    hoy = datetime.date.today().strftime("%d/%m/%Y")
    r = {}

    def campo(valor, fuente, nota=""):
        return {"valor": valor, "fuente": fuente, "fecha": hoy, "nota": nota}

    try:
        # 1. Profile — nombre, sector, precio, DY
        profile = fmp_get(f"profile/{ticker_str}")
        if not profile or not isinstance(profile, list):
            return {"error": f"Ticker '{ticker_str}' no encontrado"}
        p = profile[0]
        if not p.get("symbol"):
            return {"error": f"Ticker '{ticker_str}' no encontrado"}

        r["nombre"] = p.get("companyName", ticker_str)
        r["sector"]  = p.get("sector", "")
        r["precio"]  = safe_float(p.get("price"))
        r["moneda"]  = p.get("currency", "USD")

        dy_r = safe_float(p.get("lastDiv"))
        precio = r["precio"]
        dy = None
        if dy_r and precio and precio > 0:
            dy = sanity(round((dy_r / precio) * 100, 2), "dy")
        r["dy"] = campo(dy, "FMP — Profile", "Dividendo anual / precio actual")
        r["dy_historico"] = campo(None, "Manual", "Yahoo Finance → Estadísticas → Dividendos → Rentabilidad últimos 5 años")

        # 2. Key Metrics TTM — ROE, ND/EBITDA, P/FCF, EV/EBITDA
        km = fmp_get(f"key-metrics-ttm/{ticker_str}")
        km0 = km[0] if km and isinstance(km, list) else {}

        roe_r = safe_float(km0.get("roeTTM"))
        r["roe"] = campo(sanity(round(roe_r*100,1),"roe") if roe_r else None, "FMP — Key Metrics TTM", "roeTTM")

        nd_r = safe_float(km0.get("netDebtToEBITDATTM"))
        r["nd_ebitda"] = campo(sanity(nd_r,"nd_ebitda") if nd_r is not None else None, "FMP — Key Metrics TTM", "netDebtToEBITDATTM")
        r["nd_deuda"]      = campo(None, "FMP", "Incluido en ND/EBITDA")
        r["nd_efectivo"]   = campo(None, "FMP", "Incluido en ND/EBITDA")
        r["nd_ebitda_raw"] = campo(None, "FMP", "Incluido en ND/EBITDA")

        pfcf_r = safe_float(km0.get("pfcfRatioTTM"))
        r["pfcf"] = campo(sanity(pfcf_r,"pfcf") if pfcf_r else None, "FMP — Key Metrics TTM", "pfcfRatioTTM")

        ev_r = safe_float(km0.get("enterpriseValueOverEBITDATTM"))
        r["ev_ebitda"] = campo(sanity(ev_r,"ev_ebitda") if ev_r else None, "FMP — Key Metrics TTM", "enterpriseValueOverEBITDATTM")

        # 3. Ratios TTM — Payout, PER, BPA CAGR
        rt = fmp_get(f"ratios-ttm/{ticker_str}")
        rt0 = rt[0] if rt and isinstance(rt, list) else {}

        pr = safe_float(rt0.get("payoutRatioTTM"))
        r["payout"] = campo(sanity(round(pr*100,1),"payout") if pr else None, "FMP — Ratios TTM", "⚠ Puede ser GAAP. Verifica en Dividend.com → Dividend Safety → Payout Ratio")

        per_r = safe_float(rt0.get("priceEarningsRatioTTM"))
        r["per"] = campo(sanity(per_r,"per") if per_r else None, "FMP — Ratios TTM", "priceEarningsRatioTTM")

        eps_t = safe_float(rt0.get("epsTTM") or km0.get("epsTTM"))
        # BPA CAGR desde earnings históricos
        try:
            earn = fmp_get(f"income-statement/{ticker_str}", {"limit": 6})
            bpa_cagr = None
            if earn and isinstance(earn, list) and len(earn) >= 5:
                eps_vals = [safe_float(e.get("eps")) for e in earn[:6]]
                eps_vals = [v for v in eps_vals if v and v > 0]
                if len(eps_vals) >= 5:
                    v0, v1 = eps_vals[-1], eps_vals[0]
                    if v0 > 0:
                        bpa_cagr = sanity(round(((v1/v0)**(1/5)-1)*100,1),"bpa_cagr")
            r["bpa_cagr"] = campo(bpa_cagr, "FMP — Income Statement (5 años)", "CAGR EPS 5 años")
        except:
            r["bpa_cagr"] = campo(None, "Error", "Verifica en Simply Wall St → Tasa de crecimiento del BPA")

        # 4. DGR desde historial de dividendos
        try:
            dv = fmp_get(f"historical-price-full/stock_dividend/{ticker_str}")
            historical = dv.get("historical", []) if isinstance(dv, dict) else []
            if historical:
                from collections import defaultdict
                by_year = defaultdict(float)
                for d in historical:
                    yr = (d.get("date",""))[:4]
                    amt = safe_float(d.get("dividend")) or safe_float(d.get("adjDividend")) or 0
                    if yr.isdigit(): by_year[yr] += amt
                years = sorted(by_year.keys())
                vals  = [by_year[y] for y in years]
                anos = 0
                for i in range(len(vals)-1, 0, -1):
                    if vals[i] >= vals[i-1] * 0.95: anos += 1
                    else: break
                r["dgr_anos"] = campo(anos, "FMP — Dividend History", "Verifica en Dividend.com → Div Growth")
                d5 = None
                if len(vals) >= 6:
                    v0, v1 = vals[-6], vals[-1]
                    if v0 > 0:
                        d5 = sanity(round(((v1/v0)**(1/5)-1)*100,1),"dgr_tasa")
                r["dgr_tasa"] = campo(d5, "FMP — Dividend History", "⚠ Verifica en Dividend.com → Div Growth → col. 5Y")
            else:
                r["dgr_anos"] = campo(None, "Sin datos", "Busca en Dividend.com")
                r["dgr_tasa"] = campo(None, "Sin datos", "Busca en Dividend.com")
        except:
            r["dgr_anos"] = campo(None, "Error", "Busca en Dividend.com")
            r["dgr_tasa"] = campo(None, "Error", "Busca en Dividend.com")

    except Exception as e:
        return {"error": str(e)}

    return r

# ── Aristocrats ────────────────────────────────────────────────────────────────
ARISTOCRATS = [
    "ABT","ADP","AFL","AOS","ALB","AMCR","AME","APD","ATO","AWR",
    "BDX","BEN","BRO","CAH","CAT","CB","CINF","CL","CLX","CTAS",
    "CVX","DOV","ECL","ED","EMR","ESS","EXPD","FAST","FDS","FRT",
    "GD","GPC","GWW","HRL","IBM","ITW","JNJ","KMB","KO","LEG",
    "LIN","LOW","MCD","MCO","MDT","MKC","MMC","MMM","NDSN","NEE",
    "NUE","O","PEP","PG","PNR","PPG","ROST","ROP","SEIC",
    "SHW","SJM","SPGI","SWK","SYY","T","TGT","TROW","VFC","WAT",
    "WBA","WMT","XOM"
]

def score_empresa(datos):
    sector = datos.get("sector", "")
    campos_calidad = [
        ("dgr_anos",  s_dgr_anos(datos.get("dgr_anos"))),
        ("dgr_tasa",  s_dgr_tasa(datos.get("dgr_tasa"))),
        ("payout",    s_payout(datos.get("payout"), sector)),
        ("roe",       s_roe(datos.get("roe"), sector)),
        ("nd_ebitda", s_nd_ebitda(datos.get("nd_ebitda"), sector)),
        ("bpa_cagr",  s_bpa_cagr(datos.get("bpa_cagr"))),
    ]
    campos_entrada = [
        ("dy",        s_dy(datos.get("dy"), datos.get("dy_historico"))),
        ("per",       s_per(datos.get("per"), sector)),
        ("pfcf",      s_pfcf(datos.get("pfcf"))),
        ("ev_ebitda", s_ev_ebitda(datos.get("ev_ebitda"), sector)),
    ]
    puntos = {"green": 2, "amber": 1, "red": 0, "grey": 0}
    p_cal = sum(puntos[s] for _, s in campos_calidad)
    p_ent = sum(puntos[s] for _, s in campos_entrada)
    score = round((p_cal / (len(campos_calidad)*2) * 60) + (p_ent / (len(campos_entrada)*2) * 40))
    veredicto, _ = calcular_veredicto(datos)
    return {"score": score, "veredicto": veredicto,
            "calidad": {k: v for k, v in campos_calidad},
            "entrada": {k: v for k, v in campos_entrada}}

# ── Rutas ──────────────────────────────────────────────────────────────────────
@app.route("/debug/<ticker>")
def debug(ticker):
    try:
        data = fmp_get(f"profile/{ticker.upper()}")
        return jsonify({"status": "ok", "data": data})
    except Exception as e:
        return jsonify({"error": str(e)})

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": VERSION})

@app.route("/")
def index():
    return render_template("index.html", version=VERSION)

@app.route("/auto", methods=["POST"])
def auto():
    try:
        data   = request.get_json(force=True, silent=True) or {}
        ticker = data.get("ticker","").strip().upper()
        if not ticker: return jsonify({"error":"Introduce un ticker"}), 400
        return jsonify(fetch_auto(ticker))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/semaforo", methods=["POST"])
def semaforo():
    try:
        d      = request.get_json(force=True, silent=True) or {}
        campo  = d.get("campo"); valor = d.get("valor"); sector = d.get("sector","")
        mapa = {
            "dgr_anos": lambda v: s_dgr_anos(v), "dgr_tasa": lambda v: s_dgr_tasa(v),
            "payout":   lambda v: s_payout(v, sector), "roe": lambda v: s_roe(v, sector),
            "nd_ebitda":lambda v: s_nd_ebitda(v, sector), "bpa_cagr": lambda v: s_bpa_cagr(v),
            "dy":       lambda v: s_dy(v, d.get("dy_historico")), "per": lambda v: s_per(v, sector),
            "pfcf":     lambda v: s_pfcf(v), "ev_ebitda": lambda v: s_ev_ebitda(v, sector),
            "mos":      lambda v: s_mos(v),
        }
        fn = mapa.get(campo)
        return jsonify({"signal": fn(valor) if fn else "grey"})
    except:
        return jsonify({"signal": "grey"})

@app.route("/veredicto", methods=["POST"])
def veredicto():
    try:
        datos = request.get_json(force=True, silent=True) or {}
        v, resumen = calcular_veredicto(datos)
        return jsonify({"veredicto": v, "resumen": resumen})
    except Exception as e:
        return jsonify({"veredicto": "VIGILAR", "resumen": str(e)}), 500

@app.route("/screener", methods=["POST"])
def screener():
    resultados = []
    for ticker in ARISTOCRATS:
        try:
            datos_raw = fetch_auto(ticker)
            if "error" in datos_raw: continue
            datos = {"sector": datos_raw.get("sector", "")}
            for c in ["dgr_anos","dgr_tasa","payout","roe","nd_ebitda","bpa_cagr","dy","dy_historico","per","pfcf","ev_ebitda"]:
                obj = datos_raw.get(c)
                datos[c] = obj["valor"] if isinstance(obj, dict) else None
            res = score_empresa(datos)
            resultados.append({
                "ticker": ticker, "nombre": datos_raw.get("nombre", ticker),
                "sector": datos_raw.get("sector",""), "precio": datos_raw.get("precio"),
                "moneda": datos_raw.get("moneda","USD"), "score": res["score"],
                "veredicto": res["veredicto"], "calidad": res["calidad"],
                "entrada": res["entrada"], "datos": datos,
            })
            time.sleep(0.3)
        except:
            continue
    orden = {"COMPRAR": 0, "VIGILAR": 1, "ESPERAR": 2}
    resultados.sort(key=lambda x: (orden.get(x["veredicto"], 3), -x["score"]))
    return jsonify(resultados)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
