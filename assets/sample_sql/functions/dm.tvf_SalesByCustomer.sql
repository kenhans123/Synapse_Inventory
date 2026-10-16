CREATE FUNCTION [dm].[tvf_SalesByCustomer] (@CustomerKey INT, @FromDate DATE, @ToDate DATE)
RETURNS TABLE
AS
RETURN
(
    SELECT f.SalesDate, p.ProductName, f.Qty, f.Amount
    FROM dm.FactSales f
    JOIN dm.DimProduct p ON p.ProductKey = f.ProductKey
    WHERE f.CustomerKey = @CustomerKey AND f.SalesDate BETWEEN @FromDate AND @ToDate
);
GO
