import pandas as pd
import numpy as np

def clean_daily(filepath):
    df = pd.read_csv(filepath)

    # Parse date
    df['DATE'] = pd.to_datetime(df['DATE'])

    # Compute mean temp
    df['tmean_f'] = (df['TMAX'] + df['TMIN']) / 2

    # Extreme heat flag
    df['is_80f_day'] = (df['TMAX'] >= 80).astype(int)

    # Year / Month
    df['year'] = df['DATE'].dt.year
    df['month'] = df['DATE'].dt.month

    # Season logic
    df['season'] = np.where(df['month'].isin([12,1,2]), 'Winter',
                    np.where(df['month'].isin([3,4,5]), 'Spring',
                    np.where(df['month'].isin([6,7,8]), 'Summer',
                             'Fall')))

    # Rename columns
    df = df.rename(columns={
        'TMAX': 'tmax_f',
        'TMIN': 'tmin_f'
    })
    
    # Winter year logic (Dec belongs to next year's winter)
    df['winter_year'] = df['year']
    df.loc[df['month'] == 12, 'winter_year'] += 1

    return df


def monthly_summary(df):
    return (
        df.groupby(['STATION','year','month'])
          .agg(
              avg_tmax=('tmax_f','mean'),
              avg_tmin=('tmin_f','mean'),
              avg_tmean=('tmean_f','mean'),
              count_80f=('is_80f_day','sum')
          )
          .reset_index()
    )
    


