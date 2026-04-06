"""
Price Monitor Dashboard — Streamlit App
========================================

4-page dashboard for visualizing pipeline data:
  1. Price Trends — daily price time series with IQR bands
  2. Price vs CPI — compare product prices to official CPI indices
  3. Data Quality — check results, anomalies, historical metrics
  4. Pipeline Ops — collection history, freshness, throughput

Launch:
  cd src && streamlit run dashboard.py
  OR:  python main.py dashboard
"""

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import psycopg2
from datetime import datetime

# ── DB Connection ────────────────────────────────────────────────────

# Import config from sibling package
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from pipeline.config import get_db_params


@st.cache_resource
def get_connection():
    """Cached DB connection (shared across reruns)."""
    return psycopg2.connect(**get_db_params())


def query(sql: str, params=None) -> pd.DataFrame:
    """Run a SQL query and return a DataFrame."""
    conn = get_connection()
    try:
        return pd.read_sql(sql, conn, params=params)
    except Exception:
        # Reconnect on stale connection
        conn = psycopg2.connect(**get_db_params())
        st.cache_resource.clear()
        return pd.read_sql(sql, conn, params=params)


# ── Page Config ──────────────────────────────────────────────────────

st.set_page_config(
    page_title="Price Monitor",
    page_icon="📊",
    layout="wide",
)

# ── Sidebar Navigation ──────────────────────────────────────────────

st.sidebar.title("Price Monitor")
page = st.sidebar.radio(
    "Navigate",
    ["Price Trends", "Price vs CPI", "Data Quality", "Pipeline Ops"],
)

st.sidebar.markdown("---")
st.sidebar.caption("Real-Time Price Monitoring Pipeline")
st.sidebar.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')}")
if st.sidebar.button("Refresh Data"):
    st.cache_data.clear()
    st.rerun()


# ══════════════════════════════════════════════════════════════════════
# PAGE 1: Price Trends
# ══════════════════════════════════════════════════════════════════════

def page_price_trends():
    st.header("Price Trends")
    st.caption("Daily price aggregates from KOSTAT product data")

    # Load available items
    items_df = query("""
        SELECT DISTINCT item_code, item_name
        FROM mart.daily_price_summary
        ORDER BY item_code
    """)

    if items_df.empty:
        st.warning("No aggregated price data yet. Run `python main.py aggregate` first.")
        return

    # Item selector
    options = items_df.apply(lambda r: f"{r['item_code']} — {r['item_name']}", axis=1).tolist()
    selected = st.selectbox("Select item category", options, index=0)
    item_code = selected.split(" — ")[0]

    # Load price data for selected item
    df = query("""
        SELECT price_date, product_count, median_price, mean_price,
               min_price, max_price, p25_price, p75_price, median_discount
        FROM mart.daily_price_summary
        WHERE item_code = %s
        ORDER BY price_date
    """, (item_code,))

    if df.empty:
        st.info("No data for this item.")
        return

    # Time series chart with IQR band
    fig = go.Figure()

    # IQR shading (p25 to p75)
    fig.add_trace(go.Scatter(
        x=pd.concat([df["price_date"], df["price_date"][::-1]]),
        y=pd.concat([df["p75_price"], df["p25_price"][::-1]]),
        fill="toself",
        fillcolor="rgba(99, 110, 250, 0.15)",
        line=dict(color="rgba(255,255,255,0)"),
        name="IQR (P25–P75)",
        hoverinfo="skip",
    ))

    # Median line
    fig.add_trace(go.Scatter(
        x=df["price_date"], y=df["median_price"],
        mode="lines+markers",
        name="Median Price",
        line=dict(color="#636EFA", width=2),
        marker=dict(size=6),
    ))

    # Mean line (dashed)
    fig.add_trace(go.Scatter(
        x=df["price_date"], y=df["mean_price"],
        mode="lines",
        name="Mean Price",
        line=dict(color="#EF553B", width=1, dash="dash"),
    ))

    fig.update_layout(
        title=f"Daily Prices — {selected}",
        xaxis_title="Date",
        yaxis_title="Price (KRW)",
        hovermode="x unified",
        height=450,
    )
    st.plotly_chart(fig, use_container_width=True)

    # Summary table
    st.subheader("Summary Statistics")
    latest = df.iloc[-1]
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Products Listed", f"{int(latest['product_count']):,}")
    col2.metric("Median Price", f"₩{int(latest['median_price']):,}")
    col3.metric("Price Range",
                f"₩{int(latest['min_price']):,} – ₩{int(latest['max_price']):,}")
    discount = latest["median_discount"]
    col4.metric("Median Discount",
                f"{discount*100:.1f}%" if discount and discount > 0 else "N/A")

    # Detail table
    with st.expander("View raw data"):
        display_df = df.copy()
        display_df["price_date"] = display_df["price_date"].astype(str)
        for col in ["median_price", "mean_price", "min_price", "max_price", "p25_price", "p75_price"]:
            if col in display_df.columns:
                display_df[col] = display_df[col].apply(
                    lambda x: f"₩{int(x):,}" if pd.notna(x) else "–"
                )
        st.dataframe(display_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE 2: Price vs CPI
# ══════════════════════════════════════════════════════════════════════

def page_price_vs_cpi():
    st.header("Price vs CPI")
    st.caption("Compare product-level prices against official Consumer Price Index")

    # Load mapped items
    mapped_df = query("""
        SELECT DISTINCT m.kostat_code, m.kostat_name, m.ecos_code, m.ecos_name
        FROM mart.item_mapping m
        JOIN mart.daily_price_summary d ON d.item_code = m.kostat_code
        JOIN mart.monthly_cpi_index c ON c.item_code = m.ecos_code
        ORDER BY m.kostat_code
    """)

    if mapped_df.empty:
        st.warning("No mapped items with both price and CPI data available.")
        return

    options = mapped_df.apply(
        lambda r: f"{r['kostat_code']} — {r['kostat_name']}", axis=1
    ).tolist()
    selected = st.selectbox("Select item (mapped to CPI)", options, index=0)
    kostat_code = selected.split(" — ")[0]

    mapping = mapped_df[mapped_df["kostat_code"] == kostat_code].iloc[0]
    ecos_code = mapping["ecos_code"]

    # Load both datasets
    price_df = query("""
        SELECT price_date, median_price, product_count
        FROM mart.daily_price_summary
        WHERE item_code = %s
        ORDER BY price_date
    """, (kostat_code,))

    cpi_df = query("""
        SELECT year_month, index_value, item_name
        FROM mart.monthly_cpi_index
        WHERE item_code = %s
        ORDER BY year_month
    """, (ecos_code,))

    if price_df.empty or cpi_df.empty:
        st.info("Insufficient data for comparison.")
        return

    # Dual-axis chart
    fig = go.Figure()

    fig.add_trace(go.Scatter(
        x=price_df["price_date"],
        y=price_df["median_price"],
        name="Median Price (KRW)",
        line=dict(color="#636EFA", width=2),
        yaxis="y",
    ))

    # Convert YYYYMM to midpoint date for CPI
    cpi_df["date"] = pd.to_datetime(cpi_df["year_month"], format="%Y%m") + pd.Timedelta(days=14)
    fig.add_trace(go.Scatter(
        x=cpi_df["date"],
        y=cpi_df["index_value"],
        name="CPI Index (2020=100)",
        line=dict(color="#EF553B", width=2, dash="dot"),
        yaxis="y2",
    ))

    fig.update_layout(
        title=f"Price vs CPI — {mapping['kostat_name']}",
        xaxis_title="Date",
        yaxis=dict(title=dict(text="Median Price (KRW)", font=dict(color="#636EFA")),
                   tickfont=dict(color="#636EFA")),
        yaxis2=dict(title=dict(text="CPI Index (2020=100)", font=dict(color="#EF553B")),
                    tickfont=dict(color="#EF553B"), overlaying="y", side="right"),
        hovermode="x unified",
        height=450,
        legend=dict(orientation="h", yanchor="bottom", y=1.02),
    )
    st.plotly_chart(fig, use_container_width=True)

    # Insight callout
    if len(price_df) >= 2 and len(cpi_df) >= 2:
        price_start = price_df.iloc[0]["median_price"]
        price_end = price_df.iloc[-1]["median_price"]
        cpi_start = cpi_df.iloc[0]["index_value"]
        cpi_end = cpi_df.iloc[-1]["index_value"]

        if price_start and price_start > 0 and cpi_start and cpi_start > 0:
            price_chg = (price_end - price_start) / price_start * 100
            cpi_chg = (cpi_end - cpi_start) / cpi_start * 100

            delta_sign = "+" if price_chg > 0 else ""
            cpi_sign = "+" if cpi_chg > 0 else ""

            st.info(
                f"**{mapping['kostat_name']}**: Product prices changed "
                f"**{delta_sign}{price_chg:.1f}%** while the CPI index changed "
                f"**{cpi_sign}{cpi_chg:.1f}%** over this period."
            )

    # CPI details
    with st.expander("CPI data details"):
        display_cpi = cpi_df[["year_month", "index_value", "item_name"]].copy()
        st.dataframe(display_cpi, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE 3: Data Quality
# ══════════════════════════════════════════════════════════════════════

def page_data_quality():
    st.header("Data Quality Health")
    st.caption("Quality check results, anomalies, and historical metrics")

    # Latest check results
    latest_df = query("""
        SELECT DISTINCT ON (check_name)
            check_name, status, metric_value, threshold, details, checked_at
        FROM raw.quality_check_log
        ORDER BY check_name, checked_at DESC
    """)

    if latest_df.empty:
        st.warning("No quality check results. Run `python main.py validate` first.")
        return

    # Status cards
    st.subheader("Latest Check Results")
    cols = st.columns(min(len(latest_df), 3))
    for i, (_, row) in enumerate(latest_df.iterrows()):
        col = cols[i % len(cols)]
        status = row["status"]
        color = {"PASS": "🟢", "WARN": "🟡", "FAIL": "🔴"}.get(status, "⚪")
        with col:
            st.markdown(f"### {color} {row['check_name']}")
            st.markdown(f"**Status:** {status}")
            if row["metric_value"] is not None:
                st.markdown(f"**Value:** {row['metric_value']}")
            if row["threshold"] is not None:
                st.markdown(f"**Threshold:** {row['threshold']}")
            st.caption(f"Checked: {row['checked_at']}")

    # Historical trend
    st.subheader("Check History")
    history_df = query("""
        SELECT check_name, status, metric_value, checked_at
        FROM raw.quality_check_log
        ORDER BY checked_at
    """)

    if not history_df.empty and len(history_df) > 1:
        # Filter to numeric checks
        numeric_df = history_df[history_df["metric_value"].notna()].copy()
        if not numeric_df.empty:
            fig = px.line(
                numeric_df,
                x="checked_at",
                y="metric_value",
                color="check_name",
                title="Quality Metrics Over Time",
                markers=True,
            )
            fig.update_layout(height=350, xaxis_title="Time", yaxis_title="Metric Value")
            st.plotly_chart(fig, use_container_width=True)

    # Active anomalies
    st.subheader("Price Anomalies")
    anomalies_df = query("""
        SELECT item_code, item_name, price_date, previous_median,
               current_median, pct_change, iqr_range, flagged_at
        FROM mart.price_anomalies
        WHERE resolved = FALSE
        ORDER BY flagged_at DESC
        LIMIT 50
    """)

    if anomalies_df.empty:
        st.success("No active anomalies.")
    else:
        st.warning(f"{len(anomalies_df)} unresolved anomalies")
        st.dataframe(anomalies_df, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════
# PAGE 4: Pipeline Operations
# ══════════════════════════════════════════════════════════════════════

def page_pipeline_ops():
    st.header("Pipeline Operations")
    st.caption("Collection history, throughput, and data freshness")

    # Data freshness indicators
    st.subheader("Data Freshness")
    col1, col2 = st.columns(2)

    kostat_fresh = query("""
        SELECT MAX(price_date) AS latest_date,
               NOW()::date - MAX(price_date) AS age_days
        FROM raw.kostat_products
    """)
    ecos_fresh = query("""
        SELECT MAX(time_period) AS latest_period
        FROM raw.ecos_indices
    """)

    with col1:
        if not kostat_fresh.empty and kostat_fresh.iloc[0]["latest_date"]:
            row = kostat_fresh.iloc[0]
            age = int(row["age_days"]) if pd.notna(row["age_days"]) else "?"
            st.metric("KOSTAT Latest", str(row["latest_date"]), f"{age} days ago")
        else:
            st.metric("KOSTAT Latest", "No data", "–")

    with col2:
        if not ecos_fresh.empty and ecos_fresh.iloc[0]["latest_period"]:
            period = ecos_fresh.iloc[0]["latest_period"]
            st.metric("ECOS Latest", period, "Monthly")
        else:
            st.metric("ECOS Latest", "No data", "–")

    # Collection history
    st.subheader("Collection History")
    log_df = query("""
        SELECT source, endpoint, status, records_fetched,
               started_at, finished_at,
               EXTRACT(EPOCH FROM (finished_at - started_at))::INTEGER AS duration_s,
               error_message
        FROM raw.collection_log
        ORDER BY started_at DESC
        LIMIT 50
    """)

    if log_df.empty:
        st.info("No collection logs yet.")
        return

    # Timeline: success vs failure
    log_df["status_color"] = log_df["status"].map({
        "SUCCESS": "green", "FAILED": "red", "RUNNING": "orange", "PARTIAL": "yellow"
    }).fillna("gray")

    fig = px.scatter(
        log_df,
        x="started_at",
        y="source",
        color="status",
        size="records_fetched",
        size_max=20,
        title="Collection Timeline",
        color_discrete_map={
            "SUCCESS": "#2ECC71", "FAILED": "#E74C3C",
            "RUNNING": "#F39C12", "PARTIAL": "#F1C40F"
        },
        hover_data=["endpoint", "records_fetched", "duration_s"],
    )
    fig.update_layout(height=300, xaxis_title="Time", yaxis_title="Source")
    st.plotly_chart(fig, use_container_width=True)

    # Records per run bar chart
    success_df = log_df[log_df["status"] == "SUCCESS"].copy()
    if not success_df.empty:
        fig2 = px.bar(
            success_df,
            x="started_at",
            y="records_fetched",
            color="source",
            title="Records Collected Per Run",
            color_discrete_map={"KOSTAT": "#636EFA", "ECOS": "#EF553B"},
        )
        fig2.update_layout(height=300, xaxis_title="Run Time", yaxis_title="Records")
        st.plotly_chart(fig2, use_container_width=True)

    # Log table
    with st.expander("Full collection log"):
        display_log = log_df[[
            "source", "endpoint", "status", "records_fetched",
            "started_at", "duration_s", "error_message"
        ]].copy()
        display_log["started_at"] = display_log["started_at"].astype(str).str[:19]
        st.dataframe(display_log, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════
# Page Router
# ══════════════════════════════════════════════════════════════════════

if page == "Price Trends":
    page_price_trends()
elif page == "Price vs CPI":
    page_price_vs_cpi()
elif page == "Data Quality":
    page_data_quality()
elif page == "Pipeline Ops":
    page_pipeline_ops()
