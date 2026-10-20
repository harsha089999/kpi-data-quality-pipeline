# KPI Data Quality Pipeline

[![Run project](https://github.com/harsha089999/kpi-data-quality-pipeline/actions/workflows/run.yml/badge.svg)](https://github.com/harsha089999/kpi-data-quality-pipeline/actions/workflows/run.yml)

**A layered DuckDB pipeline that runs 11 data quality checks on real e-commerce data, quarantines bad orders and only then publishes daily KPIs.**

> **About the data:** this project runs on a real public dataset, **Olist Brazilian E-Commerce Public Dataset (Kaggle: olistbr/brazilian-ecommerce)**, included in `data/` so it runs straight after cloning. Sources and licence are listed below and in [`data/README.md`](data/README.md).

## The business question

Can the business trust today's KPI numbers, and if not, exactly which records are the problem?

## What this project does

Dashboards are only as good as the data under them. This project takes the raw Olist tables through staging, runs critical and warning checks in SQL, moves orders that fail a critical check into a quarantine table instead of quietly dropping them, and publishes daily and monthly KPIs from what's left. Every problem it reports is in the real data. Nothing is injected.

## The data

The raw Olist marketplace tables, 2016 to 2018. KPIs are published for January 2017 to 21 August 2018, the period with steady trading; outside that the extract is sparse.

**Source:**
- [Kaggle: Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)

**Licence:** CC BY-NC-SA 4.0 (non-commercial, share-alike). Redistributed here unchanged except for gzip compression.

## How it works

1. Staging in DuckDB: read the gzipped CSVs, cast types and clean status text, then build one row per order with item and payment totals.
2. Run 11 checks written in SQL. Critical: unique keys, items linked to real orders, delivered orders have items and a delivery date, payments match items plus freight within R$1. Warning: timestamps in order, approval time present, products have a category, no missing days, and no daily volume spikes or drops.
3. Save the check report, and move every order that fails a critical check into `quarantined_orders.csv` with the reason.
4. Build the daily KPI mart (delivered orders, revenue, average order value, on-time %, average days to deliver) from clean orders only, plus a monthly roll-up.
5. Exit with code 1 under `--strict` if any critical check fails, so a scheduled job stops instead of publishing.

## Run it

```bash
git clone https://github.com/harsha089999/kpi-data-quality-pipeline
cd kpi-data-quality-pipeline
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python src/main.py
```

Outputs are written to `outputs/`. Python 3.10 or newer is recommended.

## Results from the included run

This is the actual console output when the script is run as shipped:

```text
Loaded: stg_orders 99,441, stg_items 112,650, stg_payments 103,886, stg_products 32,951 

                     check severity  failing_rows status                          example
           unique_order_id critical             0   pass                                 
         unique_order_line critical             0   pass                                 
          items_have_order critical             0   pass                                 
       delivered_has_items critical             0   pass                                 
        delivered_has_date critical             8   fail 2d1e2d5bf4dc7227b3bfebb81328c15f
     payment_matches_items critical           247   fail 84d6d9710c8af32b5e88f2d1c14ab871
       timestamps_in_order     warn          1382   fail dcb36b511fcac050b97cd5c05de84dc3
approved_has_approval_time     warn            14   fail e04abd8149ef81b95221e88f6ed9ab6a
      product_has_category     warn           610   fail a41e356c76fab66334f36de622ecbd3a
           no_missing_days     warn             4   fail              2017-01-01 00:00:00
         row_count_anomaly     warn            24   fail              2017-01-06 00:00:00

Quarantined 255 orders that failed a critical check; published KPIs for 594 days.

Monthly roll-up of the published KPIs:
  month  orders  revenue_brl  on_time_pct
2017-01     745     127023.0         97.0
2017-02    1642     270613.0         97.0
2017-03    2541     413921.0         95.4
2017-04    2292     389316.0         93.5
2017-05    3525     564625.0         97.0
2017-06    3120     488554.0         97.0
2017-07    3863     565375.0         97.2
2017-08    4186     644816.0         97.1
2017-09    4144     700400.0         95.6
2017-10    4473     750857.0         95.8
2017-11    7277    1152186.0         87.6
2017-12    5496     841888.0         92.5
2018-01    7028    1071664.0         94.3
2018-02    6530     963233.0         85.8
2018-03    6997    1119882.0         81.0
2018-04    6790    1132301.0         95.5
2018-05    6743    1128175.0         93.4
2018-06    6089    1010550.0         98.8
2018-07    6152    1026530.0         96.6
2018-08    5655     895637.0         93.2

Critical checks failed: ['delivered_has_date', 'payment_matches_items']. Affected orders were quarantined, not silently dropped. Review outputs/quarantined_orders.csv before sign-off.
```

The keys are clean, but two critical checks fail on real records. 8 orders are marked delivered with no delivery date, and 247 delivered orders have payments that differ from items plus freight by more than R$1. Those 255 orders are quarantined. The warnings are worth reading too: 1,359 orders were handed to the carrier before they were approved and 23 were delivered before they were shipped, which points at timestamps being back-filled or set in different systems. 610 products have no category. The published KPIs show a real operational story: on-time delivery is around 95 to 97% most months, but dropped to 88% in November 2017 (Black Friday volume) and to 86% and 81% in February and March 2018.

## Output files

| File | What it contains |
|---|---|
| `outputs/dq_report.csv` | Every check: severity, rule, failing rows, pass or fail and an example key. |
| `outputs/quarantined_orders.csv` | Orders that failed a critical check, with the reason. |
| `outputs/kpi_daily.csv` | Daily KPIs from clean delivered orders. |
| `outputs/kpi_monthly.csv` | Monthly roll-up of orders, revenue and on-time %. |

## Limitations and honest caveats

- The R$1 payment tolerance is a judgement call. Some mismatches may be legitimate (vouchers, instalment interest).
- Checks run on a full extract each time. A production version would check each new load incrementally.
- 'On time' uses the estimated delivery date plus one day of grace, which is a business choice.

## Turning it into a dashboard

A data quality panel (green, amber or red per check, with failing row counts) sitting above the KPI page, so anyone reading the numbers can see whether to trust them that day.

## Ideas to extend it

- Rewrite the checks as dbt tests and models.
- Store each run's results to track data quality over time.
- Send an alert (email or Teams) when a critical check fails.

## Skills demonstrated

SQL, data modelling in layers, data quality testing, defensive pipeline design, KPI definitions.

**Tools:** Python, DuckDB, pandas

## Project structure

```text
kpi-data-quality-pipeline/
├── src/main.py                 # the whole pipeline
├── data/                       # real source data (gzipped) + data/README.md with source and licence
├── outputs/                    # files from the run shown above
├── requirements.txt
├── .github/workflows/run.yml   # CI: installs and runs the project on every push
└── LICENSE
```

## Licence

Code: MIT, see [LICENSE](LICENSE). The data in `data/` keeps its original licence (see above).
