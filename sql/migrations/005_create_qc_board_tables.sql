/* 005_create_qc_board_tables.sql
   ---------------------------------------------------------------------------
   The QC board, moved off data/qc_board.json.

     qc_board_stage    the columns of the board, and which of them carry a
                       verdict. The order they appear in and what they are
                       labelled are the GUI's business: STAGE_ORDER and
                       STAGE_LABELS.
     qc_board_entry    the electrodes on the board now, one row each
     qc_board_archive  verdicts frozen by "mark all as done", which no longer
                       depend on re-running the propagation rules

   The board file also holds a last_action section for the undo button. That is
   one step of local editing state, not a record of anything, so it stays on the
   machine and is not migrated.

   Moving this table is the point of the exercise: the JSON file is per machine,
   so today two operators cannot see each other's board.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[electrode_status_kind]', N'U') IS NULL
    THROW 50000, N'Run 004 first (creates the status lookup).', 1;

/* ---- the columns of the board --------------------------------------------- */
IF OBJECT_ID(N'[qc_data].[qc_board_stage]', N'U') IS NULL
    CREATE TABLE [qc_data].[qc_board_stage] (
        [stage]  NVARCHAR(32) NOT NULL
                 CONSTRAINT [PK_qc_board_stage] PRIMARY KEY,
        /* Set only on the two terminal stages: reaching one is what gives the
           electrode a verdict. Everything earlier leaves it Not Tested. This
           is what usp_qc_board_archive reads to know which cards leave the
           board, rather than naming the two stages in SQL. */
        [status] NVARCHAR(32) NULL
                 CONSTRAINT [FK_qc_board_stage_kind]
                 REFERENCES [qc_data].[electrode_status_kind]([status])
    );

MERGE [qc_data].[qc_board_stage] AS target
USING (VALUES
        (N'waiting_for_qc',       NULL),
        (N'sem_done',             NULL),
        (N'ic_done',              NULL),
        (N'waiting_for_approval', NULL),
        (N'waiting_for_retest',   NULL),
        (N'retest_ongoing',       NULL),
        (N'release',              N'Tested and Released'),
        (N'scrapped',             N'Tested and Scrapped')
      ) AS source ([stage], [status])
    ON target.[stage] = source.[stage]
WHEN MATCHED THEN UPDATE SET target.[status] = source.[status]
WHEN NOT MATCHED BY TARGET THEN
    INSERT ([stage], [status]) VALUES (source.[stage], source.[status]);

/* ---- the electrodes on the board now -------------------------------------- */
IF OBJECT_ID(N'[qc_data].[qc_board_entry]', N'U') IS NULL
    CREATE TABLE [qc_data].[qc_board_entry] (
        /* An electrode is in exactly one stage, so the marking is the key. */
        [electrode]      NVARCHAR(32) NOT NULL
                         CONSTRAINT [PK_qc_board_entry] PRIMARY KEY,
        [stage]          NVARCHAR(32) NOT NULL
                         CONSTRAINT [FK_qc_board_entry_stage]
                         REFERENCES [qc_data].[qc_board_stage]([stage]),
        [electrode_type] NVARCHAR(16) NOT NULL
                         CONSTRAINT [CK_qc_board_entry_type]
                         CHECK ([electrode_type] IN (N'ANODE', N'CATHODE')),
        /* Findings, not verdicts: neither changes the status on its own. */
        [cracked]        BIT          NOT NULL
                         CONSTRAINT [DF_qc_board_entry_cracked] DEFAULT 0,
        /* NULL means the IC test has not been run. The application spells that
           as an empty string, and maps the two on the way through. */
        [ic_result]      NVARCHAR(16) NULL
                         CONSTRAINT [CK_qc_board_entry_ic]
                         CHECK ([ic_result] IN (N'passed', N'failed')),
        [updated_at_utc] DATETIME2    NOT NULL
                         CONSTRAINT [DF_qc_board_entry_at] DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION   NOT NULL
    );

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_qc_board_entry_stage'
                 AND object_id = OBJECT_ID(N'[qc_data].[qc_board_entry]'))
    CREATE INDEX [IX_qc_board_entry_stage]
        ON [qc_data].[qc_board_entry] ([stage], [electrode_type]);

/* ---- verdicts frozen by "mark all as done" -------------------------------- */
IF OBJECT_ID(N'[qc_data].[qc_board_archive]', N'U') IS NULL
    CREATE TABLE [qc_data].[qc_board_archive] (
        [electrode]      NVARCHAR(32) NOT NULL
                         CONSTRAINT [PK_qc_board_archive] PRIMARY KEY,
        [status]         NVARCHAR(32) NOT NULL
                         CONSTRAINT [FK_qc_board_archive_kind]
                         REFERENCES [qc_data].[electrode_status_kind]([status]),
        /* Which decision drove this one. The same marking when the electrode
           was tested itself, a neighbour's when the verdict was swept onto it. */
        [caused_by]      NVARCHAR(32) NOT NULL,
        /* Pull this one for testing first: it sits just outside a scrap's
           margin, where the fault most likely started or stopped. */
        [test_first]     BIT          NOT NULL
                         CONSTRAINT [DF_qc_board_archive_first] DEFAULT 0,
        [archived_at_utc] DATETIME2   NOT NULL
                         CONSTRAINT [DF_qc_board_archive_at] DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION   NOT NULL
    );

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_qc_board_archive_caused_by'
                 AND object_id = OBJECT_ID(N'[qc_data].[qc_board_archive]'))
    CREATE INDEX [IX_qc_board_archive_caused_by]
        ON [qc_data].[qc_board_archive] ([caused_by]);

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'005_create_qc_board_tables.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'005_create_qc_board_tables.sql');
GO
