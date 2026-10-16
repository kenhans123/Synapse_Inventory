CREATE FUNCTION dm.mtvf_CustomerTiers (@Year INT)
RETURNS @result TABLE (CustomerKey INT, Tier VARCHAR(10))
AS
BEGIN
    INSERT INTO @result
    SELECT CustomerKey,
           CASE WHEN SUM(Amount) > 100000 THEN 'Gold' WHEN SUM(Amount) > 10000 THEN 'Silver' ELSE 'Bronze' END
    FROM dm.FactSales
    WHERE YEAR(SalesDate) = @Year
    GROUP BY CustomerKey;
    RETURN;
END
GO
