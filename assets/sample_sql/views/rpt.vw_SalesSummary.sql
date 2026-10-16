CREATE VIEW rpt.vw_SalesSummary AS
WITH monthly AS (
    SELECT FiscalPeriod, CustomerKey, SUM(Amount) AS Amount FROM dm.FactSales GROUP BY FiscalPeriod, CustomerKey
)
SELECT m.FiscalPeriod, c.Segment, SUM(m.Amount) AS Amount
FROM monthly m
JOIN dm.DimCustomer c ON c.CustomerKey = m.CustomerKey
GROUP BY m.FiscalPeriod, c.Segment;
GO
