-- This migration precedes the first order/quotation writes in the fresh D1 DB.
-- Native repositories maintain full Unicode casefold keys in the same batch.
ALTER TABLE sales_sales_orders ADD COLUMN _number_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE sales_sales_orders ADD COLUMN _party_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE purchasing_purchase_orders ADD COLUMN _number_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE purchasing_purchase_orders ADD COLUMN _party_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE sales_quotations ADD COLUMN _number_fold TEXT NOT NULL DEFAULT '';
ALTER TABLE sales_quotations ADD COLUMN _party_fold TEXT NOT NULL DEFAULT '';
