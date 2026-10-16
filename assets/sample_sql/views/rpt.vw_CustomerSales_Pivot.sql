CREATE VIEW [rpt].[vw_CustomerSales_Pivot]
AS
SELECT Segment, [Gold], [Silver], [Bronze]
FROM (SELECT s.Segment, t.Tier, s.Amount
      FROM rpt.vw_SalesSummary s
      CROSS APPLY dm.mtvf_CustomerTiers(2026) t) src
PIVOT (SUM(Amount) FOR Tier IN ([Gold], [Silver], [Bronze])) p;
GO
