from src.transform import clean_daily, monthly_summary

# Step 1: Clean data
df = clean_daily("data/bronze/daily_raw.csv")

# Step 2: Split stations (optional)
iah = df[df['STATION'] == "USW00012960"]
newark = df[df['STATION'] == "USW00014734"]

# Step 3: Create monthly gold table
monthly = monthly_summary(df)

print(df.head())
print(monthly.head())

# Optional: save outputs
monthly.to_parquet("data/gold/monthly_summary.parquet", index=False)

print("\nStations present:")
print(df['STATION'].unique())

print("\nRow count by station:")
print(df.groupby('STATION').size())


from sklearn.linear_model import LinearRegression
import numpy as np

def compute_trend(df, value_col):
    df = df[df['year'] <= 2025]  # exclude partial 2026
    
    yearly = df.groupby('year')[value_col].mean().reset_index()
    
    X = yearly[['year']]
    y = yearly[value_col]

    model = LinearRegression()
    model.fit(X, y)

    slope_per_year = model.coef_[0]
    slope_per_decade = slope_per_year * 10

    return slope_per_decade

print("\nWarming Trends (Mean Temp per Decade):")

for station in df['STATION'].unique():
    station_df = df[df['STATION'] == station]
    slope = compute_trend(station_df, 'tmean_f')
    print(f"{station}: {round(slope,3)} °F per decade")
    
for station in df['STATION'].unique():
    station_df = df[df['STATION'] == station]
    yearly = station_df.groupby('year')['tmean_f'].mean().reset_index()
    print(f"\n{station} yearly sample:")
    print(yearly.head())
    print(yearly.tail())
    
from src.analytics import winter_summary

winter = winter_summary(df)

print("\nWinter Minimum Trends (°F per decade):")

for station in winter['STATION'].unique():
    station_df = winter[(winter['STATION'] == station) & (winter['year'] <= 2025)]
    slope = compute_trend(station_df, 'winter_avg_tmin')
    print(f"{station}: {round(slope,3)} °F per decade")
    
print("\n80°F Days in February & March (Trend per decade):")

feb_mar = df[df['month'].isin([2,3])]

for station in feb_mar['STATION'].unique():
    station_df = feb_mar[(feb_mar['STATION'] == station) & (feb_mar['year'] <= 2025)]
    
    yearly_extreme = (
        station_df.groupby('year')['is_80f_day']
        .sum()
        .reset_index()
    )

    X = yearly_extreme[['year']]
    y = yearly_extreme['is_80f_day']

    model = LinearRegression()
    model.fit(X, y)

    slope_per_decade = model.coef_[0] * 10
    
    print(f"{station}: {round(slope_per_decade,3)} additional 80°F days per decade")
    
import matplotlib.pyplot as plt

houston = df[(df['STATION'] == "USW00012960") & (df['year'] <= 2025)]
yearly = houston.groupby('year')['tmean_f'].mean().reset_index()

plt.figure()
plt.plot(yearly['year'], yearly['tmean_f'])
plt.title("Houston Annual Mean Temperature (1970–2025)")
plt.xlabel("Year")
plt.ylabel("Mean Temp (°F)")
plt.show()