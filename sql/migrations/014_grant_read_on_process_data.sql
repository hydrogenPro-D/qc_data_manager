/* 014_grant_read_on_process_data.sql
   ---------------------------------------------------------------------------
   Lets the application read the process log, for the Vat column.

     GRANT SELECT ON dbo.ProcessData TO qc_data_manager_role

   WHY IT IS NEEDED. 002 grants SELECT on dbo.MetaData alone, one object at a
   time rather than the whole schema, so nothing else under dbo is reachable.
   The vat an electrode was plated in is not in MetaData: it is the step number
   in ProcessData, joined in by marking at display time and never written back.

   READ ONLY, AND ONE OBJECT. The application has no business writing to the
   process log, and no business reading the rest of dbo either.

   CONDITIONAL, like the MetaData grant in 002. dbo.ProcessData is a table of
   the production system, not one this project creates, so a database that only
   carries a MetaData snapshot does not have it. The application copes: with no
   vats to show it leaves the column off the table rather than showing it empty
   for every row.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF DATABASE_PRINCIPAL_ID(N'qc_data_manager_role') IS NULL
    THROW 50000, N'Run 002 first (creates the role).', 1;

IF OBJECT_ID(N'[dbo].[ProcessData]') IS NOT NULL
    GRANT SELECT ON OBJECT::[dbo].[ProcessData] TO [qc_data_manager_role];
ELSE
    PRINT N'No dbo.ProcessData here, SELECT on it was not granted. The Vat column will not be shown.';

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'014_grant_read_on_process_data.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'014_grant_read_on_process_data.sql');
GO
