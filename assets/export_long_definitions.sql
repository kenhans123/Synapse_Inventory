/* =====================================================================================
   synapse-datamart-inventory : fetch LONG definitions in 4,000-character pieces
   Run this ONLY if the build said "definition(s) cut short by the results grid".

   1. Paste the object names the build listed into @names (comma separated, schema.name),
      or leave it empty to fetch every object longer than @min_length characters.
   2. Run, then Save as JSON (e.g. bidw_long_definitions.json).
   3. Build with both files:
        python scripts/inventory.py bidw_definitions.json bidw_long_definitions.json --datamart bidw
   ===================================================================================== */
DECLARE @names      NVARCHAR(4000) = N'';   -- e.g. N'dm.usp_Load_FactSales, dm.usp_Big_Merge'
DECLARE @min_length INT            = 30000; -- used only when @names is empty

IF OBJECT_ID('tempdb..#n') IS NOT NULL DROP TABLE #n;
CREATE TABLE #n (n INT);
INSERT INTO #n (n)                           -- 0..299 -> up to 1,200,000 characters per object
SELECT a.n + b.n * 10 + c.n * 100
FROM      (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
           UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) a
CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
           UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9) b
CROSS JOIN (SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2) c;

SELECT s.name                                   AS object_schema,
       o.name                                   AS object_name,
       RTRIM(o.type)                            AS type_code,
       CONVERT(VARCHAR(19), o.create_date, 120) AS created,
       CONVERT(VARCHAR(19), o.modify_date, 120) AS modified,
       n.n                                      AS chunk_no,
       DATALENGTH(m.definition) / 2             AS def_length,
       4000                                     AS chunk_size,
       SUBSTRING(m.definition, n.n * 4000 + 1, 4000) AS chunk_text
FROM sys.objects o
JOIN sys.schemas s     ON s.schema_id = o.schema_id
JOIN sys.sql_modules m ON m.object_id = o.object_id
JOIN #n n              ON n.n * 4000 < DATALENGTH(m.definition) / 2
WHERE o.is_ms_shipped = 0
  AND (   (LEN(@names) > 0
           AND CHARINDEX(',' + s.name + '.' + o.name + ',', ',' + REPLACE(@names, ' ', '') + ',') > 0)
       OR (LEN(@names) = 0 AND DATALENGTH(m.definition) / 2 > @min_length));
