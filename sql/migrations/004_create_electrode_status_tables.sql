/* 004_create_electrode_status_tables.sql
   ---------------------------------------------------------------------------
   What the Electrodes tab reads.

     electrode_status_kind   the six statuses, as a lookup so a typo cannot
                             enter the data and a rename is a migration
     electrode_status        one row per electrode, what the tab reads

   ONE STATUS TABLE, NOT TWO. A status set by hand updates the electrode's own
   row and says so in [source]. The alternative, a second table layered on top,
   only pays for itself if something can put the previous value back, and
   nothing can: the value would have come from tested_electrodes.csv, which
   never reaches this schema. So the tab has no "clear override" and to change a
   hand set status you set another one.

   THE LOADER MUST NOT OVERWRITE source = 'manual'. data/ETL/04_load_to_database.py
   rebuilds this table from the curated ranges on every run, and without that
   condition each run would silently undo every correction anybody made.

   The electrode is keyed by its marking rather than by dbo.MetaData.Id, and
   there is deliberately no foreign key to it: SEM has already turned up
   electrodes the metadata table has never heard of, and a status for one of
   those is still worth keeping.

   The authored ranges themselves are NOT stored here. They live in
   data/csv/electrode_ranges.csv, 01_export_electrode_status.py turns them into
   the rows below, and nothing ever reads them back: the range view on the tab
   is collapsed at runtime from these per-electrode statuses.

   Every mutable table carries a ROWVERSION, because the application polls
   (MAX(row_version), COUNT(*)) to notice another machine's changes.

   There is no "who changed it" column anywhere in this schema. Every operator
   connects as the same contained user, so SUSER_SNAME() would record one
   constant on every row.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

/* ---- the statuses an electrode can hold ---------------------------------- */
/* A short list whose whole purpose is that a typo cannot become a status.
   Which of them count as released, or as actually tested, is decided in the
   application: see merged_status in electrode_columns.py. */
IF OBJECT_ID(N'[qc_data].[electrode_status_kind]', N'U') IS NULL
    CREATE TABLE [qc_data].[electrode_status_kind] (
        [status] NVARCHAR(32) NOT NULL
                 CONSTRAINT [PK_electrode_status_kind] PRIMARY KEY
    );

MERGE [qc_data].[electrode_status_kind] AS target
USING (VALUES (N'Tested and Released'), (N'Released'),
              (N'Tested and Scrapped'), (N'Scrapped'),
              (N'Needs testing'),       (N'Not Tested')
      ) AS source ([status])
    ON target.[status] = source.[status]
WHEN NOT MATCHED BY TARGET THEN INSERT ([status]) VALUES (source.[status]);

/* ---- one row per electrode ------------------------------------------------ */
IF OBJECT_ID(N'[qc_data].[electrode_status]', N'U') IS NULL
    CREATE TABLE [qc_data].[electrode_status] (
        [electrode]      NVARCHAR(32) NOT NULL
                         CONSTRAINT [PK_electrode_status] PRIMARY KEY,
        [status]         NVARCHAR(32) NOT NULL
                         CONSTRAINT [FK_electrode_status_kind]
                         REFERENCES [qc_data].[electrode_status_kind]([status]),
        /* Where the status came from. 'manual' is the one the loader must
           leave alone; the other two it owns and rebuilds. */
        [source]         NVARCHAR(16) NOT NULL
                         CONSTRAINT [DF_electrode_status_source] DEFAULT N'range'
                         CONSTRAINT [CK_electrode_status_source]
                         CHECK ([source] IN (N'range', N'tested', N'manual')),
        [updated_at_utc] DATETIME2    NOT NULL
                         CONSTRAINT [DF_electrode_status_at]
                         DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION   NOT NULL
    );

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_electrode_status_status'
                 AND object_id = OBJECT_ID(N'[qc_data].[electrode_status]'))
    CREATE INDEX [IX_electrode_status_status]
        ON [qc_data].[electrode_status] ([status]);

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'004_create_electrode_status_tables.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'004_create_electrode_status_tables.sql');
GO
