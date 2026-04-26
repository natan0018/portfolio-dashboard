import streamlit as st
import yfinance as yf
import gspread
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from google.oauth2 import service_account
from datetime import datetime, date
import json
import time

# ─── PAGE CONFIG ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Portfolio Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed"
)

# ─── DARK THEME CSS ─────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300..700&family=JetBrains+Mono:wght@400;600&display=swap');
html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
.stApp { background-color: #0d0e10; }
.block-container { padding-top: 2rem; padding-bottom: 2rem; }
.metric-card {
    background: #13151a; border: 1px solid #2a2d38;
    border-radius: 12px; padding: 1.25rem 1.5rem;
}
.metric-label { font-size: 0.7rem; color: #6b6f82; text-transform: uppercase; letter-spacing: 0.06em; margin-bottom: 0.4rem; }
.metric-value { font-family: 'JetBrains Mono', monospace; font-size: 1.6rem; font-weight: 600; color: #e2e4ef; }
.metric-delta-up   { font-size: 0.75rem; color: #3ecf8e; font-weight: 600; }
.metric-delta-down { font-size: 0.75rem; color: #f56565; font-weight: 600; }
.ticker-tag {
    display: inline-block; padding: 2px 10px;
    border-radius: 999px; font-size: 0.75rem; font-weight: 700;
}
div[data-testid="stDataFrame"] { border-radius: 12px; overflow: hidden; }
</style>
""", unsafe_allow_html=True)

# ─── USD/ILS RATE ────────────────────────────────────────────────────────────
@st.cache_data(ttl=3600)
def get_usd_ils():
    try:
        t = yf.Ticker("USDILS=X")
        rate = t.fast_info.get("last_price") or t.info.get("regularMarketPrice", 3.65)
        return round(float(rate), 4)
    except:
        return 3.65

# ─── FETCH LIVE PRICES ───────────────────────────────────────────────────────
@st.cache_data(ttl=300)  # cache 5 min
def get_prices(tickers: list):
    prices = {}
    prev_prices = {}
    for t in tickers:
        try:
            tk = yf.Ticker(t)
            info = tk.fast_info
            prices[t] = round(float(info.get("last_price", 0)), 2)
            prev_prices[t] = round(float(info.get("previous_close", 0)), 2)
        except:
            prices[t] = 0.0
            prev_prices[t] = 0.0
    return prices, prev_prices

# ─── LOAD PORTFOLIO FROM GOOGLE SHEETS ──────────────────────────────────────
@st.cache_data(ttl=60)
def load_from_sheets():
    try:
        creds_dict = dict(st.secrets["gcp_service_account"])
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        credentials = service_account.Credentials.from_service_account_info(
            creds_dict, scopes=scopes
        )
        gc = gspread.authorize(credentials)
        sh = gc.open_by_key(st.secrets["sheet_id"])
        ws = sh.worksheet("Portfolio")
        data = ws.get_all_records()
        df = pd.DataFrame(data)
        df["Shares"] = pd.to_numeric(df["Shares"], errors="coerce")
        df["AvgCost"] = pd.to_numeric(df["AvgCost"], errors="coerce")
        return df.dropna(subset=["Ticker", "Shares", "AvgCost"])
    except Exception as e:
        st.warning(f"⚠️ לא ניתן לטעון מ-Google Sheets: {e}. משתמש בנתונים קשיחים.")
        return None

# ─── FALLBACK — HARDCODED (Blink data from 24 Apr 2026) ─────────────────────
def default_portfolio():
    return pd.DataFrame([
        {"Ticker": "IREN",  "Shares": 86.289,    "AvgCost": 41.87,  "Platform": "Blink"},
        {"Ticker": "BMNR",  "Shares": 227.9826,  "AvgCost": 19.44,  "Platform": "Blink"},
        {"Ticker": "MSTR",  "Shares": 19.0,      "AvgCost": 136.17, "Platform": "Blink"},
    ])

# ─── MAIN ────────────────────────────────────────────────────────────────────
def main():
    # Header
    col_title, col_meta = st.columns([3, 1])
    with col_title:
        st.markdown("## 📈 Portfolio Dashboard")
        st.caption(f"עדכון אחרון: {datetime.now().strftime('%d/%m/%Y %H:%M:%S IST')}")
    with col_meta:
        usd_ils = get_usd_ils()
        st.metric("USD/ILS", f"₪{usd_ils:.3f}")

    st.divider()

    # Load portfolio
    df = load_from_sheets()
    if df is None:
        df = default_portfolio()
        st.info("📋 מציג פורטפוליו ברירת מחדל (Blink · 24 Apr 2026). חבר Google Sheets לעדכון אוטומטי.")

    # Fetch live prices
    tickers = df["Ticker"].tolist()
    with st.spinner("🔄 טוען מחירים חיים מ-Yahoo Finance..."):
        prices, prev_prices = get_prices(tickers)

    # Enrich dataframe
    df["CurrentPrice"]  = df["Ticker"].map(prices)
    df["PrevPrice"]     = df["Ticker"].map(prev_prices)
    df["ValueUSD"]      = df["Shares"] * df["CurrentPrice"]
    df["ValueILS"]      = df["ValueUSD"] * usd_ils
    df["CostBasisUSD"]  = df["Shares"] * df["AvgCost"]
    df["PnL_USD"]       = df["ValueUSD"] - df["CostBasisUSD"]
    df["PnL_Pct"]       = (df["PnL_USD"] / df["CostBasisUSD"] * 100).round(2)
    df["DailyChgUSD"]   = df["Shares"] * (df["CurrentPrice"] - df["PrevPrice"])
    df["DailyChgPct"]   = ((df["CurrentPrice"] - df["PrevPrice"]) / df["PrevPrice"].replace(0, float("nan")) * 100).round(2)
    df["Weight"]        = (df["ValueUSD"] / df["ValueUSD"].sum() * 100).round(1)

    total_usd   = df["ValueUSD"].sum()
    total_ils   = df["ValueILS"].sum()
    total_pnl   = df["PnL_USD"].sum()
    total_cost  = df["CostBasisUSD"].sum()
    total_pnl_p = total_pnl / total_cost * 100
    daily_chg   = df["DailyChgUSD"].sum()
    daily_chg_p = daily_chg / (total_usd - daily_chg) * 100

    # ── KPI CARDS ────────────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-label">שווי תיק (₪)</div>
            <div class="metric-value">₪{total_ils:,.0f}</div>
            <div class="{'metric-delta-up' if daily_chg>=0 else 'metric-delta-down'}">
                {'▲' if daily_chg>=0 else '▼'} ${abs(daily_chg):,.2f} יומי ({daily_chg_p:+.2f}%)
            </div>
        </div>""", unsafe_allow_html=True)
    with c2:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-label">שווי תיק (USD)</div>
            <div class="metric-value">${total_usd:,.2f}</div>
            <div class="metric-delta-up">{len(df)} פוזיציות פתוחות</div>
        </div>""", unsafe_allow_html=True)
    with c3:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-label">רווח ממועד קנייה</div>
            <div class="metric-value">${total_pnl:+,.2f}</div>
            <div class="{'metric-delta-up' if total_pnl>=0 else 'metric-delta-down'}">
                {'▲' if total_pnl>=0 else '▼'} {total_pnl_p:+.2f}% על עלות
            </div>
        </div>""", unsafe_allow_html=True)
    with c4:
        st.markdown(f"""<div class="metric-card">
            <div class="metric-label">שינוי יומי (USD)</div>
            <div class="metric-value" style="color:{'#3ecf8e' if daily_chg>=0 else '#f56565'}">${daily_chg:+,.2f}</div>
            <div class="{'metric-delta-up' if daily_chg>=0 else 'metric-delta-down'}">
                {daily_chg_p:+.2f}% מאתמול
            </div>
        </div>""", unsafe_allow_html=True)

    st.markdown("<br>", unsafe_allow_html=True)

    # ── CHARTS ───────────────────────────────────────────────────────────────
    COLORS = {"IREN": "#4fa8f5", "BMNR": "#a78bfa", "MSTR": "#f5c842"}
    def get_color(ticker): return COLORS.get(ticker, "#6b6f82")

    ch1, ch2, ch3 = st.columns([2, 1, 1])

    with ch1:
        # Bar: daily change
        fig_bar = go.Figure(go.Bar(
            x=df["Ticker"],
            y=df["DailyChgUSD"],
            marker_color=[("#3ecf8e" if v >= 0 else "#f56565") for v in df["DailyChgUSD"]],
            marker_line_width=0,
            text=[f"${v:+,.2f}" for v in df["DailyChgUSD"]],
            textposition="outside",
        ))
        fig_bar.update_layout(
            title="שינוי יומי לפי מניה ($)",
            paper_bgcolor="#13151a", plot_bgcolor="#13151a",
            font=dict(color="#9ca3af", family="Inter"),
            title_font=dict(color="#e2e4ef", size=13),
            margin=dict(t=40, b=20, l=20, r=20),
            height=250,
            xaxis=dict(gridcolor="#2a2d38"),
            yaxis=dict(gridcolor="#2a2d38", tickprefix="$"),
            showlegend=False,
        )
        st.plotly_chart(fig_bar, use_container_width=True)

    with ch2:
        # Pie: allocation
        fig_pie = go.Figure(go.Pie(
            labels=df["Ticker"],
            values=df["ValueUSD"].round(2),
            hole=0.62,
            marker_colors=[get_color(t) for t in df["Ticker"]],
            marker_line=dict(color="#0d0e10", width=3),
            textinfo="label+percent",
            textfont=dict(color="#e2e4ef", size=11),
        ))
        fig_pie.update_layout(
            title="הקצאת תיק",
            paper_bgcolor="#13151a", plot_bgcolor="#13151a",
            font=dict(color="#9ca3af", family="Inter"),
            title_font=dict(color="#e2e4ef", size=13),
            margin=dict(t=40, b=10, l=10, r=10),
            height=250,
            showlegend=False,
        )
        st.plotly_chart(fig_pie, use_container_width=True)

    with ch3:
        # Bar: PnL since buy
        fig_pnl = go.Figure(go.Bar(
            x=df["Ticker"],
            y=df["PnL_USD"],
            marker_color=[get_color(t) for t in df["Ticker"]],
            marker_line_width=0,
            text=[f"+${v:,.0f}" for v in df["PnL_USD"]],
            textposition="outside",
        ))
        fig_pnl.update_layout(
            title="רווח ממועד קנייה ($)",
            paper_bgcolor="#13151a", plot_bgcolor="#13151a",
            font=dict(color="#9ca3af", family="Inter"),
            title_font=dict(color="#e2e4ef", size=13),
            margin=dict(t=40, b=20, l=20, r=20),
            height=250,
            xaxis=dict(gridcolor="#2a2d38"),
            yaxis=dict(gridcolor="#2a2d38", tickprefix="$"),
            showlegend=False,
        )
        st.plotly_chart(fig_pnl, use_container_width=True)

    # ── TABLE ─────────────────────────────────────────────────────────────────
    st.markdown("### פוזיציות פתוחות")
    display_df = df[[
        "Ticker", "Shares", "AvgCost", "CurrentPrice",
        "ValueUSD", "ValueILS", "DailyChgUSD", "DailyChgPct",
        "PnL_USD", "PnL_Pct", "Weight"
    ]].copy()
    display_df.columns = [
        "מניה", "מניות", "עלות ממוצעת ($)", "מחיר נוכחי ($)",
        "שווי (USD)", "שווי (₪)", "שינוי יומי ($)", "שינוי יומי (%)",
        "רווח כולל ($)", "רווח כולל (%)", "משקל (%)"
    ]
    # Format
    for col in ["עלות ממוצעת ($)", "מחיר נוכחי ($)", "שווי (USD)", "שינוי יומי ($)", "רווח כולל ($)"]:
        display_df[col] = display_df[col].map(lambda x: f"${x:,.2f}")
    display_df["שווי (₪)"] = display_df["שווי (₪)"].map(lambda x: f"₪{x:,.0f}")
    for col in ["שינוי יומי (%)", "רווח כולל (%)", "משקל (%)"]:
        display_df[col] = display_df[col].map(lambda x: f"{x:+.2f}%")

    st.dataframe(display_df, use_container_width=True, hide_index=True)

    # totals
    t1, t2, t3 = st.columns(3)
    t1.metric("סה״כ שווי (USD)", f"${total_usd:,.2f}")
    t2.metric("סה״כ שווי (₪)",  f"₪{total_ils:,.0f}")
    t3.metric("סה״כ שינוי יומי", f"${daily_chg:+,.2f}", f"{daily_chg_p:+.2f}%")

    # ── AUTO REFRESH ──────────────────────────────────────────────────────────
    st.divider()
    st.caption("⏱ נתונים מתרעננים כל 5 דקות אוטומטית · Yahoo Finance · USD/ILS מתעדכן כל שעה")
    if st.button("🔄 רענן עכשיו"):
        st.cache_data.clear()
        st.rerun()

main()
