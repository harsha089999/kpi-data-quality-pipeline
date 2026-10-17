"""Layered DuckDB pipeline with data quality gates before publishing KPIs."""
from pathlib import Path
import sys

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)


def make_raw(seed=2):
    rng = np.random.default_rng(seed)
    days = pd.date_range("2026-09-01", "2026-10-15")
    frames = []
    for d in days:
        if d == pd.Timestamp("2026-09-20"):
            continue  # missing day
        n = 400 if d != pd.Timestamp("2026-10-08") else 180  # partial extract
        f = pd.DataFrame({"order_id": [f"{d:%Y%m%d}-{i:04d}" for i in range(n)],
                          "order_date": d.strftime("%Y-%m-%d"),
                          "store_id": rng.choice(["RIC", "ROL", "HOR", "PAP"], n),
                          "channel": rng.choice(["store", "online"], n, p=[0.7, 0.3]),
                          "amount_nzd": rng.gamma(4, 18, n).round(2)})
        if d == pd.Timestamp("2026-10-02"):
            f.loc[:5, "store_id"] = None  # null keys
            f.loc[6:8, "channel"] = "Online "  # dirty value
        frames.append(f)
    raw = pd.concat(frames)
    return pd.concat([raw, raw[raw.order_date == "2026-10-12"]])  # duplicated load


CHECKS = [
    ("unique_order_id", "critical", "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM stg_orders"),
    ("not_null_store_id", "critical", "SELECT COUNT(*) FROM stg_orders WHERE store_id IS NULL"),
    ("accepted_channel", "critical", "SELECT COUNT(*) FROM stg_orders WHERE channel NOT IN ('store','online')"),
    ("no_missing_days", "warn", """
        SELECT COUNT(*) FROM (SELECT UNNEST(generate_series(MIN(order_date), MAX(order_date), INTERVAL 1 DAY)) d
        FROM stg_orders) cal WHERE CAST(d AS DATE) NOT IN (SELECT DISTINCT order_date FROM stg_orders)"""),
    ("row_count_anomaly", "warn", """
        WITH daily AS (SELECT order_date, COUNT(*) n FROM stg_orders GROUP BY 1),
        w AS (SELECT order_date, n, AVG(n) OVER (ORDER BY order_date ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING) avg7 FROM daily)
        SELECT COUNT(*) FROM w WHERE avg7 IS NOT NULL AND n < 0.6 * avg7"""),
]


def main():
    con = duckdb.connect()
    raw = make_raw()
    con.register("raw_df", raw)
    con.execute("CREATE TABLE raw_orders AS SELECT * FROM raw_df")
    # staging: dedupe exact duplicate loads, clean text, cast types
    con.execute("""
        CREATE TABLE stg_orders AS
        SELECT DISTINCT order_id, CAST(order_date AS DATE) order_date, store_id,
               LOWER(TRIM(channel)) channel, CAST(amount_nzd AS DECIMAL(10,2)) amount_nzd
        FROM raw_orders""")
    results = []
    for name, severity, sql in CHECKS:
        failures = con.execute(sql).fetchone()[0]
        results.append({"check": name, "severity": severity, "failing_rows": failures,
                        "status": "pass" if failures == 0 else "fail"})
    report = pd.DataFrame(results)
    report.to_csv(OUT / "dq_report.csv", index=False)
    print(report.to_string(index=False))

    # marts: quarantine bad rows rather than silently dropping the day
    con.execute("""
        CREATE TABLE kpi_daily AS
        SELECT order_date, COUNT(*) orders, SUM(amount_nzd) revenue_nzd, ROUND(AVG(amount_nzd), 2) aov_nzd
        FROM stg_orders WHERE store_id IS NOT NULL AND channel IN ('store','online')
        GROUP BY 1 ORDER BY 1""")
    con.execute(f"COPY kpi_daily TO '{OUT / 'kpi_daily.csv'}' (HEADER)")

    critical_fail = report.query("severity == 'critical' and status == 'fail'")
    if len(critical_fail):
        print(f"\nCRITICAL checks failed: {list(critical_fail.check)}. Do not publish.")
        # the demo data is broken on purpose; pass --strict (as a real scheduled job would) to fail the run
        sys.exit(1 if "--strict" in sys.argv else 0)
    print("\nAll critical checks passed. KPI table published (warnings above need a look).")


if __name__ == "__main__":
    main()
