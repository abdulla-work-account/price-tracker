"""
Competitor Price Dashboard (Streamlit)
--------------------------------------
Reads data.csv directly from the GitHub repository (raw file URL) and shows
latest prices, a price history chart, and download buttons.

BEFORE DEPLOYING: edit RAW_CSV_URL below with your GitHub username and repo name.
"""

import io
import time

import altair as alt
import pandas as pd
import requests
import streamlit as st

# ---------------------------------------------------------------------------
# EDIT THIS ONE LINE. Replace YOUR_GITHUB_USERNAME and YOUR_REPO_NAME.
# Example: https://raw.githubusercontent.com/abdulla-fp/chewy-price-tracker/main/data.csv
# ---------------------------------------------------------------------------
RAW_CSV_URL = "https://raw.githubusercontent.com/YOUR_GITHUB_USERNAME/YOUR_REPO_NAME/main/data.csv"

# Freshpet internal design system palette
FP_GREEN = "#286140"
FP_INK = "#35312E"
FP_CREAM = "#F8F3EF"
FP_GOLD = "#B8860B"
FP_GRAY = "#A4A3A4"
FP_LINE_COLORS = ["#286140", "#B8860B", "#95B09E", "#C8D6A0", "#FCD3BC", "#A4A3A4"]

st.set_page_config(page_title="Competitor Price Tracker", layout="wide")

st.markdown(
    f"""
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:ital,wght@0,400;0,500;0,600;0,700;0,800;1,400;1,600&display=swap" rel="stylesheet">
    <style>
      html, body, [class*="css"], .stMarkdown, .stDataFrame {{
        font-family: 'Montserrat', system-ui, sans-serif;
        color: {FP_INK};
      }}
      .stApp {{ background-color: {FP_CREAM}; }}
      h1, h2, h3 {{ color: {FP_GREEN}; font-weight: 700; letter-spacing: -0.01em; }}
      .fp-eyebrow {{
        font-size: 11px; text-transform: uppercase; letter-spacing: 0.12em;
        font-weight: 700; color: {FP_GOLD};
      }}
      div.stButton > button, div.stDownloadButton > button {{
        background-color: {FP_GREEN}; color: #FFFFFF; border: none;
        border-radius: 2px; box-shadow: none; font-weight: 600;
      }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_data(ttl=300)  # Re-fetch from GitHub at most every 5 minutes
def load_data(cache_buster: int) -> pd.DataFrame:
    # The cache_buster value is added to the URL so GitHub's CDN cannot serve
    # a stale copy of the file after a new scrape has been committed.
    url = f"{RAW_CSV_URL}?nocache={cache_buster}"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    df = pd.read_csv(io.StringIO(response.text))
    df["Date of Scrape"] = pd.to_datetime(df["Date of Scrape"], errors="coerce")
    # Numeric price column for charting ("Error - Check Page" becomes blank)
    df["Price (numeric)"] = pd.to_numeric(
        df["Retail Price (One-Time)"].astype(str).str.replace(r"[$,]", "", regex=True),
        errors="coerce",
    )
    return df


st.markdown('<div class="fp-eyebrow">E-Commerce Competitive Intelligence</div>', unsafe_allow_html=True)
st.title("Chewy Competitor Price Tracker")
st.caption("Source: daily automated scrape committed to GitHub. Rows marked 'Error - Check Page' need a manual look.")

if st.button("Refresh data"):
    st.cache_data.clear()

try:
    df = load_data(cache_buster=int(time.time() // 300))
except Exception as exc:
    st.error(
        "Could not load data.csv from GitHub. Check that RAW_CSV_URL in app.py "
        f"is correct and that the scraper has run at least once. Details: {exc}"
    )
    st.stop()

if df.empty:
    st.warning("data.csv exists but has no rows yet. Run the GitHub Action once.")
    st.stop()

# --- Sidebar filters ---
st.sidebar.header("Filters")
products = sorted(df["Product Name"].dropna().unique())
selected = st.sidebar.multiselect("Products", products, default=products)
min_d, max_d = df["Date of Scrape"].min(), df["Date of Scrape"].max()
date_range = st.sidebar.date_input("Date range", value=(min_d, max_d))

filtered = df[df["Product Name"].isin(selected)]
if isinstance(date_range, tuple) and len(date_range) == 2:
    start, end = pd.to_datetime(date_range[0]), pd.to_datetime(date_range[1])
    filtered = filtered[(filtered["Date of Scrape"] >= start) & (filtered["Date of Scrape"] <= end)]

# --- Latest snapshot ---
st.header("Latest Prices")
latest = (
    filtered.sort_values("Date of Scrape")
    .groupby("Product Name", as_index=False)
    .tail(1)
    .sort_values("Product Name")
)
display_cols = ["Date of Scrape", "Retailer", "Product Name", "Size",
                "Retail Price (One-Time)", "Status", "Product URL"]
st.dataframe(latest[display_cols], use_container_width=True, hide_index=True)

# --- Price history chart ---
st.header("Price History")
chart_data = filtered.dropna(subset=["Price (numeric)"])
if chart_data.empty:
    st.info("No numeric prices to chart yet (or all rows are errors).")
else:
    chart = (
        alt.Chart(chart_data)
        .mark_line(point=True)
        .encode(
            x=alt.X("Date of Scrape:T", title="Date"),
            y=alt.Y("Price (numeric):Q", title="One-Time Price ($)", scale=alt.Scale(zero=False)),
            color=alt.Color("Product Name:N", scale=alt.Scale(range=FP_LINE_COLORS),
                            legend=alt.Legend(orient="bottom")),
            tooltip=["Date of Scrape:T", "Product Name:N", "Retail Price (One-Time):N", "Status:N"],
        )
        .configure(font="Montserrat")
        .configure_axis(gridColor="#E7E6E6", labelColor=FP_INK, titleColor=FP_INK)
        .properties(height=420)
    )
    st.altair_chart(chart, use_container_width=True)

# --- Full history + downloads ---
st.header("Full History")
st.dataframe(filtered[display_cols].sort_values("Date of Scrape", ascending=False),
             use_container_width=True, hide_index=True)

csv_bytes = filtered[display_cols].to_csv(index=False).encode("utf-8")

excel_buffer = io.BytesIO()
with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
    filtered[display_cols].to_excel(writer, index=False, sheet_name="Price Data")

col1, col2 = st.columns(2)
with col1:
    st.download_button("Download as Excel (.xlsx)", data=excel_buffer.getvalue(),
                       file_name="competitor_prices.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
with col2:
    st.download_button("Download as CSV", data=csv_bytes,
                       file_name="competitor_prices.csv", mime="text/csv")
