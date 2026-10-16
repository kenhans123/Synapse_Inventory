CREATE VIEW mirror.vw_dbx_orders AS
SELECT *
FROM OPENROWSET(
    BULK 'https://adlsprod.dfs.core.windows.net/gold/orders/',
    FORMAT = 'DELTA'
) AS rows;
GO
