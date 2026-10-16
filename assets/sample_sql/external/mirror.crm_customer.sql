CREATE EXTERNAL TABLE [mirror].[crm_customer]
(
    [CustomerCode] NVARCHAR(20) NOT NULL,
    [CustomerName] NVARCHAR(200),
    [Segment] VARCHAR(30),
    [_commit_timestamp] DATETIME2(7)
)
WITH (
    LOCATION = 'crm/customer/',
    DATA_SOURCE = [ds_databricks_gold],
    FILE_FORMAT = [ff_delta]
);
GO
