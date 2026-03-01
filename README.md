ClimatePulse 🌎

An end-to-end climate analytics pipeline comparing long-term temperature trends between Houston (IAH) and Newark (EWR) using NOAA GHCN Daily data (1970–2025).

## 📊 Executive Summary

Using 55 years of NOAA daily station data (1970–2025):

- Houston annual warming: +0.805°F per decade
- Newark annual warming: +0.472°F per decade
- Houston winter nighttime warming: +1.005°F per decade
- Houston Feb–March 80°F days: +1.721 additional days per decade

🔧 Architecture

Bronze → Silver → Gold pipeline structure:

Bronze: Raw NOAA daily station data

Silver: Cleaned + feature-engineered dataset

Gold: Aggregated seasonal summaries + regression trend metrics

📊 Key Insights
🌡 Annual Warming Trend (°F per decade)

Houston (IAH): +0.805°F

Newark (EWR): +0.472°F

❄️ Winter Minimum Warming (Nighttime Lows)

Houston: +1.005°F per decade

Newark: +0.79°F per decade

Winter nighttime temperatures are warming faster than annual averages.

🔥 February–March 80°F Days

Houston: +1.721 additional 80°F days per decade

Newark: +0.065 per decade

Houston shows significant seasonal shift in late-winter extreme heat frequency.

🛠 Tech Stack

Python

pandas

scikit-learn (Linear Regression)

Modular ETL design

Bronze–Silver–Gold architecture

🚀 How to Run
python main.py


### Architecture Diagram

# Bronze (Raw NOAA CSV)
    ↓
# Silver (Feature Engineering: tmean, winter_year, 80°F flag)
    ↓
# Gold (Monthly + Winter Aggregations)
    ↓
# Regression Trend Analysis
