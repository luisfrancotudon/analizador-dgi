#!/usr/bin/env python3
import datetime, time, os, requests
from flask import Flask, jsonify, request, render_template

app = Flask(__name__)
VERSION = "v4.2"
AV_KEY = os.environ.get("AV_KEY", "S7P50XU84RPHYGAE")
AV_URL = "https://www.alphavantage.co/query"

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

def av_get(func, ticker, extra=None):
    params = {"function": func, "symbol": ticker, "apikey": AV_KEY}
    if extra: params.update(extra)
    r = requests.get(AV_URL, params=params, timeout=15)
    return r.json()

def safe_float(d, *keys):
    for k in keys:
        v = d.get(k)
        if v not in (None, "None", "-", ""):
            try: return float(str(v).replace("%","").replace(",",""))
            except: pass
    return None

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

# ── Fetch Alpha Vantage ────────────────────────────────────────────────────────
def fetch_auto(ticker_str):
    hoy = datetime.date.today().strftime("%d/%m/%Y")
    r = {}

    def campo(valor, fuente, nota=""):
        return {"valor": valor, "fuente": fuente, "fecha": hoy, "nota": nota}

    try:
        # OVERVIEW — datos fundamentales principales
        ov = av_get("OVERVIEW", ticker_str)
        if "Note" in ov or "Information" in ov:
            msg = ov.get("Note") or ov.get("Information") or ""
            return {"error": "Límite de peticiones Alpha Vantage. Espera 1 minuto e inténtalo de nuevo."}
        if not ov.get("Symbol"):
            return {"error": f"Ticker '{ticker_str}' no encontrado en Alpha Vantage"}

        r["nombre"] = ov.get("Name", ticker_str)
        r["sector"] = ov.get("Sector", "")
        r["moneda"] = ov.get("Currency", "USD")

        # Precio actual
        try:
            qt = av_get("GLOBAL_QUOTE", ticker_str)
            precio = safe_float(qt.get("Global Quote", {}), "05. price")
            r["precio"] = precio
        except:
            r["precio"] = None

        # ROE
        roe_r = safe_float(ov, "ReturnOnEquityTTM")
        roe = sanity(round(roe_r*100, 1), "roe") if roe_r else None
        r["roe"] = campo(roe, "Alpha Vantage — OVERVIEW", "ReturnOnEquityTTM")

        # Payout
        pr = safe_float(ov, "PayoutRatio")
        payout = sanity(round(pr*100, 1), "payout") if pr else None
        r["payout"] = campo(payout, "Alpha Vantage — OVERVIEW", "⚠ GAAP. Verifica en Dividend.com → Dividend Safety → Payout Ratio")

        # PER
        per = sanity(safe_float(ov, "TrailingPE"), "per")
        r["per"] = campo(per, "Alpha Vantage — OVERVIEW", "TrailingPE")

        # EV/EBITDA
        ev    = safe_float(ov, "EVToEBITDA")
        r["ev_ebitda"] = campo(sanity(ev, "ev_ebitda") if ev else None, "Alpha Vantage — OVERVIEW", "EVToEBITDA")

        # P/FCF
        pfcf_r = safe_float(ov, "PriceToFreeCashFlowsRatioTTM")
        r["pfcf"] = campo(sanity(pfcf_r, "pfcf") if pfcf_r else None, "Alpha Vantage — OVERVIEW", "PriceToFreeCashFlowsRatioTTM")

        # BPA CAGR (EPS trailing vs forward)
        eps_t = safe_float(ov, "EPS")
        eps_f = safe_float(ov, "ForwardEPS")
        bpa_cagr = None
        if eps_t and eps_f and eps_t > 0:
            bpa_cagr = sanity(round(((eps_f/eps_t)-1)*100, 1), "bpa_cagr")
        r["bpa_cagr"] = campo(bpa_cagr, "Alpha Vantage — OVERVIEW", "⚠ Solo 1 año. Verifica en Simply Wall St → Tasa de crecimiento del BPA")

        # DY actual
        dy_r = safe_float(ov, "DividendYield")
        dy = sanity(round(dy_r*100, 2), "dy") if dy_r else None
        r["dy"] = campo(dy, "Alpha Vantage — OVERVIEW", "DividendYield")

        # DY histórico — no disponible en AV, dejar en None para entrada manual
        r["dy_historico"] = campo(None, "Manual", "Busca en Yahoo Finance → Estadísticas → Dividendos → Rentabilidad últimos 5 años")

        # ND/EBITDA desde INCOME_STATEMENT y BALANCE_SHEET
        try:
            bs = av_get("BALANCE_SHEET", ticker_str)
            inc = av_get("INCOME_STATEMENT", ticker_str)
            time.sleep(0.5)

            bal = bs.get("annualReports", [{}])[0]
            inc0 = inc.get("annualReports", [{}])[0]

            deuda    = safe_float(bal, "shortLongTermDebtTotal", "longTermDebt")
            efectivo = safe_float(bal, "cashAndCashEquivalentsAtCarryingValue", "cashAndShortTermInvestments")
            ebit     = safe_float(inc0, "ebit")
            dep      = safe_float(inc0, "depreciationAndAmortization")
            ebitda   = None
            if ebit and dep:
                ebitda = ebit + dep
            elif ebit:
                ebitda = ebit

            deuda_b    = round(deuda/1e9, 2)    if deuda    else None
            efectivo_b = round(efectivo/1e9, 2) if efectivo else None
            ebitda_b   = round(ebitda/1e9, 2)   if ebitda   else None
            nd = None
            if deuda and efectivo is not None and ebitda and ebitda > 0:
                nd = sanity(round((deuda - efectivo) / ebitda, 1), "nd_ebitda")

            r["nd_deuda"]      = campo(deuda_b,    "Alpha Vantage — Balance Sheet", "shortLongTermDebtTotal")
            r["nd_efectivo"]   = campo(efectivo_b, "Alpha Vantage — Balance Sheet", "cashAndCashEquivalentsAtCarryingValue")
            r["nd_ebitda_raw"] = campo(ebitda_b,   "Alpha Vantage — Income Statement", "EBIT + D&A")
            r["nd_ebitda"]     = campo(nd,          "Alpha Vantage — calculado", "(Deuda − Efectivo) ÷ EBITDA")
        except:
            for k in ["nd_deuda","nd_efectivo","nd_ebitda_raw","nd_ebitda"]:
                r[k] = campo(None, "Error", "Introduce manualmente")

        # DGR años y tasa — desde DIVIDENDS endpoint
        try:
            dv = av_get("DIVIDENDS", ticker_str)
            time.sleep(0.5)
            divs = dv.get("data", [])
            if divs:
                # Agrupar por año
                from collections import defaultdict
                by_year = defaultdict(float)
                for d in divs:
                    yr = d.get("payment_date","")[:4] or d.get("ex_dividend_date","")[:4]
                    amt = safe_float(d, "amount") or 0
                    if yr.isdigit(): by_year[yr] += amt

                years = sorted(by_year.keys())
                vals  = [by_year[y] for y in years]

                # DGR años consecutivos
                anos = 0
                for i in range(len(vals)-1, 0, -1):
                    if vals[i] >= vals[i-1] * 0.95: anos += 1
                    else: break
                r["dgr_anos"] = campo(anos, "Alpha Vantage — DIVIDENDS", "Verifica en Dividend.com → Div Growth")

                # DGR tasa 5Y
                d5 = None
                if len(vals) >= 6:
                    v0, v1 = vals[-6], vals[-1]
                    if v0 > 0:
                        d5 = sanity(round(((v1/v0)**(1/5)-1)*100, 1), "dgr_tasa")
                r["dgr_tasa"] = campo(d5, "Alpha Vantage — DIVIDENDS", "⚠ Verifica en Dividend.com → Div Growth → col. 5Y")
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
        import requests
        params = {"function": "OVERVIEW", "symbol": ticker.upper(), "apikey": AV_KEY}
        r = requests.get(AV_URL, params=params, timeout=15)
        return jsonify({"status": r.status_code, "data": r.json()})
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
    for i, ticker in enumerate(ARISTOCRATS):
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
            # Alpha Vantage free: 25 req/min — cada empresa usa ~4 llamadas
            # Pausa generosa para no saturar
            time.sleep(3)
        except:
            continue
    orden = {"COMPRAR": 0, "VIGILAR": 1, "ESPERAR": 2}
    resultados.sort(key=lambda x: (orden.get(x["veredicto"], 3), -x["score"]))
    return jsonify(resultados)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(debug=False, host="0.0.0.0", port=port)
