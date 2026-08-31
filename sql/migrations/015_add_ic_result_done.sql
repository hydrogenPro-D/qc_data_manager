/* 015_add_ic_result_done.sql
   ---------------------------------------------------------------------------
   A third IC result: the test has been run, the verdict has not come back.

     qc_data.qc_board_entry.ic_result   'passed', 'failed', and now 'done'

   WHY IT IS NOT A VERDICT. Passed and failed are answers; 'done' says only that
   the instrument has finished with the electrode. It moves nothing by itself,
   the same way an accepted SEM result on its own moves nothing: the pair of
   results is what decides a column.

   WHY IT IS NOT JUST NULL. NULL is "nobody has run it", which is what an
   operator sees as Not tested. Telling that apart from "run, still waiting" is
   the whole point: one is work outstanding, the other is work done.

   Only the check constraint changes. The column is already NVARCHAR(16) and
   nullable, and NULL still means the test has not been run.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[qc_board_entry]', N'U') IS NULL
    THROW 50000, N'Run 005 first (creates the board tables).', 1;

IF EXISTS (SELECT 1 FROM sys.check_constraints
            WHERE name = N'CK_qc_board_entry_ic'
              AND parent_object_id = OBJECT_ID(N'[qc_data].[qc_board_entry]'))
    ALTER TABLE [qc_data].[qc_board_entry] DROP CONSTRAINT [CK_qc_board_entry_ic];

ALTER TABLE [qc_data].[qc_board_entry] WITH CHECK
    ADD CONSTRAINT [CK_qc_board_entry_ic]
    CHECK ([ic_result] IN (N'passed', N'failed', N'done'));

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'015_add_ic_result_done.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'015_add_ic_result_done.sql');
GO
