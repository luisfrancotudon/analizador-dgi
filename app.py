#!/usr/bin/env python3
import datetime, time
from flask import Flask, jsonify, request, render_template
import yfinance as yf

app = Flask(__name__)
VERSION = "v4.1"

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

def fetch_auto(ticker_str):
    hoy = datetime.date.today().strftime("%d/%m/%Y")
    r = {}
    try:
        tk = yf.Ticker(ticker_str); info = tk.info or {}
        if not info or not info.get("quoteType"): return {"error": f"Ticker '{ticker_str}' no encontrado"}
        r["nombre"] = info.get("longName", ticker_str)
        r["sector"] = info.get("sector", "")
        precio = info.get("currentPrice") or info.get("regularMarketPrice")
        r["precio"] = precio; r["moneda"] = info.get("currency","USD")

        def campo(valor, fuente, nota=""):
            return {"valor": valor, "fuente": fuente, "fecha": hoy, "nota": nota}

        try:
            hd = tk.dividends; anos = None
            if hd is not None and not hd.empty:
                an = hd.resample("YE").sum(); an = an[an > 0]
                if len(an) >= 2:
                    vals = an.values.tolist()
                    med = sorted(vals)[len(vals)//2]
                    if med > 0:
                        an2 = an.copy().astype(float)
                        for idx in an.index:
                            if an2[idx] > med*5: an2[idx] /= 100
                        vals = an2.values.tolist()
                    c = 0
                    for i in range(len(vals)-1,0,-1):
                        if (vals[i]-vals[i-1])/abs(vals[i-1]) >= -0.05: c += 1
                        else: break
                    anos = c
            r["dgr_anos"] = campo(anos, "Yahoo Finance — historial dividendos", "Verifica en Dividend.com → Div Growth")
        except: r["dgr_anos"] = campo(None,"Error","Busca en Dividend.com")

        try:
            hd = tk.dividends; d5 = None
            if hd is not None and not hd.empty:
                an = hd.resample("YE").sum(); an = an[an>0]; vals = an.values.tolist()
                if len(vals) >= 6:
                    v0,v1 = vals[-6],vals[-1]
                    if v0 > 0: d5 = sanity(round(((v1/v0)**(1/5)-1)*100,1),"dgr_tasa")
            r["dgr_tasa"] = campo(d5,"Yahoo Finance — historial","⚠ Verifica en Dividend.com → Div Growth → col. 5Y")
        except: r["dgr_tasa"] = campo(None,"Error","Busca en Dividend.com")

        try:
            pr = info.get("payoutRatio")
            r["payout"] = campo(sanity(round(pr*100,1),"payout") if pr else None,"Yahoo Finance — EPS GAAP","⚠ GAAP. Sustituye por Dividend.com → Dividend Safety → Payout Ratio")
        except: r["payout"] = campo(None,"Error","Busca en Dividend.com → Dividend Safety")

        try:
            ro = info.get("returnOnEquity")
            r["roe"] = campo(sanity(round(ro*100,1),"roe") if ro else None,"Yahoo Finance — TTM","Yahoo → Estadísticas → Eficacia de gestión → Rentabilidad financiera (ttm)")
        except: r["roe"] = campo(None,"Error","")

        try:
            deu = info.get("totalDebt",0) or 0; ef = info.get("totalCash",0) or 0; eb = info.get("ebitda",0) or 0
            db = round(deu/1e9,2) if deu else None; efb = round(ef/1e9,2) if ef else None; ebb = round(eb/1e9,2) if eb else None
            nd = None
            if deu and ef is not None and eb and eb > 0: nd = sanity(round((deu-ef)/eb,1),"nd_ebitda")
            r["nd_deuda"]      = campo(db,   "Yahoo Finance","Estadísticas → Balance → Endeudamiento total (tmr)")
            r["nd_efectivo"]   = campo(efb,  "Yahoo Finance","Estadísticas → Balance → Efectivo total (tmr)")
            r["nd_ebitda_raw"] = campo(ebb,  "Yahoo Finance","Estadísticas → Ingresos → EBITDA")
            r["nd_ebitda"]     = campo(nd,   "Yahoo Finance — calculado","(Deuda − Efectivo) ÷ EBITDA")
        except:
            for k in ["nd_deuda","nd_efectivo","nd_ebitda_raw","nd_ebitda"]: r[k] = campo(None,"Error","")

        try:
            et = info.get("trailingEps"); ef2 = info.get("forwardEps"); bc = None
            if et and ef2 and et > 0: bc = sanity(round(((ef2/et)-1)*100,1),"bpa_cagr")
            r["bpa_cagr"] = campo(bc,"Yahoo Finance — EPS trailing vs forward","⚠ Solo 1 año. Verifica en Simply Wall St → Rendimiento pasado → Tasa de crecimiento del BPA")
        except: r["bpa_cagr"] = campo(None,"Error","")

        try:
            hd = tk.dividends; dy = None
            if hd is not None and not hd.empty and precio:
                mx = hd.index.max(); div1a = hd[hd.index >= mx - datetime.timedelta(days=365)].sum()
                if div1a > precio*0.30: div1a /= 100
                if div1a > 0: dy = sanity(round((div1a/precio)*100,2),"dy")
            r["dy"] = campo(dy,"Yahoo Finance — calculado","Div. últimos 12m / precio. Verifica en Dividend.com")
        except: r["dy"] = campo(None,"Error","")

        try:
            dy5 = info.get("fiveYearAvgDividendYield")
            r["dy_historico"] = campo(sanity(round(dy5,2),"dy") if dy5 else None,"Yahoo Finance — media 5 años","Estadísticas → Dividendos → Rentabilidad últimos 5 años")
        except: r["dy_historico"] = campo(None,"Error","")

        try:
            r["per"] = campo(sanity(info.get("trailingPE"),"per"),"Yahoo Finance — TTM GAAP","⚠ GAAP. Yahoo → Estadísticas → P/E últimos 12 meses")
        except: r["per"] = campo(None,"Error","")

        try:
            fcf = info.get("freeCashflow"); mc = info.get("marketCap"); pf = None
            if fcf and fcf > 0 and mc: pf = sanity(round(mc/fcf,1),"pfcf")
            r["pfcf"] = campo(pf,"Yahoo Finance — Market Cap/FCF","Estadísticas → Flujo de caja → Flujo de efectivo apalancado")
        except: r["pfcf"] = campo(None,"Error","")

        try:
            ev = info.get("enterpriseValue"); eb2 = info.get("ebitda"); ev_eb = None
            if ev and eb2 and eb2 > 0: ev_eb = sanity(round(ev/eb2,1),"ev_ebitda")
            r["ev_ebitda"] = campo(ev_eb,"Yahoo Finance — EV/EBITDA","Estadísticas → Medidas de valoración → Valor/EBITDA de empresa")
        except: r["ev_ebitda"] = campo(None,"Error","")

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

@app.route("/health")
def health():
    return jsonify({"status": "ok", "version": VERSION})

@app.route("/")
def index(): return render_template("index.html", version=VERSION)

@app.route("/auto", methods=["POST"])
def auto():
    try:
        data = request.get_json(force=True, silent=True) or {}
        ticker = data.get("ticker","").strip().upper()
        if not ticker: return jsonify({"error":"Introduce un ticker"}), 400
        return jsonify(fetch_auto(ticker))
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/semaforo", methods=["POST"])
def semaforo():
    d = request.get_json(); campo = d.get("campo"); valor = d.get("valor"); sector = d.get("sector","")
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
            time.sleep(0.4)
        except: continue
    orden = {"COMPRAR": 0, "VIGILAR": 1, "ESPERAR": 2}
    resultados.sort(key=lambda x: (orden.get(x["veredicto"], 3), -x["score"]))
    return jsonify(resultados)

# ── Arranque ───────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 5000))
    print(f"  Analizador DGI {VERSION} · puerto {port}")
    app.run(debug=False, host="0.0.0.0", port=port)
