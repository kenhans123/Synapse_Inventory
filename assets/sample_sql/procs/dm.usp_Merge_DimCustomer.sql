CREATE PROCEDURE [dm].[usp_Merge_DimCustomer]
AS
BEGIN
    TRUNCATE TABLE stg.Customer_Delta;
    INSERT INTO stg.Customer_Delta
    SELECT * FROM mirror.crm_customer   -- Databricks-written Delta table
    WHERE _commit_timestamp > (SELECT MAX(LoadedAt) FROM audit.Watermark WHERE TableName = 'DimCustomer');

    MERGE INTO dm.DimCustomer AS tgt
    USING stg.Customer_Delta AS src ON tgt.CustomerCode = src.CustomerCode
    WHEN MATCHED THEN UPDATE SET tgt.CustomerName = src.CustomerName, tgt.Segment = src.Segment
    WHEN NOT MATCHED THEN INSERT (CustomerCode, CustomerName, Segment) VALUES (src.CustomerCode, src.CustomerName, src.Segment);

    UPDATE w SET LoadedAt = GETDATE() FROM audit.Watermark w WHERE w.TableName = 'DimCustomer';
    EXEC dm.usp_Log_Audit 'usp_Merge_DimCustomer', 0;
END
GO
