CREATE PROC dm.usp_Log_Audit @ProcName VARCHAR(200), @Rows BIGINT
AS
INSERT INTO audit.ProcLog (ProcName, RowsAffected, LoggedAt)
VALUES (@ProcName, @Rows, GETDATE());
GO
