/* 017_watermark_includes_requests.sql
   ---------------------------------------------------------------------------
   Let the polling notice a QC request.

     qc_data.vw_change_watermark  gains a qc_requests entity

   WHY IT IS NEEDED. The Electrodes tab shows a QC request column, and the
   other application writes those rows. Nothing in the watermark moved when it
   did, so a request raised in Production Data Manager did not reach this one
   until somebody restarted it. That is precisely the case the watermark exists
   for: another machine changing what this screen says.

   A SEPARATE ENTITY rather than folding the rows into electrode_status. The
   Electrodes tab already compares a tuple of several watermarks, so one more
   costs a line of Python and keeps each name meaning what it says. Calling a
   request an electrode status would have saved that line and cost the next
   person an hour.

   THE VIEW LIVES IN qc_data AND NOW READS qc_requests. Both schemas are owned
   by dbo, so the ownership chain is unbroken and the application needs no
   grant on the request table: it already has SELECT on this view, and that is
   still all it uses.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    THROW 50000, N'Run the qc_requests migrations first (001 to 005).', 1;

EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_change_watermark] AS
    WITH [source] AS (
        SELECT N''electrode_status'' AS [entity],
               [row_version] FROM [qc_data].[electrode_status]
        UNION ALL
        SELECT N''qc_board'', [row_version] FROM [qc_data].[qc_board_entry]
        UNION ALL
        SELECT N''qc_board'', [row_version] FROM [qc_data].[qc_board_archive]
        UNION ALL
        SELECT N''sem_edx'', [row_version] FROM [qc_data].[sem_electrode]
        UNION ALL
        SELECT N''sem_edx'', [row_version] FROM [qc_data].[sem_sample]
        UNION ALL
        SELECT N''sem_edx'', [row_version] FROM [qc_data].[sem_element_reading]
        UNION ALL
        SELECT N''sem_edx'', [row_version] FROM [qc_data].[sem_sample_image]
        UNION ALL
        SELECT N''qc_requests'', [row_version] FROM [qc_requests].[qc_request]
    )
    SELECT [entity],
           ISNULL(MAX(CONVERT(BIGINT, [row_version])), 0) AS [max_row_version],
           COUNT_BIG(*)                                   AS [row_count]
      FROM [source]
     GROUP BY [entity];
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'017_watermark_includes_requests.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'017_watermark_includes_requests.sql');
GO
