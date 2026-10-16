CREATE MATERIALIZED VIEW dm.mv_DailySales
WITH (DISTRIBUTION = HASH(ProductKey))
AS
SELECT SalesDate, ProductKey, COUNT_BIG(*) AS Cnt, SUM(Amount) AS Amount, SUM(Qty) AS Qty
FROM dm.FactSales
GROUP BY SalesDate, ProductKey;
GO
