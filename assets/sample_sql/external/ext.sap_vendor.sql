CREATE EXTERNAL TABLE ext.sap_vendor
(
    VendorId VARCHAR(10),
    VendorName NVARCHAR(100)
)
WITH (LOCATION = 'sap/vendor/*.parquet', DATA_SOURCE = ds_landing, FILE_FORMAT = ff_parquet);
GO
