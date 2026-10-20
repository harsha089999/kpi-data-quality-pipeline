# Data

Real data, not simulated. Files are the original CSVs, gzip-compressed to keep the repo small. pandas and DuckDB read
`.csv.gz` directly, so nothing needs unzipping.

**Dataset:** Olist Brazilian E-Commerce Public Dataset (Kaggle: olistbr/brazilian-ecommerce)

- [Kaggle: Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)

**Licence:** CC BY-NC-SA 4.0 (non-commercial, share-alike). Redistributed here unchanged except for gzip compression.

| File | Contents |
|---|---|
| `olist_orders_dataset.csv.gz` | 99,441 orders with status and five timestamps |
| `olist_order_items_dataset.csv.gz` | 112,650 order lines with price and freight |
| `olist_order_payments_dataset.csv.gz` | 103,886 payment records (one order can have several) |
| `olist_products_dataset.csv.gz` | 32,951 products |

To refresh from Kaggle instead, download the dataset with the Kaggle CLI (`kaggle datasets download <owner/dataset>`),
then gzip the needed files into this folder with the same names.
