import pandas as pd

def load_raw(filepath):
    df = pd.read_csv(filepath)
    return df

def save_silver(df, output_path):
    df.to_parquet(output_path, index=False)