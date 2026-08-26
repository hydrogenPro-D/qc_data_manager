/* 001_create_schema_and_ledger.sql
   ---------------------------------------------------------------------------
   Creates the qc_data schema and the ledger every later file records itself in.
   Run this one first, and run it against the test database before production.

   Every file in this folder guards on the database name. Azure SQL has no USE,
   so that guard is the only thing standing between you and a wrong-database
   run. When a production database joins dataloggingDev and dataloggingTest, add
   its name to the list in every file.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'qc_data')
    EXEC (N'CREATE SCHEMA [qc_data]');

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    CREATE TABLE [qc_data].[schema_migrations] (
        [id]             INT IDENTITY PRIMARY KEY,
        [filename]       NVARCHAR(260) NOT NULL UNIQUE,
        [applied_at_utc] DATETIME2     NOT NULL
                         CONSTRAINT [DF_schema_migrations_at] DEFAULT SYSUTCDATETIME(),
        [applied_by]     NVARCHAR(256) NOT NULL
                         CONSTRAINT [DF_schema_migrations_by] DEFAULT SUSER_SNAME()
    );

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'001_create_schema_and_ledger.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'001_create_schema_and_ledger.sql');
GO
