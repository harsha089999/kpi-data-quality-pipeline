"""Layered DuckDB pipeline with data quality gates on real e-commerce data, before publishing daily KPIs.

Data: Olist Brazilian E-Commerce Public Dataset (Kaggle: olistbr/brazilian-ecommerce), raw orders, order items,
payments and products. Stored in data/. Nothing is injected: every failure the checks report is in the real data.

Layers: raw (as loaded) -> staging (typed, cleaned) -> checks -> marts (only rows that pass, plus a quarantine table).
"""
from pathlib import Path
import sys

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "outputs"
OUT.mkdir(exist_ok=True)
REPORT_FROM, REPORT_TO = "2017-01-01", "2018-08-21"  # the period with steady trading; outside it the extract is sparse

STAGING = f"""
CREATE TABLE stg_orders AS
SELECT order_id, customer_id, LOWER(TRIM(order_status)) AS order_status,
       CAST(order_purchase_timestamp AS TIMESTAMP)      AS purchased_at,
       CAST(order_approved_at AS TIMESTAMP)             AS approved_at,
       CAST(order_delivered_carrier_date AS TIMESTAMP)  AS shipped_at,
       CAST(order_delivered_customer_date AS TIMESTAMP) AS delivered_at,
       CAST(order_estimated_delivery_date AS TIMESTAMP) AS promised_at
FROM read_csv_auto('{DATA}/olist_orders_dataset.csv.gz');

CREATE TABLE stg_items AS
SELECT order_id, order_item_id, product_id, seller_id,
       CAST(price AS DECIMAL(12,2)) AS price, CAST(freight_value AS DECIMAL(12,2)) AS freight
FROM read_csv_auto('{DATA}/olist_order_items_dataset.csv.gz');

CREATE TABLE stg_payments AS
SELECT order_id, payment_sequential, payment_type, payment_installments,
       CAST(payment_value AS DECIMAL(12,2)) AS payment_value
FROM read_csv_auto('{DATA}/olist_order_payments_dataset.csv.gz');

CREATE TABLE stg_products AS
SELECT product_id, product_category_name AS category, product_weight_g
FROM read_csv_auto('{DATA}/olist_products_dataset.csv.gz');

-- one row per order with item and payment totals, used by checks and marts
CREATE TABLE int_orders AS
SELECT o.*, i.items, i.item_value, p.paid
FROM stg_orders o
LEFT JOIN (SELECT order_id, COUNT(*) items, SUM(price + freight) item_value FROM stg_items GROUP BY 1) i USING (order_id)
LEFT JOIN (SELECT order_id, SUM(payment_value) paid FROM stg_payments GROUP BY 1) p USING (order_id);
"""

# (name, severity, description, SQL returning the failing rows' order_id or key)
CHECKS = [
    ("unique_order_id", "critical", "order_id is unique in orders",
     "SELECT order_id FROM stg_orders GROUP BY 1 HAVING COUNT(*) > 1"),
    ("unique_order_line", "critical", "(order_id, order_item_id) is unique in items",
     "SELECT order_id FROM stg_items GROUP BY order_id, order_item_id HAVING COUNT(*) > 1"),
    ("items_have_order", "critical", "every item belongs to a known order",
     "SELECT order_id FROM stg_items WHERE order_id NOT IN (SELECT order_id FROM stg_orders)"),
    ("delivered_has_items", "critical", "delivered orders have at least one item",
     "SELECT order_id FROM int_orders WHERE order_status = 'delivered' AND items IS NULL"),
    ("delivered_has_date", "critical", "delivered orders have a delivery date",
     "SELECT order_id FROM int_orders WHERE order_status = 'delivered' AND delivered_at IS NULL"),
    ("payment_matches_items", "critical", "payments equal items + freight within R$1",
     "SELECT order_id FROM int_orders WHERE order_status = 'delivered' AND ABS(COALESCE(paid,0) - COALESCE(item_value,0)) > 1"),
    ("timestamps_in_order", "warn", "purchase <= approval <= carrier <= delivery",
     """SELECT order_id FROM int_orders WHERE approved_at < purchased_at OR shipped_at < approved_at
        OR delivered_at < shipped_at"""),
    ("approved_has_approval_time", "warn", "non-cancelled orders past 'created' have an approval time",
     "SELECT order_id FROM int_orders WHERE approved_at IS NULL AND order_status NOT IN ('canceled','created')"),
    ("product_has_category", "warn", "products have a category",
     "SELECT product_id FROM stg_products WHERE category IS NULL"),
    ("no_missing_days", "warn", "every day in the reporting window has orders",
     f"""SELECT d FROM (SELECT UNNEST(generate_series(DATE '{REPORT_FROM}', DATE '{REPORT_TO}', INTERVAL 1 DAY)) d) cal
         WHERE CAST(d AS DATE) NOT IN (SELECT DISTINCT CAST(purchased_at AS DATE) FROM stg_orders)"""),
    ("row_count_anomaly", "warn", "daily orders not below 50% or above 250% of the trailing 4-week average",
     f"""WITH daily AS (SELECT CAST(purchased_at AS DATE) d, COUNT(*) n FROM stg_orders
                       WHERE purchased_at BETWEEN '{REPORT_FROM}' AND '{REPORT_TO} 23:59:59' GROUP BY 1),
         w AS (SELECT d, n, AVG(n) OVER (ORDER BY d ROWS BETWEEN 28 PRECEDING AND 1 PRECEDING) avg28 FROM daily)
         SELECT d FROM w WHERE avg28 IS NOT NULL AND (n < 0.5 * avg28 OR n > 2.5 * avg28)"""),
]


def main():
    con = duckdb.connect()
    con.execute(STAGING)
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ["stg_orders", "stg_items", "stg_payments", "stg_products"]}
    print("Loaded:", ", ".join(f"{k} {v:,}" for k, v in counts.items()), "\n")

    results, quarantine = [], []
    for name, severity, desc, sql in CHECKS:
        failing = con.execute(sql).df()
        results.append({"check": name, "severity": severity, "rule": desc, "failing_rows": len(failing),
                        "status": "pass" if failing.empty else "fail",
                        "example": str(failing.iloc[0, 0])[:32] if len(failing) else ""})
        if severity == "critical" and len(failing) and failing.columns[0] == "order_id":
            quarantine.append(failing.assign(reason=name))
    report = pd.DataFrame(results)
    report.to_csv(OUT / "dq_report.csv", index=False)
    print(report.drop(columns="rule").to_string(index=False))

    q = pd.concat(quarantine) if quarantine else pd.DataFrame(columns=["order_id", "reason"])
    q.to_csv(OUT / "quarantined_orders.csv", index=False)
    con.register("quarantine", q)

    # mart: daily KPIs on delivered orders that passed every critical check
    con.execute(f"""
        CREATE TABLE kpi_daily AS
        SELECT CAST(purchased_at AS DATE) AS order_date,
               COUNT(*) AS delivered_orders,
               ROUND(SUM(item_value), 2) AS revenue_brl,
               ROUND(AVG(item_value), 2) AS aov_brl,
               ROUND(100.0 * AVG(CASE WHEN delivered_at <= promised_at + INTERVAL 1 DAY THEN 1 ELSE 0 END), 1) AS on_time_pct,
               ROUND(AVG(DATE_DIFF('hour', purchased_at, delivered_at)) / 24.0, 1) AS avg_days_to_deliver
        FROM int_orders
        WHERE order_status = 'delivered'
          AND order_id NOT IN (SELECT order_id FROM quarantine)
          AND purchased_at BETWEEN '{REPORT_FROM}' AND '{REPORT_TO} 23:59:59'
        GROUP BY 1 ORDER BY 1""")
    con.execute(f"COPY kpi_daily TO '{OUT / 'kpi_daily.csv'}' (HEADER)")
    monthly = con.execute("""SELECT STRFTIME(order_date, '%Y-%m') AS month, CAST(SUM(delivered_orders) AS INT) AS orders,
                                    ROUND(SUM(revenue_brl)) revenue_brl,
                                    ROUND(SUM(on_time_pct * delivered_orders) / SUM(delivered_orders), 1) on_time_pct
                             FROM kpi_daily GROUP BY 1 ORDER BY 1""").df()
    monthly.to_csv(OUT / "kpi_monthly.csv", index=False)
    print(f"\nQuarantined {q.order_id.nunique():,} orders that failed a critical check; "
          f"published KPIs for {con.execute('SELECT COUNT(*) FROM kpi_daily').fetchone()[0]} days.")
    print("\nMonthly roll-up of the published KPIs:\n" + monthly.to_string(index=False))

    critical_fail = report.query("severity == 'critical' and status == 'fail'")
    if len(critical_fail):
        print(f"\nCritical checks failed: {list(critical_fail.check)}. Affected orders were quarantined, "
              "not silently dropped. Review outputs/quarantined_orders.csv before sign-off.")
        sys.exit(1 if "--strict" in sys.argv else 0)  # a scheduled job would run with --strict and stop here
    print("\nAll critical checks passed.")


if __name__ == "__main__":
    main()
