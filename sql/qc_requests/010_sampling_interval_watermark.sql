/* 010_sampling_interval_watermark.sql
   ---------------------------------------------------------------------------
   Something cheap for Production Data Manager to poll when QC changes how
   often a vat is sampled. Answers QC_SAMPLING_WATERMARK.md.

     vw_vat_sampling_watermark   one row per vat that has an interval set

   NOTHING NEW IS STORED. [vat_sampling_interval] has carried a ROWVERSION
   since 002, for this application's own polling, and a ROWVERSION moves on
   every insert and update to its row whoever makes it. So the marker PDM asks
   for already exists and has always been correct. What was missing is a view
   with its name on it and a grant, which is the whole job.

   NOT A GRANT ON [vw_vat_sampling_interval]. That one is QC's own read, it
   names the column [electrodes_between] as the table does, and granting it
   would make this application's working part into a contract that cannot then
   be changed. This view names the column [sampling_interval], the same as
   [vw_vat_sampling_status] already does, so the two things PDM reads agree.

   WHAT IT NOTICES, and what it does not. It moves when an interval is set or
   changed, and when a vat gets an interval for the first time. It does not
   move when electrodes are produced, so [remaining] in [vw_vat_sampling_status]
   can change while this stands still. That is the split the specification
   asked for: production is already visible to PDM through the request
   watermark and its own counts, the configuration was not visible at all.

   SETTING AN INTERVAL TO WHAT IT ALREADY WAS MOVES NOTHING, because
   [usp_vat_interval_set] only updates when the figure differs. Nothing
   changed, so nothing is announced, and PDM does not re-read the expensive
   view for a write that was a no-op.

   A MAX OVER THE WHOLE VIEW works as well, and is what PDM already does with
   [vw_qc_request]. Three rows either way, so take whichever fits the existing
   code:

       SELECT MAX([row_version]) FROM [qc_requests].[vw_vat_sampling_watermark];

   NO DELETE TO MISS. A ROWVERSION cannot announce a row that is gone, which is
   the usual trap with this kind of marker. There is no path that deletes an
   interval: [usp_vat_interval_set] inserts or updates and nothing else, and a
   vat that stops being sampled keeps its row. If a delete is ever added, this
   view needs a row count beside the version.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[vat_sampling_interval]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 002 first (creates the interval table).', 1;
GO

/* ---- the marker ------------------------------------------------------------ */
/* The interval travels with it so a caller that has re-read on a change does
   not need a second query to find out what the new figure is. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_watermark] AS
    SELECT [vat],
           [electrodes_between] AS [sampling_interval],
           [updated_at_utc],
           [row_version]
      FROM [qc_requests].[vat_sampling_interval];
GO

/* ---- the grant ------------------------------------------------------------- */
/* By name, for the one caller that asked for it. */
IF EXISTS (SELECT 1 FROM sys.database_principals
            WHERE name = N'db_production_data_manager_user')
    GRANT SELECT ON OBJECT::[qc_requests].[vw_vat_sampling_watermark]
        TO [db_production_data_manager_user];
ELSE
    PRINT 'db_production_data_manager_user does not exist here, grant skipped. Re-run this file once it does.';
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'010_sampling_interval_watermark.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'010_sampling_interval_watermark.sql');
GO
