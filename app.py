import streamlit as st
import gspread
import pandas as pd
import plotly.graph_objects as go
from google.oauth2 import service_account
from datetime import datetime, date, timezone
import requests
import io

st.set_page_config(page_title="Portfolio Dashboard", page_icon="📈", layout="wide")
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background-color: #0d0e10; }
.block-container { padding-top: 2rem; padding-bottom: 2rem; }
.metric-card { background: #13151a; border: 1px solid #2a2d38; border-radius: 12px; padding: 1.25rem 1.5rem; margin-bottom: 0.5rem; }
.metric-label { font-size: 0.7rem; color: #6b6f82; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem; }
.metric-value { font-family: 'JetBrains Mono', monospace; font-size: 1.6rem; font-weight: 600; color: #e2e4ef; }
.metric-sub { font-size: 0.75rem; color: #6b6f82; }
.metric-delta-up   { font-size: 0.75rem; color: #3ecf8e; font-weight: 600; }
.metric-delta-down { font-size: 0.75rem; color: #f56565; font-weight: 600; }
div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

_S = requests.Session()
_S.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
    "Accept-Language": "en-US,en;q=0.9",
})


@st.cache_resource
def _yf_session():
    """Yahoo Finance authenticated session for previousClose."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0.0.0",
        "Accept": "text/html,application/xhtml+xml,*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    s.get("https://fc.yahoo.com", timeout=6)
    crumb = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=6).text.strip()
    return s, crumb


@st.cache_data(ttl=300)
def get_prices(tickers: tuple) -> tuple:
    """
    Returns (prices, prev_prices, market_open_today).
    - prices        : last close from Stooq
    - prev_prices   : yesterday's close from Yahoo Finance (used ONLY when market is open today)
    - market_open   : True if last Stooq trade date == today (UTC)
    When market is CLOSED today → prev_price = current price → daily change = 0
    """
    prices, prev_prices = {t: 0.0 for t in tickers}, {t: 0.0 for t in tickers}
    today_utc = datetime.now(timezone.utc).date()
    market_open = False

    # ── Stooq: current price + detect if market open today ────────────────────
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
                last_date = pd.to_datetime(df["Date"].iloc[-1]).date()
                if last_date == today_utc:
                    market_open = True
        except Exception:
            pass

    if market_open:
        # Market is open today → fetch real previous close from Yahoo Finance
        try:
            yf_s, crumb = _yf_session()
            symbols = ",".join([t.strip().upper() for t in tickers])
            url  = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbols}&crumb={crumb}"
            data = yf_s.get(url, timeout=10).json()
            for q in data["quoteResponse"]["result"]:
                sym  = q.get("symbol", "").strip().upper()
                prev = q.get("regularMarketPreviousClose", 0)
                if sym in prev_prices and prev and prev > 0:
                    prev_prices[sym] = round(float(prev), 2)
        except Exception:
            pass
        # Fallback: if Yahoo failed for a ticker, prev = current (no change shown)
        for t in tickers:
            if prev_prices[t] == 0.0:
                prev_prices[t] = prices[t]
    else:
        # Market is CLOSED today → no change occurred today
        for t in tickers:
            prev_prices[t] = prices[t]  # change = 0

    return prices, prev_prices, market_open


@st.cache_data(ttl=3600)
def get_usd_ils() -> float:
    try:
        url  = ("https://edge.boi.org.il/FusionEdgeServer/sdmx/v2/data/dataflow/"
                "BOI.STATISTICS/EXR/1.0/RER_USD_ILS?format=json&lastNObservations=1")
        data = _S.get(url, timeout=6).json()
        obs  = list(data["data"]["dataSets"][0]["series"].values())[0]["observations"]
        rate = float(list(obs.values())[0][0])
        if 1.0 < rate < 10.0:
            return round(rate, 4)
    except Exception:
        pass
    try:
        rate = _S.get("https://api.exchangerate-api.com/v4/latest/USD", timeout=6).json()["rates"]["ILS"]
        if 1.0 < float(rate) < 10.0:
            return round(float(rate), 4)
    except Exception:
        pass
    return 3.60


@st.cache_data(ttl=60)
def load_from_sheets():
    try:
        creds = dict(st.secrets["gcp_service_account"])
        scopes = ["https://www.googleapis.com/auth/spreadsheets",
                  "https://www.googleapis.com/auth/drive"]
        credentials = service_account.Credentials.from_service_account_info(creds, scopes=scopes)
        gc = gspread.authorize(credentials)
        ws = gc.open_by_key(st.secrets["sheet_id"]).worksheet("Sheet1")
        df = pd.DataFrame(ws.get_all_records())
        df["Shares"]  = pd.to_numeric(df["Shares"],  errors="coerce")
        df["AvgCost"] = pd.to_numeric(df["AvgCost"], errors="coerce")
        df["Ticker"]  = df["Ticker"].astype(str).str.strip().str.upper()
        return df.dropna(subset=["Ticker", "Shares", "AvgCost"])
    except Exception as e:
        st.warning(f"Google Sheets: {e}")
        return None


def default_portfolio():
    return pd.DataFrame([
        {"Ticker": "IREN",  "Shares": 86.289,   "AvgCost": 41.87},
        {"Ticker": "BMNR",  "Shares": 227.9826, "AvgCost": 19.44},
        {"Ticker": "MSTR",  "Shares": 19.0,     "AvgCost": 136.17},
    ])


def main():
    col_title, col_meta = st.columns([3, 1])
    with col_title:
        st.markdown("## 📈 Portfolio Dashboard")
        st.caption(f"Last update: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    with col_meta:
        usd_ils = st.number_input("USD / ILS", min_value=2.5, max_value=4.5,
                                  value=float(get_usd_ils()), step=0.0001, format="%.4f",
                                  help="Auto: Bank of Israel.")
    st.divider()

    df = load_from_sheets()
    if df is None:
        df = default_portfolio()
        st.info("Using default portfolio (Google Sheets unavailable).")

    tickers = tuple(df["Ticker"].tolist())
    with st.spinner("Loading prices..."):
        prices, prev_prices, market_open = get_prices(tickers)

    if market_open:
        st.success("🟢 Market open — showing live daily change")
    else:
        st.info("🔴 Market closed — daily change: $0.00 (no trading today)")

    failed = [t for t in tickers if prices.get(t, 0.0) == 0.0]
    if failed:
        st.error(f"Could not fetch: {", ".join(failed)}")

    df["CurrentPrice"] = df["Ticker"].map(prices)
    df["PrevPrice"]    = df["Ticker"].map(prev_prices)
    df["ValueUSD"]     = df["Shares"] * df["CurrentPrice"]
    df["ValueILS"]     = df["ValueUSD"] * usd_ils
    df["CostBasisUSD"] = df["Shares"] * df["AvgCost"]
    df["PnL_USD"]      = df["ValueUSD"] - df["CostBasisUSD"]
    df["PnL_Pct"]      = (df["PnL_USD"] / df["CostBasisUSD"] * 100).round(2)
    df["DailyChgUSD"]  = df["Shares"] * (df["CurrentPrice"] - df["PrevPrice"])
    df["DailyChgPct"]  = (
        (df["CurrentPrice"] - df["PrevPrice"])
        / df["PrevPrice"].replace(0, float("nan")) * 100
    ).round(2)
    total_val   = df["ValueUSD"].sum()
    df["Weight"] = (df["ValueUSD"] / total_val * 100).round(1) if total_val else 0.0

    total_usd   = df["ValueUSD"].sum()
    total_ils   = df["ValueILS"].sum()
    total_pnl   = df["PnL_USD"].sum()
    total_cost  = df["CostBasisUSD"].sum()
    total_pnl_p = (total_pnl / total_cost * 100) if total_cost else 0.0
    daily_chg   = df["DailyChgUSD"].sum()
    daily_chg_p = (df["DailyChgUSD"].sum() / (total_usd - daily_chg) * 100) if (total_usd - daily_chg) != 0 else 0.0

    day_label = "Daily Change" if market_open else "Daily Change (Closed)"

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        cls = "metric-delta-up" if daily_chg >= 0 else "metric-delta-down"
        st.markdown(f'''<div class="metric-card">
<div class="metric-label">Portfolio Value (ILS)</div>
<div class="metric-value">₪{total_ils:,.0f}</div>
<div class="{cls}">${abs(daily_chg):,.2f} ({daily_chg_p:+.2f}%)</div>
</div>''', unsafe_allow_html=True)
    with c2:
        st.markdown(f'''<div class="metric-card">
<div class="metric-label">Portfolio Value (USD)</div>
<div class="metric-value">${total_usd:,.2f}</div>
<div class="metric-sub">{len(df)} positions</div>
</div>''', unsafe_allow_html=True)
    with c3:
        cls = "metric-delta-up" if total_pnl >= 0 else "metric-delta-down"
        st.markdown(f'''<div class="metric-card">
<div class="metric-label">Total P&amp;L</div>
<div class="metric-value">${total_pnl:+,.2f}</div>
<div class="{cls}">{total_pnl_p:+.2f}%</div>
</div>''', unsafe_allow_html=True)
    with c4:
        color = "#3ecf8e" if daily_chg >= 0 else "#f56565"
        cls   = "metric-delta-up" if daily_chg >= 0 else "metric-delta-down"
        chg_display = f"${daily_chg:+,.2f}" if market_open else "$0.00"
        pct_display = f"{daily_chg_p:+.2f}%" if market_open else "Market Closed"
        st.markdown(f'''<div class="metric-card">
<div class="metric-label">{day_label}</div>
<div class="metric-value" style="color:{color}">{chg_display}</div>
<div class="{cls}">{pct_display}</div>
</div>''', unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)
    COLORS = {"IREN": "#4fa8f5", "BMNR": "#a78bfa", "MSTR": "#f5c842"}
    def gc_(t): return COLORS.get(t, "#6b6f82")

    ch1, ch2, ch3 = st.columns([2, 1, 1])
    with ch1:
        bar_vals = df["DailyChgUSD"] if market_open else pd.Series([0.0]*len(df), index=df.index)
        fig = go.Figure(go.Bar(
            x=df["Ticker"], y=bar_vals,
            marker_color=[("#3ecf8e" if v >= 0 else "#f56565") for v in bar_vals],
            marker_line_width=0,
            text=[f"${v:+,.2f}" for v in bar_vals], textposition="outside"))
        title_txt = "Daily Change ($)" if market_open else "Daily Change — Market Closed"
        fig.update_layout(title=title_txt, paper_bgcolor="#13151a", plot_bgcolor="#13151a",
            font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef", size=13),
            margin=dict(t=40,b=20,l=20,r=20), height=260,
            xaxis=dict(gridcolor="#2a2d38"), yaxis=dict(gridcolor="#2a2d38", tickprefix="$"))
        st.plotly_chart(fig, use_container_width=True)
    with ch2:
        pie_df = df[df["ValueUSD"] > 0]
        if not pie_df.empty:
            fig2 = go.Figure(go.Pie(labels=pie_df["Ticker"], values=pie_df["ValueUSD"].round(2),
                hole=0.62, marker_colors=[gc_(t) for t in pie_df["Ticker"]],
                marker_line=dict(color="#0d0e10", width=3),
                textinfo="label+percent", textfont=dict(color="#e2e4ef", size=11)))
            fig2.update_layout(title="Allocation", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
                font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef", size=13),
                margin=dict(t=40,b=10,l=10,r=10), height=260, showlegend=False)
            st.plotly_chart(fig2, use_container_width=True)
    with ch3:
        fig3 = go.Figure(go.Bar(x=df["Ticker"], y=df["PnL_USD"],
            marker_color=[gc_(t) for t in df["Ticker"]], marker_line_width=0,
            text=[f"${v:+,.0f}" for v in df["PnL_USD"]], textposition="outside"))
        fig3.update_layout(title="P&L from Cost ($)", paper_bgcolor="#13151a", plot_bgcolor="#13151a",
            font=dict(color="#9ca3af"), title_font=dict(color="#e2e4ef", size=13),
            margin=dict(t=40,b=20,l=20,r=20), height=260,
            xaxis=dict(gridcolor="#2a2d38"), yaxis=dict(gridcolor="#2a2d38", tickprefix="$"))
        st.plotly_chart(fig3, use_container_width=True)

    st.markdown("### Open Positions")
    disp = df[["Ticker","Shares","AvgCost","CurrentPrice","ValueUSD","ValueILS",
               "DailyChgUSD","DailyChgPct","PnL_USD","PnL_Pct","Weight"]].copy()
    disp.columns = ["Ticker","Shares","Avg Cost ($)","Current ($)","Value (USD)","Value (ILS)",
                    "Day Chg ($)","Day Chg (%)","P&L ($)","P&L (%)","Weight (%)"]
    for col in ["Avg Cost ($)","Current ($)","Value (USD)","Day Chg ($)","P&L ($)"]:
        disp[col] = disp[col].map(lambda x: f"${x:,.2f}")
    disp["Value (ILS)"] = disp["Value (ILS)"].map(lambda x: f"₪{x:,.0f}")
    disp["Day Chg ($)"] = disp["Day Chg ($)"].map(lambda x: "$0.00" if not market_open else x)
    disp["Day Chg (%)"] = disp["Day Chg (%)"].map(
        lambda x: "Closed" if not market_open else (f"{x:+.2f}%" if pd.notna(x) else "N/A"))
    disp["P&L (%)"]     = disp["P&L (%)"].map(lambda x: f"{x:+.2f}%")
    disp["Weight (%)"]  = disp["Weight (%)"].map(lambda x: f"{x:.1f}%")
    st.dataframe(disp, use_container_width=True, hide_index=True)

    t1, t2, t3 = st.columns(3)
    t1.metric("Total (USD)", f"${total_usd:,.2f}")
    t2.metric("Total (ILS)", f"₪{total_ils:,.0f}")
    chg_str = f"${daily_chg:+,.2f}" if market_open else "$0.00"
    t3.metric("Day Change", chg_str)

    st.divider()
    st.caption("📡 Stooq (price) · Yahoo Finance (prev close) · Bank of Israel (FX) · Cache 5 min")
    if st.button("🔄 Refresh Now"):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()


main()
