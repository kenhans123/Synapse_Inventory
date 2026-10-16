SET ANSI_NULLS ON
GO
SET QUOTED_IDENTIFIER ON
GO
-- Loads FactSales from staging using CTAS + RENAME swap
CREATE PROC [dm].[usp_Load_FactSales] @LoadDate [DATE], @BatchId [INT] = NULL, @RowsLoaded INT OUTPUT AS
BEGIN
    SET NOCOUNT ON;
    BEGIN TRY
        IF OBJECT_ID('tempdb..#stg') IS NOT NULL DROP TABLE #stg;

        CREATE TABLE #stg WITH (DISTRIBUTION = ROUND_ROBIN, HEAP)
        AS SELECT s.*, c.CustomerKey
           FROM stg.Sales s
           JOIN dm.DimCustomer c ON c.CustomerCode = s.CustomerCode
           WHERE s.SalesDate = @LoadDate;

        CREATE TABLE dm.FactSales_new
        WITH (DISTRIBUTION = HASH(CustomerKey), CLUSTERED COLUMNSTORE INDEX)
        AS
        SELECT f.* FROM dm.FactSales f WHERE f.SalesDate <> @LoadDate
        UNION ALL
        SELECT st.SalesDate, st.CustomerKey, p.ProductKey, st.Qty, st.Amount,
               dm.fn_GetFiscalPeriod(st.SalesDate) AS FiscalPeriod,
               SUM(st.Amount) OVER (PARTITION BY st.CustomerKey ORDER BY st.SalesDate) AS RunningAmt
        FROM #stg st
        LEFT JOIN dm.DimProduct p ON p.ProductCode = st.ProductCode
        OPTION (LABEL = 'Load FactSales');

        SET @RowsLoaded = @@ROWCOUNT;

        RENAME OBJECT dm.FactSales TO FactSales_old;
        RENAME OBJECT dm.FactSales_new TO FactSales;
        DROP TABLE dm.FactSales_old;

        EXEC dm.usp_Log_Audit @ProcName = 'usp_Load_FactSales', @Rows = @RowsLoaded;
    END TRY
    BEGIN CATCH
        THROW;
    END CATCH
END
GO
