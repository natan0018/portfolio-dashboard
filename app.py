import streamlit as st
import gspread
import pandas as pd
import plotly.graph_objects as go
from google.oauth2 import service_account
from datetime import datetime, timezone
import requests, io, urllib.parse

st.set_page_config(page_title="Portfolio Dashboard", page_icon="📈", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;600&display=swap');
html,body,[class*="css"]{font-family:'Inter',sans-serif;}
.stApp{background-color:#0d0e10;}
.block-container{padding-top:1.5rem;padding-bottom:2rem;}
.metric-card{background:#13151a;border:1px solid #2a2d38;border-radius:12px;padding:1.1rem 1.3rem;margin-bottom:.5rem;}
.metric-label{font-size:.7rem;color:#6b6f82;text-transform:uppercase;letter-spacing:.06em;margin-bottom:.3rem;}
.metric-value{font-family:'JetBrains Mono',monospace;font-size:1.5rem;font-weight:600;color:#e2e4ef;}
.metric-sub{font-size:.75rem;color:#6b6f82;}
.metric-up{font-size:.75rem;color:#3ecf8e;font-weight:600;}
.metric-dn{font-size:.75rem;color:#f56565;font-weight:600;}
div[data-testid="stDataFrame"]{border-radius:12px;overflow:hidden;}
div[data-testid="stTabs"] button{font-size:.85rem;}
</style>
""", unsafe_allow_html=True)

_S = requests.Session()
_S.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0","Accept-Language":"en-US,en;q=0.9"})

INDEX_META = {
    "SPX":  {"yf": "^GSPC",   "color": "#f5a623", "label": "S&P 500"},
    "NDX":  {"yf": "^NDX",    "color": "#7ed321", "label": "Nasdaq 100"},
    "RUT":  {"yf": "^RUT",    "color": "#bd10e0", "label": "Russell 2000"},
    "BTC":  {"yf": "BTC-USD", "color": "#f7931a", "label": "Bitcoin"},
    "ETH":  {"yf": "ETH-USD", "color": "#627eea", "label": "Ethereum"},
}
COLORS = {"IREN":"#4fa8f5","BMNR":"#a78bfa","MSTR":"#f5c842"}
PORT_COLOR = "#3ecf8e"

PERIOD_CFG = {
    "1D": ("1d",  "30m"),
    "1W": ("5d",  "1d"),
    "1M": ("1mo", "1d"),
}
RETURN_CFG = {
    "1D":  ("5d",  "1d"),
    "1W":  ("5d",  "1d"),
    "1M":  ("1mo", "1d"),
    "1Y":  ("1y",  "1wk"),
    "YTD": ("ytd", "1d"),
}


@st.cache_resource
def _yf_session():
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
                       "Accept":"text/html,*/*","Accept-Language":"en-US,en;q=0.9"})
    s.get("https://fc.yahoo.com", timeout=6)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=6).text.strip()
    return s, crumb


@st.cache_data(ttl=1800)
def get_history(ticker: str, period: str, interval: str) -> pd.Series:
    try:
        yf_s, crumb = _yf_session()
        url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(ticker)}"
               f"?interval={interval}&range={period}&crumb={crumb}")
        data = yf_s.get(url, timeout=12).json()
        result = data["chart"]["result"][0]
        ts     = pd.to_datetime(result["timestamp"], unit="s", utc=True)
        closes = result["indicators"]["quote"][0]["close"]
        return pd.Series(closes, index=ts, name=ticker).dropna()
    except Exception:
        return pd.Series(dtype=float, name=ticker)


@st.cache_data(ttl=1800)
def get_port_history(holdings: tuple, period: str, interval: str) -> pd.Series:
    frames = {}
    for ticker, shares in holdings:
        s = get_history(ticker, period, interval)
        if not s.empty:
            frames[ticker] = s * float(shares)
    if not frames:
        return pd.Series(dtype=float)
    df = pd.DataFrame(frames).ffill().dropna(how="all")
    return df.sum(axis=1)


@st.cache_data(ttl=300)
def get_prices(tickers: tuple) -> tuple:
    prices, prev_prices = {t: 0.0 for t in tickers}, {t: 0.0 for t in tickers}
    today_utc  = datetime.now(timezone.utc).date()
    market_open = False
    for ticker in tickers:
        try:
            t   = ticker.strip().upper()
            url = f"https://stooq.com/q/l/?s={t.lower()}.us&f=sd2t2ohlcv&h&e=csv"
            r   = _S.get(url, timeout=12)
            df  = pd.read_csv(io.StringIO(r.text))
            df.columns = df.columns.str.strip()
            close = pd.to_numeric(df["Close"].iloc[-1], errors="coerce")
            if not pd.isna(close) and close > 0:
                prices[ticker] = round(float(close), 2)
                if pd.to_datetime(df["Date"].iloc[-1]).date() == today_utc:
                    market_open = True
        except Exception:
            pass
    if market_open:
        try:
            yf_s, crumb = _yf_session()
            symbols = ",".join([t.strip().upper() for t in tickers])
            data    = yf_s.get(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}&crumb={crumb}", timeout=10).json()
            for q in data["quoteResponse"]["result"]:
                sym  = q.get("symbol","").strip().upper()
                prev = q.get("regularMarketPreviousClose", 0)
                if sym in prev_prices and prev > 0:
                    prev_prices[sym] = round(float(prev), 2)
        except Exception:
            pass
        for t in tickers:
            if prev_prices[t] == 0.0:
                prev_prices[t] = prices[t]
    else:
        for t in tickers:
            prev_prices[t] = prices[t]
    return prices, prev_prices, market_open


@st.cache_data(ttl=3600)
def get_usd_ils() -> float:
    try:
        data = _S.get("https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/BOI.STATISTICS/EXR/1.0/RER_USD_ILS?format=json&lastNObservations=1", timeout=6).json()
        rate = float(list(list(data["data"]["dataSets"][0]["series"].values())[0]["observations"].values())[0][0])
        if 1 < rate < 10: return round(rate, 4)
    except Exception: pass
    try:
        rate = float(_S.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=6).json()["rates"]["ILS"])
        if 1 < rate < 10: return round(rate, 4)
    except Exception: pass
    return 3.60


@st.cache_data(ttl=60)
def load_from_sheets():
    try:
        creds = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        gc = gspread.authorize(service_account.Credentials.from_service_account_info(creds, scopes=scopes))
        df = pd.DataFrame(gc.open_by_key(st.secrets["sheet_id"]).worksheet("Sheet1").get_all_records())
        df["Shares"]  = pd.to_numeric(df["Shares"],  errors="coerce")
        df["AvgCost"] = pd.to_numeric(df["AvgCost"], errors="coerce")
        df["Ticker"]  = df["Ticker"].astype(str).str.strip().str.upper()
        return df.dropna(subset=["Ticker","Shares","AvgCost"])
    except Exception as e:
        st.warning(f"Google Sheets: {e}")
        return None


@st.cache_data(ttl=60)
def load_dca_from_sheets():
    try:
        creds = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
        gc = gspread.authorize(service_account.Credentials.from_service_account_info(creds, scopes=scopes))
        df = pd.DataFrame(gc.open_by_key(st.secrets["sheet_id"]).worksheet("DCA").get_all_records())
        df["Date"]   = pd.to_datetime(df["Date"], errors="coerce")
        df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce")
        return df.dropna(subset=["Date","Amount"])
    except Exception:
        return pd.DataFrame(columns=["Date","Amount","Note"])


def default_portfolio():
    return pd.DataFrame([
        {"Ticker":"IREN", "Shares":86.289,   "AvgCost":41.87},
        {"Ticker":"BMNR", "Shares":227.9826, "AvgCost":19.44},
        {"Ticker":"MSTR", "Shares":19.0,     "AvgCost":136.17},
    ])


def pct_color(v):
    if v is None or (isinstance(v, float) and pd.isna(v)): return ""
    return "#3ecf8e" if v >= 0 else "#f56565"


def fmt_pct(v, suffix="%"):
    if v is None or (isinstance(v, float) and pd.isna(v)): return "N/A"
    return f"{v:+.2f}{suffix}"


# ──────────────────────────────────────────────────────────────────────────────
def main():
    col_h, col_fx = st.columns([3,1])
    with col_h:
        st.markdown("## 📈 Portfolio Dashboard")
        st.caption(f"Last update: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    with col_fx:
        usd_ils = st.number_input("USD / ILS", min_value=2.5, max_value=4.5,
                                  value=float(get_usd_ils()), step=0.0001, format="%.4f")
    st.divider()

    raw = load_from_sheets()
    raw = raw if raw is not None else default_portfolio()

    port_df = raw[raw["Ticker"] != "DCA"].copy()
    tickers  = tuple(port_df["Ticker"].tolist())
    holdings = tuple(zip(port_df["Ticker"], port_df["Shares"]))

    with st.spinner("Loading prices..."):
        prices, prev_prices, market_open = get_prices(tickers)

    failed = [t for t in tickers if prices.get(t,0)==0]
    if failed: st.error(f"Could not fetch: {', '.join(failed)}")
    badge = "🟢 Market open" if market_open else "🔴 Market closed — daily change $0.00"
    st.caption(badge)

    port_df["Price"]    = port_df["Ticker"].map(prices)
    port_df["Prev"]     = port_df["Ticker"].map(prev_prices)
    port_df["ValUSD"]   = port_df["Shares"] * port_df["Price"]
    port_df["ValILS"]   = port_df["ValUSD"] * usd_ils
    port_df["Cost"]     = port_df["Shares"] * port_df["AvgCost"]
    port_df["PnL"]      = port_df["ValUSD"] - port_df["Cost"]
    port_df["PnLPct"]   = (port_df["PnL"] / port_df["Cost"] * 100).round(2)
    port_df["DayChg"]   = port_df["Shares"] * (port_df["Price"] - port_df["Prev"])
    port_df["DayChgPct"]= ((port_df["Price"]-port_df["Prev"])/port_df["Prev"].replace(0,float("nan"))*100).round(2)
    total_val = port_df["ValUSD"].sum()
    port_df["W"] = (port_df["ValUSD"]/total_val*100).round(1) if total_val else 0.0

    # Weekly change — from 5d history
    week_hist = {t: get_history(t, "5d", "1d") for t in tickers}
    def week_start_price(t):
        s = week_hist.get(t, pd.Series(dtype=float))
        return float(s.iloc[0]) if len(s)>=2 else prices.get(t,0)
    port_df["WkChg"] = port_df.apply(lambda r: r["Shares"]*(r["Price"]-week_start_price(r["Ticker"])), axis=1)

    total_usd    = port_df["ValUSD"].sum()
    total_ils    = port_df["ValILS"].sum()
    total_pnl    = port_df["PnL"].sum()
    total_cost   = port_df["Cost"].sum()
    total_pnl_p  = total_pnl/total_cost*100 if total_cost else 0
    daily_chg    = port_df["DayChg"].sum()
    daily_chg_p  = daily_chg/(total_usd-daily_chg)*100 if (total_usd-daily_chg) else 0
    weekly_chg   = port_df["WkChg"].sum()

    # ── TABS ──────────────────────────────────────────────────────────────────
    tab1, tab2, tab3, tab4 = st.tabs(["📊 Dashboard","📈 Charts","🏆 Returns","💰 DCA"])

    # ── TAB 1: Dashboard ──────────────────────────────────────────────────────
    with tab1:
        c1,c2,c3,c4 = st.columns(4)
        def kpi(col, label, value, delta, delta_is_pct=False):
            cls = "metric-up" if (delta or 0)>=0 else "metric-dn"
            d   = fmt_pct(delta, "%") if delta_is_pct else (f"${abs(delta):,.2f}" if delta is not None else "")
            col.markdown(f'''<div class="metric-card"><div class="metric-label">{label}</div>
<div class="metric-value">{value}</div><div class="{cls}">{d}</div></div>''', unsafe_allow_html=True)

        kpi(c1, "Portfolio (ILS)",    f"₪{total_ils:,.0f}",    daily_chg)
        kpi(c2, "Portfolio (USD)",    f"${total_usd:,.2f}",     weekly_chg)
        kpi(c3, "Total P&L",          f"${total_pnl:+,.2f}",    total_pnl_p,  True)
        with c4:
            color = "#3ecf8e" if daily_chg>=0 else "#f56565"
            chg_s = f"${daily_chg:+,.2f}" if market_open else "$0.00"
            pct_s = f"{daily_chg_p:+.2f}%" if market_open else "Closed"
            cls   = "metric-up" if daily_chg>=0 else "metric-dn"
            c4.markdown(f'''<div class="metric-card"><div class="metric-label">Daily Change</div>
<div class="metric-value" style="color:{color}">{chg_s}</div>
<div class="{cls}">{pct_s}</div></div>''', unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Charts row
        ch1,ch2,ch3 = st.columns([2,1,1])
        with ch1:
            bar_y = port_df["DayChg"] if market_open else pd.Series([0]*len(port_df))
            fig = go.Figure(go.Bar(x=port_df["Ticker"], y=bar_y,
                marker_color=[("#3ecf8e" if v>=0 else "#f56565") for v in bar_y],
                marker_line_width=0,
                text=[f"${v:+,.2f}" for v in bar_y], textposition="outside"))
            fig.update_layout(title="Daily Change ($)", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef",size=13),
                margin=dict(t=40,b=20,l=20,r=20), height=240,
                xaxis=dict(gridcolor="#2a2d38"), yaxis=dict(gridcolor="#2a2d38",tickprefix="$"))
            st.plotly_chart(fig, use_container_width=True)
        with ch2:
            pie_df = port_df[port_df["ValUSD"]>0]
            fig2 = go.Figure(go.Pie(labels=pie_df["Ticker"], values=pie_df["ValUSD"].round(2), hole=0.62,
                marker_colors=[COLORS.get(t,"#6b6f82") for t in pie_df["Ticker"]],
                marker_line=dict(color="#0d0e10",width=3),
                textinfo="label+percent", textfont=dict(color="#e2e4ef",size=11)))
            fig2.update_layout(title="Allocation", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef",size=13),
                margin=dict(t=40,b=10,l=10,r=10), height=240, showlegend=False)
            st.plotly_chart(fig2, use_container_width=True)
        with ch3:
            fig3 = go.Figure(go.Bar(x=port_df["Ticker"], y=port_df["PnL"],
                marker_color=[COLORS.get(t,"#6b6f82") for t in port_df["Ticker"]],
                marker_line_width=0,
                text=[f"${v:+,.0f}" for v in port_df["PnL"]], textposition="outside"))
            fig3.update_layout(title="P&L from Cost ($)", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef",size=13),
                margin=dict(t=40,b=20,l=20,r=20), height=240,
                xaxis=dict(gridcolor="#2a2d38"), yaxis=dict(gridcolor="#2a2d38",tickprefix="$"))
            st.plotly_chart(fig3, use_container_width=True)

        # Positions table
        st.markdown("### Open Positions")
        disp = port_df[["Ticker","Shares","AvgCost","Price","ValUSD","ValILS",
                         "DayChg","DayChgPct","WkChg","PnL","PnLPct","W"]].copy()
        disp.columns = ["Ticker","Shares","Avg Cost","Current","Value $","Value ₪",
                        "Day Chg $","Day Chg %","Week Chg $","P&L $","P&L %","Weight"]
        for c in ["Avg Cost","Current","Value $","P&L $"]:
            disp[c] = disp[c].map(lambda x: f"${x:,.2f}")
        disp["Value ₪"]   = disp["Value ₪"].map(lambda x: f"₪{x:,.0f}")
        disp["Day Chg $"] = disp["Day Chg $"].map(lambda x: "$0.00" if not market_open else f"${x:+,.2f}")
        disp["Day Chg %"] = disp["Day Chg %"].map(lambda x: "Closed" if not market_open else (f"{x:+.2f}%" if pd.notna(x) else "N/A"))
        disp["Week Chg $"]= disp["Week Chg $"].map(lambda x: f"${x:+,.2f}")
        disp["P&L %"]     = disp["P&L %"].map(lambda x: f"{x:+.2f}%")
        disp["Weight"]    = disp["Weight"].map(lambda x: f"{x:.1f}%")
        st.dataframe(disp, use_container_width=True, hide_index=True)

    # ── TAB 2: Charts ─────────────────────────────────────────────────────────
    with tab2:
        cc1,cc2,cc3 = st.columns([2,2,4])
        with cc1:
            period_sel = st.radio("Period", ["1D","1W","1M"], horizontal=True, key="chart_period")
        with cc2:
            view_sel   = st.radio("View",   ["% Change","$ Value"], horizontal=True, key="chart_view")
        with cc3:
            idx_sel = st.multiselect("Add Index", list(INDEX_META.keys()),
                format_func=lambda k: INDEX_META[k]["label"],
                placeholder="SPX, NDX, RUT, BTC, ETH...")

        p_range, p_interval = PERIOD_CFG[period_sel]

        with st.spinner("Building chart..."):
            port_hist = get_port_history(holdings, p_range, p_interval)

        if port_hist.empty:
            st.warning("No historical data available.")
        else:
            fig_c = go.Figure()
            x_axis = port_hist.index

            if view_sel == "% Change":
                base = port_hist.iloc[0]
                y_port = (port_hist / base - 1) * 100
                fig_c.add_trace(go.Scatter(x=x_axis, y=y_port, name="Portfolio",
                    line=dict(color=PORT_COLOR, width=2.5),
                    hovertemplate="%{y:.2f}%<extra>Portfolio</extra>"))
                # Indices
                for idx_key in idx_sel:
                    meta = INDEX_META[idx_key]
                    idx_hist = get_history(meta["yf"], p_range, p_interval)
                    if not idx_hist.empty:
                        idx_x = idx_hist.index
                        idx_y = (idx_hist / idx_hist.iloc[0] - 1) * 100
                        fig_c.add_trace(go.Scatter(x=idx_x, y=idx_y, name=meta["label"],
                            line=dict(color=meta["color"], width=1.8, dash="dot"),
                            hovertemplate="%{y:.2f}%<extra>"+meta["label"]+"</extra>"))
                fig_c.add_hline(y=0, line_dash="dash", line_color="#2a2d38", line_width=1)
                yaxis_title = "% Change"
            else:
                fig_c.add_trace(go.Scatter(x=x_axis, y=port_hist, name="Portfolio ($)",
                    line=dict(color=PORT_COLOR, width=2.5), fill="tozeroy",
                    fillcolor="rgba(62,207,142,0.07)",
                    hovertemplate="$%{y:,.2f}<extra>Portfolio</extra>"))
                if idx_sel:
                    for idx_key in idx_sel:
                        meta = INDEX_META[idx_key]
                        idx_hist = get_history(meta["yf"], p_range, p_interval)
                        if not idx_hist.empty:
                            idx_x = idx_hist.index
                            idx_y = (idx_hist / idx_hist.iloc[0] - 1) * 100
                            fig_c.add_trace(go.Scatter(x=idx_x, y=idx_y, name=meta["label"],
                                line=dict(color=meta["color"], width=1.8, dash="dot"),
                                yaxis="y2",
                                hovertemplate="%{y:.2f}%<extra>"+meta["label"]+"</extra>"))
                    fig_c.update_layout(yaxis2=dict(title="% Change (indices)",
                        overlaying="y", side="right",
                        gridcolor="#2a2d38", ticksuffix="%",
                        tickfont=dict(color="#6b6f82")))
                yaxis_title = "Portfolio Value ($)"

            period_label = {"1D":"Today","1W":"Last 5 Days","1M":"Last Month"}[period_sel]
            fig_c.update_layout(
                title=f"Portfolio Performance — {period_label}",
                paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af", size=12),
                title_font=dict(color="#e2e4ef", size=14),
                margin=dict(t=50,b=40,l=60,r=60), height=420,
                hovermode="x unified",
                legend=dict(bgcolor="#13151a", bordercolor="#2a2d38", borderwidth=1,
                            font=dict(color="#e2e4ef")),
                xaxis=dict(gridcolor="#1e2029", showgrid=True),
                yaxis=dict(gridcolor="#2a2d38", title=yaxis_title,
                           ticksuffix="%" if view_sel=="% Change" else "",
                           tickprefix="$" if view_sel=="$ Value" else ""),
            )
            st.plotly_chart(fig_c, use_container_width=True)

    # ── TAB 3: Returns ────────────────────────────────────────────────────────
    with tab3:
        st.markdown("### Portfolio & Stock Returns")
        with st.spinner("Computing returns..."):
            def get_return_pct(hist: pd.Series, current: float, period_key: str) -> float | None:
                if hist.empty or current == 0:
                    return None
                if period_key == "1D":
                    start = hist.iloc[-2] if len(hist)>=2 else None
                else:
                    start = hist.iloc[0]
                if start is None or start == 0:
                    return None
                return round((current - start) / start * 100, 2)

            rows = []
            # Portfolio row
            port_row = {"Name": "📦 Portfolio"}
            port_1d_pct = round(daily_chg_p, 2) if market_open else 0.0
            port_row["1D"] = port_1d_pct
            for period_key in ["1W","1M","1Y","YTD"]:
                r, iv = RETURN_CFG[period_key]
                ph = get_port_history(holdings, r, iv)
                if period_key == "1W":
                    val = get_return_pct(ph, total_usd, "1W")
                else:
                    val = (total_usd/ph.iloc[0]-1)*100 if not ph.empty and ph.iloc[0] else None
                port_row[period_key] = round(val, 2) if val is not None else None
            rows.append(port_row)

            # Per-stock rows
            for _, row in port_df.iterrows():
                t   = row["Ticker"]
                cur = prices.get(t, 0)
                r_  = {"Name": t}
                r_["1D"] = round(row["DayChgPct"],2) if market_open and pd.notna(row["DayChgPct"]) else 0.0
                for period_key in ["1W","1M","1Y","YTD"]:
                    rng, iv = RETURN_CFG[period_key]
                    hist_s  = get_history(t, rng, iv)
                    if period_key == "1W":
                        start = hist_s.iloc[0] if len(hist_s)>=2 else None
                    else:
                        start = hist_s.iloc[0] if not hist_s.empty else None
                    r_[period_key] = round((cur-start)/start*100,2) if start and start>0 else None
                rows.append(r_)

        ret_df = pd.DataFrame(rows).set_index("Name")

        # Style returns table
        def color_val(v):
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return "color:#6b6f82"
            return "color:#3ecf8e" if v>=0 else "color:#f56565"

        styled = ret_df.style.format(lambda v: fmt_pct(v) if v is not None else "N/A")            .applymap(lambda v: color_val(v))
        st.dataframe(styled, use_container_width=True)

    # ── TAB 4: DCA ────────────────────────────────────────────────────────────
    with tab4:
        st.markdown("### Annual DCA")
        dca_df = load_dca_from_sheets()

        if dca_df.empty:
            st.info("""**Setup required:** Add a sheet named **DCA** to your Google Sheets with these columns:
- **Date** (YYYY-MM-DD) — date of deposit
- **Amount** (USD) — deposit amount
- **Note** (optional) — description

Each row = one deposit/contribution.""")
        else:
            this_year = datetime.now().year
            ytd_dca   = dca_df[dca_df["Date"].dt.year == this_year]["Amount"].sum()
            total_dca = dca_df["Amount"].sum()

            d1,d2,d3,d4 = st.columns(4)
            d1.metric("YTD Invested",    f"${ytd_dca:,.0f}")
            d2.metric("Total Invested",  f"${total_dca:,.0f}")
            d3.metric("Portfolio Value", f"${total_usd:,.2f}")
            roi = (total_usd - total_dca) / total_dca * 100 if total_dca else 0
            d4.metric("ROI on DCA", f"{roi:+.1f}%")

            st.markdown("#### Monthly Contributions")
            dca_df["Month"] = dca_df["Date"].dt.to_period("M").astype(str)
            monthly = dca_df.groupby("Month")["Amount"].sum().reset_index()
            fig_dca = go.Figure(go.Bar(x=monthly["Month"], y=monthly["Amount"],
                marker_color="#4fa8f5", marker_line_width=0,
                text=[f"${v:,.0f}" for v in monthly["Amount"]], textposition="outside"))
            fig_dca.update_layout(
                title="DCA per Month ($)", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef",size=13),
                margin=dict(t=40,b=40,l=40,r=20), height=300,
                xaxis=dict(gridcolor="#2a2d38"), yaxis=dict(gridcolor="#2a2d38",tickprefix="$"))
            st.plotly_chart(fig_dca, use_container_width=True)

            st.markdown("#### Deposits Log")
            disp_dca = dca_df[["Date","Amount","Note"]].copy() if "Note" in dca_df.columns else dca_df[["Date","Amount"]].copy()
            disp_dca["Date"]   = disp_dca["Date"].dt.strftime("%Y-%m-%d")
            disp_dca["Amount"] = disp_dca["Amount"].map(lambda x: f"${x:,.2f}")
            st.dataframe(disp_dca, use_container_width=True, hide_index=True)

    st.divider()
    st.caption("📡 Stooq (price) · Yahoo Finance (history & prev close) · Bank of Israel (FX)")
    if st.button("🔄 Refresh"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


main()
