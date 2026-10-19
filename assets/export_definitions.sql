/* =====================================================================================
   synapse-datamart-inventory : FAST export of object definitions (no installs needed)
   Works on: Azure Synapse dedicated SQL pool, serverless SQL pool, Azure SQL Database.

   One row per object with the full definition, plus one row per external-table column.
   No joins to number tables, no ORDER BY - only plain catalog-view reads, so it should return in seconds.

   1. VS Code: right-click your connection > New Query, paste this script, run (Ctrl+Shift+E).
   2. Results grid > Save as JSON (icons on the right edge of the grid).
   3. Build: python scripts/inventory.py <saved file> --client <name>
      The build compares each definition with def_length and lists any object the results grid cut short.
      Only if it reports some: run export_long_definitions.sql (it fetches just those objects in pieces),
      save it next to the first file and pass both files to inventory.py.
   Read-only: catalog metadata only, no table data. Needs VIEW DEFINITION (else definition is NULL).
   ===================================================================================== */
SELECT s.name                                   AS object_schema,
       o.name                                   AS object_name,
       RTRIM(o.type)                            AS type_code,
       CONVERT(VARCHAR(19), o.create_date, 120) AS created,
       CONVERT(VARCHAR(19), o.modify_date, 120) AS modified,
       0                                        AS chunk_no,
       DATALENGTH(m.definition) / 2             AS def_length,
       m.definition                             AS chunk_text,
       CAST(NULL AS NVARCHAR(4000))             AS location,
       CAST(NULL AS NVARCHAR(256))              AS data_source,
       CAST(NULL AS NVARCHAR(4000))             AS data_source_location,
       CAST(NULL AS NVARCHAR(256))              AS file_format,
       CAST(NULL AS NVARCHAR(60))               AS format_type
FROM sys.objects o
JOIN sys.schemas s          ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.is_ms_shipped = 0
  AND o.type IN ('P','PC','FN','IF','TF','FS','FT','V')
  -- AND s.name IN ('dm','rpt')            -- optional: limit to some schemas

UNION ALL

SELECT s.name, tb.name, 'ET',
       CONVERT(VARCHAR(19), tb.create_date, 120), CONVERT(VARCHAR(19), tb.modify_date, 120),
       c.column_id,
       NULL,
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
JOIN sys.tables  tb ON tb.object_id    = et.object_id
JOIN sys.schemas s  ON s.schema_id     = tb.schema_id
JOIN sys.columns c  ON c.object_id     = et.object_id
JOIN sys.types   ty ON ty.user_type_id = c.user_type_id
LEFT JOIN sys.external_data_sources ds ON ds.data_source_id = et.data_source_id
LEFT JOIN sys.external_file_formats ff ON ff.file_format_id = et.file_format_id;
