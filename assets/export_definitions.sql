/* =====================================================================================
   synapse-datamart-inventory : export object definitions WITHOUT installing anything
   Works on: Azure Synapse dedicated SQL pool, serverless SQL pool, Azure SQL Database.
   Uses only sys catalog views + SUBSTRING / DATALENGTH / CONVERT (no OBJECT_DEFINITION,
   STRING_AGG, TYPE_NAME or APPLY, which dedicated pools reject).

   1. VS Code: right-click your connection > New Query, paste this whole script, run (Ctrl+Shift+E).
   2. In the results grid click "Save as JSON" (icons on the right edge of the grid).
   3. On a machine with Python + openpyxl (or in Claude):
        python scripts/extract.py export --path <saved file> --datamart <name> --out output/<name>/inventory.json
   Read-only: catalog metadata only, no table data is read.
   Long definitions are split into 4,000-character chunks (chunk_no) so the grid cannot truncate them.
   External tables return one row per column (chunk_no = column_id).
   Needs VIEW DEFINITION; without it chunk_text comes back NULL.
   ===================================================================================== */
WITH d AS (
    SELECT 0 AS n UNION ALL SELECT 1 UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4
    UNION ALL SELECT 5 UNION ALL SELECT 6 UNION ALL SELECT 7 UNION ALL SELECT 8 UNION ALL SELECT 9
),
tally AS (   -- 0..999 chunks x 4,000 chars = up to 4 million characters per object
    SELECT a.n + b.n * 10 + c.n * 100 AS n FROM d a CROSS JOIN d b CROSS JOIN d c
),
modules AS (
    SELECT s.name AS object_schema, o.name AS object_name, RTRIM(o.type) AS type_code,
           CONVERT(VARCHAR(19), o.create_date, 120) AS created,
           CONVERT(VARCHAR(19), o.modify_date, 120) AS modified,
           m.definition
    FROM sys.objects o
    JOIN sys.schemas s ON s.schema_id = o.schema_id
    LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
    WHERE o.is_ms_shipped = 0
      AND RTRIM(o.type) IN ('P','PC','FN','IF','TF','FS','FT','V')
      -- AND s.name IN ('dm','rpt')          -- optional schema filter
)
SELECT m.object_schema, m.object_name, m.type_code,
       0 AS is_materialized, m.created, m.modified,
       t.n AS chunk_no,
       SUBSTRING(m.definition, t.n * 4000 + 1, 4000) AS chunk_text,
       CAST(NULL AS NVARCHAR(4000)) AS location,
       CAST(NULL AS NVARCHAR(256))  AS data_source,
       CAST(NULL AS NVARCHAR(4000)) AS data_source_location,
       CAST(NULL AS NVARCHAR(256))  AS file_format,
       CAST(NULL AS NVARCHAR(60))   AS format_type
FROM modules m
CROSS JOIN tally t
WHERE t.n = 0 OR t.n * 4000 < ISNULL(DATALENGTH(m.definition), 0) / 2

UNION ALL

SELECT s.name, tb.name, 'ET',
       0, CONVERT(VARCHAR(19), tb.create_date, 120), CONVERT(VARCHAR(19), tb.modify_date, 120),
       c.column_id,
       '[' + c.name + '] ' + ty.name +
       CASE WHEN ty.name IN ('varchar','char','varbinary','binary')
                 THEN '(' + CASE WHEN c.max_length = -1 THEN 'max' ELSE CAST(c.max_length AS VARCHAR(10)) END + ')'
            WHEN ty.name IN ('nvarchar','nchar')
                 THEN '(' + CASE WHEN c.max_length = -1 THEN 'max' ELSE CAST(c.max_length / 2 AS VARCHAR(10)) END + ')'
            WHEN ty.name IN ('decimal','numeric')
                 THEN '(' + CAST(c.precision AS VARCHAR(5)) + ',' + CAST(c.scale AS VARCHAR(5)) + ')'
            WHEN ty.name IN ('datetime2','time','datetimeoffset')
                 THEN '(' + CAST(c.scale AS VARCHAR(5)) + ')'
            ELSE '' END +
       CASE WHEN c.is_nullable = 0 THEN ' NOT NULL' ELSE '' END,
       et.location, ds.name, ds.location, ff.name, ff.format_type
FROM sys.external_tables et
JOIN sys.tables  tb ON tb.object_id = et.object_id
JOIN sys.schemas s  ON s.schema_id  = tb.schema_id
JOIN sys.columns c  ON c.object_id  = et.object_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.external_data_sources ds ON ds.data_source_id = et.data_source_id
LEFT JOIN sys.external_file_formats ff ON ff.file_format_id = et.file_format_id

ORDER BY object_schema, object_name, chunk_no;
