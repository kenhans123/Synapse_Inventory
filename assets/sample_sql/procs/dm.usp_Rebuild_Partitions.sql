CREATE PROCEDURE dm.usp_Rebuild_Partitions
    @SchemaName NVARCHAR(128) = N'dm'
AS
BEGIN
    DECLARE @tbl NVARCHAR(256), @sql NVARCHAR(MAX);
    DECLARE @tables TABLE (TableName NVARCHAR(256));
    INSERT INTO @tables SELECT name FROM sys.tables WHERE schema_id = SCHEMA_ID(@SchemaName);

    DECLARE tbl_cursor CURSOR FOR SELECT TableName FROM @tables;
    OPEN tbl_cursor;
    FETCH NEXT FROM tbl_cursor INTO @tbl;
    WHILE @@FETCH_STATUS = 0
    BEGIN
        BEGIN TRAN;
        SET @sql = N'ALTER INDEX ALL ON ' + QUOTENAME(@SchemaName) + '.' + QUOTENAME(@tbl) + ' REBUILD';
        EXEC sp_executesql @sql;
        SET @sql = N'UPDATE STATISTICS ' + QUOTENAME(@SchemaName) + '.' + QUOTENAME(@tbl);
        EXEC (@sql);
        COMMIT;
        IF @@ERROR <> 0 GOTO ErrHandler;
        FETCH NEXT FROM tbl_cursor INTO @tbl;
    END
    CLOSE tbl_cursor; DEALLOCATE tbl_cursor;
    RETURN 0;
ErrHandler:
    RAISERROR('Rebuild failed for %s', 16, 1, @tbl);
END
GO
