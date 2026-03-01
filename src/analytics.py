def monthly_summary(df):
    return (
        df.groupby(['year','month'])
          .agg(
              avg_tmax=('tmax_f','mean'),
              avg_tmin=('tmin_f','mean'),
              avg_tmean=('tmean_f','mean'),
              count_80f=('is_80f_day','sum')
          )
          .reset_index()
    )

def winter_summary(df):
    winter = df[df['month'].isin([12, 1, 2])]

    return (
        winter.groupby(['STATION','winter_year'])
              .agg(
                  winter_avg_tmin=('tmin_f','mean'),
                  winter_avg_tmax=('tmax_f','mean'),
                  winter_80f_days=('is_80f_day','sum')
              )
              .reset_index()
              .rename(columns={'winter_year': 'year'})
    )
    
from sklearn.linear_model import LinearRegression
import numpy as np

def compute_trend(df, value_column):
    yearly = df.groupby('year')[value_column].mean().reset_index()
    
    X = yearly[['year']]
    y = yearly[value_column]
    
    model = LinearRegression()
    model.fit(X, y)
    
    slope = model.coef_[0]
    
    return slope, yearly