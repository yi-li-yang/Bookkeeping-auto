"""Parse CSV files into raw rows (list of dicts)."""
import pandas as pd


def parse_csv(file_path: str) -> list[dict]:
    """Read a CSV and return all rows as a list of dicts."""
    df = pd.read_csv(file_path, dtype=str)
    df.columns = [c.strip() for c in df.columns]
    rows = df.where(df.notna(), None).to_dict(orient="records")
    return rows
