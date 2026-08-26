/* 006_create_sem_edx_reference_tables.sql
   ---------------------------------------------------------------------------
   The SEM EDX generations.

     sem_generation   Gen3.1 and Gen4.1, one tab each

   One column, and that is the point: it is the foreign key domain for
   sem_electrode, and usp_sem_sample_import checks against it, so a mistyped
   generation cannot become data. Nothing else about a generation is stored.

   WHAT EACH SHEET MEASURES IS NOT HERE. Which elements and ratios a tab shows,
   and in what order, is presentation, so it lives in config/config.json under
   sem_edx.layout. It used to be a table because the ETL discovered it from the
   workbook headers; once the workbooks are retired nothing discovers it, and a
   table nobody computes is a constant that happens to live in SQL.

   The order the tabs appear in is not here either. GENERATIONS in
   sem_edx_source.py declares it, oldest first, the same way STAGE_ORDER
   declares the board.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_generation]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_generation] (
        [generation] NVARCHAR(16) NOT NULL
                     CONSTRAINT [PK_sem_generation] PRIMARY KEY
    );

MERGE [qc_data].[sem_generation] AS target
USING (VALUES (N'Gen3.1'), (N'Gen4.1')) AS source ([generation])
    ON target.[generation] = source.[generation]
WHEN NOT MATCHED BY TARGET THEN
    INSERT ([generation]) VALUES (source.[generation]);

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'006_create_sem_edx_reference_tables.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'006_create_sem_edx_reference_tables.sql');
GO
