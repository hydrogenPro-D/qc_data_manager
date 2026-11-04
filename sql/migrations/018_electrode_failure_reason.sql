/* 018_electrode_failure_reason.sql
   ---------------------------------------------------------------------------
   Why an electrode failed, for the ones the board never judged.

     qc_data.electrode_failure_reason   electrode -> ic_result, sem_result

   016 put the findings on qc_board_archive, which works for anything the board
   decided. It does not reach the 121 failures imported by the ETL on 26 August
   2026, because those were judged before the board existed and have no archive
   row at all.

   THE ARCHIVE IS NOT THE PLACE TO PUT THEM. It is read twice: once as a record
   of what happened, and once by db_manager._settled_verdicts as the set of
   decisions the propagation sweeps from. Backfilling it gave 121 year-old
   failures a live vote, each condemning its margin and opening a run of Needs
   testing beyond it, which turned 11,719 Released electrodes into 10,793 and
   produced 808 reading Waiting for additional SEM approval. That attempt was
   rolled back.

   So this table does one job and no other. Nothing reads it as a decision, so
   nothing in it can sweep, whatever is put in it. That is a property of where
   it sits rather than of how carefully it is filled.

   READ ONLY TO THE APPLICATION. There is no procedure, deliberately: the rows
   are a one-off correction of history written by an admin, not something an
   operator edits. A reason recorded from now on goes on the card, as it does
   today, and the card carries it into the archive.

   LOWEST PRECEDENCE. qc_board_db_store.findings reads the live card first,
   then the archive, then this. An electrode that has been judged properly is
   never described by this table.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'016_archive_findings.sql')
    THROW 50000, N'Run 016_archive_findings.sql first.', 1;

/* ---- the table ------------------------------------------------------------- */
/* No foreign key to anything. An electrode here may have no status row and no
   archive row: that it has neither is exactly why its reason has nowhere else
   to live. The columns match qc_board_archive's so one reader serves both. */
IF OBJECT_ID(N'[qc_data].[electrode_failure_reason]', N'U') IS NULL
    CREATE TABLE [qc_data].[electrode_failure_reason] (
        [electrode]       NVARCHAR(32) NOT NULL
                          CONSTRAINT [PK_electrode_failure_reason] PRIMARY KEY,
        /* NULL means nobody wrote this one down, the same as on the archive. */
        [ic_result]       NVARCHAR(16) NULL,
        [sem_result]      NVARCHAR(16) NULL,
        /* When it was recorded here, NOT when the electrode was judged. The
           judging happened long before and no date for it survives. */
        [recorded_at_utc] DATETIME2    NOT NULL
                          CONSTRAINT [DF_electrode_failure_reason_at]
                          DEFAULT SYSUTCDATETIME(),
        [row_version]     ROWVERSION   NOT NULL
    );
GO

/* ---- and hand it out ------------------------------------------------------- */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_electrode_failure_reason] AS
    SELECT [electrode],
           [ic_result],
           [sem_result],
           [recorded_at_utc]
      FROM [qc_data].[electrode_failure_reason];
');

/* SELECT only. The application reads this and never writes it. */
IF EXISTS (SELECT 1 FROM sys.database_principals
           WHERE name = N'qc_data_manager_role' AND type = N'R')
    GRANT SELECT ON [qc_data].[vw_electrode_failure_reason]
        TO [qc_data_manager_role];
GO

/* ---- what it holds --------------------------------------------------------- */
SELECT COUNT(*) AS [reasons_recorded] FROM [qc_data].[electrode_failure_reason];

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'018_electrode_failure_reason.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'018_electrode_failure_reason.sql');
GO
