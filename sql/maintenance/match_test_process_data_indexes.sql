/* Give dataloggingTest the dbo.ProcessData indexes that dataloggingDev and
   production already have. The ETL loads Test as a plain heap, so every join
   on Marking scans 361k rows and the QC sampling rule is slower here than it
   will ever be in production.

   Idempotent. Note that ETL 06 with --recreate drops the table, which takes
   these with it, so run this again after that. */

IF DB_NAME() <> N'dataloggingTest'
    THROW 50000, N'Refusing to run: this is for dataloggingTest only.', 1;
GO

/* Clustered first, so the two below are built against it rather than rebuilt
   when it arrives. Id is unique and not null, so this cannot fail on data. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'[dbo].[ProcessData]')
                 AND name = N'PK_ProcessData')
    ALTER TABLE [dbo].[ProcessData]
        ADD CONSTRAINT [PK_ProcessData] PRIMARY KEY CLUSTERED ([Id]);
GO

/* One electrode's process rows, which is the join the sampling rule makes. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'[dbo].[ProcessData]')
                 AND name = N'IX_ProcessData_Marking')
    CREATE NONCLUSTERED INDEX [IX_ProcessData_Marking]
        ON [dbo].[ProcessData] ([Marking]);
GO

/* A whole vat, which is how the rule counts along one. */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID(N'[dbo].[ProcessData]')
                 AND name = N'IX_ProcessData_Vat')
    CREATE NONCLUSTERED INDEX [IX_ProcessData_Vat]
        ON [dbo].[ProcessData] ([Proces], [Marking]);
GO

/* Should list the same three Dev has. */
SELECT i.name AS [index], i.type_desc, i.is_unique,
       STUFF((SELECT ', ' + c.name
                FROM sys.index_columns ic
                JOIN sys.columns c ON c.object_id = ic.object_id
                                  AND c.column_id = ic.column_id
               WHERE ic.object_id = i.object_id AND ic.index_id = i.index_id
                 AND ic.is_included_column = 0
               ORDER BY ic.key_ordinal FOR XML PATH('')), 1, 2, '') AS [keys]
  FROM sys.indexes AS i
 WHERE i.object_id = OBJECT_ID(N'[dbo].[ProcessData]') AND i.type > 0
 ORDER BY i.index_id;
GO
