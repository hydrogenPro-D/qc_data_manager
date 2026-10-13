/* 002_create_vat_sampling_interval.sql
   ---------------------------------------------------------------------------
   How often each vat is sampled, moved off a JSON file on one machine.

     vat_sampling_interval    one row per vat, test one electrode in every N
     vw_vat_sampling_interval what the QC Sampling tab reads
     usp_vat_interval_set     what it writes

   WHY IT IS IN qc_requests RATHER THAN qc_data. It is QC's setting, but the
   rule that turns it into a request is shared: the procedure that works out
   which electrode is due next reads this table, and either application may be
   the one that runs it. Anything the shared rule depends on is part of the
   contract, so it lives on the contract's side of the fence.

   SEEDED AT 25 FOR EVERY VAT, not carried over from the JSON file this
   replaces. That file held three keys for two vats, "28", "30" and "Vat 28",
   with the two spellings of 28 disagreeing, so there was nothing there worth
   keeping.

   A VAT WITH NO ROW IS NOT SAMPLED. The rule that works out which electrode is
   due reads this table and nothing else, so there is no default hiding in SQL
   for it to fall back on. A vat that appears on the line later therefore waits
   for somebody to set an interval rather than quietly starting to raise
   requests at a number nobody chose.

   NO HISTORY. An interval is a setting, not a record: changing one updates the
   row. Nothing needs the old value, because the next due electrode is worked
   out from the interval as it stands when the question is asked, and requests
   already raised are left alone. See QC_WORKFLOW_DESIGN.md.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 001 first (creates the schema + ledger).', 1;

IF DATABASE_PRINCIPAL_ID(N'qc_data_manager_role') IS NULL
    THROW 50000, N'Run qc_data 002 first (creates this application''s role).', 1;

/* ---- the setting ----------------------------------------------------------- */
/* The vat as the rest of the application names it: the bare step number from
   dbo.ProcessData, "28", not "Vat 28". A lookup table would be nicer, but the
   vats are the production line's, not ours, and inventing a list here would be
   one more thing to keep in step with it. */
IF OBJECT_ID(N'[qc_requests].[vat_sampling_interval]', N'U') IS NULL
    CREATE TABLE [qc_requests].[vat_sampling_interval] (
        [vat]                NVARCHAR(32) NOT NULL
                             CONSTRAINT [PK_vat_sampling_interval] PRIMARY KEY,
        /* One means every electrode, which is not sampling at all, but it is
           allowed so the figure can be typed while somebody is thinking. */
        [electrodes_between] INT NOT NULL
                             CONSTRAINT [CK_vat_sampling_interval_range]
                             CHECK ([electrodes_between] BETWEEN 1 AND 1000),
        [updated_at_utc]     DATETIME2 NOT NULL
                             CONSTRAINT [DF_vat_sampling_interval_at]
                             DEFAULT SYSUTCDATETIME(),
        /* What the polling watermark compares, the same as every other table
           this application reads. */
        [row_version]        ROWVERSION NOT NULL
    );

/* ---- the vats being sampled today ----------------------------------------- */
/* MERGE rather than INSERT, so re-running the file is harmless and, more to the
   point, so it never writes over an interval somebody has since changed. A
   second run adds a vat that was missing and leaves the rest alone. */
MERGE [qc_requests].[vat_sampling_interval] AS target
USING (VALUES (N'28', 25), (N'29', 25), (N'30', 25))
      AS source ([vat], [electrodes_between])
   ON target.[vat] = source.[vat]
WHEN NOT MATCHED
    THEN INSERT ([vat], [electrodes_between])
         VALUES (source.[vat], source.[electrodes_between]);

/* ---- read ------------------------------------------------------------------ */
EXEC (N'
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_interval] AS
    SELECT [vat], [electrodes_between], [updated_at_utc], [row_version]
    FROM [qc_requests].[vat_sampling_interval];
');

/* ---- write ----------------------------------------------------------------- */
/* One statement, so setting an interval for a vat nobody has set before and
   changing one that exists are the same call. The tab has no idea which it is
   doing and should not have to. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_requests].[usp_vat_interval_set]
    @vat                 NVARCHAR(32),
    @electrodes_between  INT
AS
BEGIN
    SET NOCOUNT ON;

    IF @vat IS NULL OR LTRIM(RTRIM(@vat)) = N''''
        THROW 50000, N''A vat is needed.'', 1;
    IF @electrodes_between IS NULL OR @electrodes_between NOT BETWEEN 1 AND 1000
        THROW 50000, N''An interval has to be between 1 and 1000.'', 1;

    MERGE [qc_requests].[vat_sampling_interval] AS target
    USING (SELECT LTRIM(RTRIM(@vat)) AS [vat],
                  @electrodes_between AS [electrodes_between]) AS source
       ON target.[vat] = source.[vat]
    WHEN MATCHED AND target.[electrodes_between] <> source.[electrodes_between]
        THEN UPDATE SET [electrodes_between] = source.[electrodes_between],
                        [updated_at_utc]     = SYSUTCDATETIME()
    WHEN NOT MATCHED
        THEN INSERT ([vat], [electrodes_between])
             VALUES (source.[vat], source.[electrodes_between]);

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- grants ---------------------------------------------------------------- */
/* Named objects, not the schema. Everything in here is reachable by two
   applications, so a grant on the schema would hand each of them whatever is
   added next without anybody deciding to. */
GRANT SELECT  ON OBJECT::[qc_requests].[vw_vat_sampling_interval] TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_vat_interval_set]     TO [qc_data_manager_role];

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'002_create_vat_sampling_interval.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'002_create_vat_sampling_interval.sql');
GO
