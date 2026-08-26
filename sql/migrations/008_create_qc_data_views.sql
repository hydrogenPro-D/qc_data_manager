/* 008_create_qc_data_views.sql
   ---------------------------------------------------------------------------
   What the application reads. Every read goes through a view, so the table
   shapes behind them can change without touching the app.

     vw_electrode_status   one row per electrode, its QC status and where it
                           came from, including the ones set by hand
     vw_qc_board           the electrodes on the board now
     vw_qc_board_archive   verdicts already frozen
     vw_sem_measurement    every SEM reading, one row per element
     vw_change_watermark   (max_row_version, row_count) per entity

   ONE ROW PER ELEMENT READING, not a column per element, because a view cannot
   have a column list that depends on its data. Which elements exist differs by
   generation and changes, so the application pivots these rows into its wide
   table. That keeps the dynamic part in Python, where it already lives, instead
   of forcing dynamic SQL down here.

   CREATE OR ALTER through EXEC, because a view has to be the first statement in
   its batch and the playbook keeps each file to a single batch. Re-running the
   file therefore redefines the views rather than failing.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_sample]', N'U') IS NULL
    THROW 50000, N'Run 007 first (creates the SEM tables).', 1;

/* ---- electrode statuses ---------------------------------------------------- */
/* [source] is the whole story of where a status came from: a curated range, a
   recorded test, or a person on the Electrodes tab. There is no second table
   layered on top, so this view is the answer, not one of two halves of it. */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_electrode_status] AS
    SELECT [electrode],
           [status],
           [source],
           [updated_at_utc]
      FROM [qc_data].[electrode_status];
');

/* ---- the QC board ---------------------------------------------------------- */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_qc_board] AS
    SELECT [electrode],
           [stage],
           [electrode_type],
           [cracked],
           [ic_result],
           [updated_at_utc]
      FROM [qc_data].[qc_board_entry];
');

EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_qc_board_archive] AS
    SELECT [electrode],
           [status],
           [caused_by],
           [test_first],
           [archived_at_utc]
      FROM [qc_data].[qc_board_archive];
');

/* ---- SEM EDX --------------------------------------------------------------- */
/* Every reading, with its sample repeated beside it. LEFT JOIN to the readings,
   so a sample recorded with no numbers yet still reaches the application
   instead of vanishing from the table. The captures are folded into one cell
   rather than joined, which would multiply the rows by the number of images. */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_sem_measurement] AS
    SELECT e.[generation],
           e.[electrode],
           e.[electrode_type],
           e.[folder],
           s.[position],
           s.[zoom],
           s.[operator],
           s.[note],
           s.[measured_on],
           r.[point],
           r.[symbol],
           r.[value],
           i.[images]
      FROM [qc_data].[sem_electrode] AS e
      JOIN [qc_data].[sem_sample] AS s
        ON s.[generation] = e.[generation]
       AND s.[electrode]  = e.[electrode]
      LEFT JOIN [qc_data].[sem_element_reading] AS r
        ON r.[generation] = s.[generation]
       AND r.[electrode]  = s.[electrode]
       AND r.[position]   = s.[position]
      OUTER APPLY (
            SELECT STRING_AGG(x.[filename], N''|'')
                   WITHIN GROUP (ORDER BY x.[ordinal]) AS [images]
              FROM [qc_data].[sem_sample_image] AS x
             WHERE x.[generation] = s.[generation]
               AND x.[electrode]  = s.[electrode]
               AND x.[position]   = s.[position]
      ) AS i;
');

/* ---- what the application polls -------------------------------------------- */
/* (MAX(row_version), COUNT(*)) per entity. The pair is what makes a deletion
   visible: the maximum never falls when a row goes, but the count does.
   Grouped, because one screen is fed by several tables and should reload when
   any of them moves. */
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
    )
    SELECT [entity],
           ISNULL(MAX(CONVERT(BIGINT, [row_version])), 0) AS [max_row_version],
           COUNT_BIG(*)                                   AS [row_count]
      FROM [source]
     GROUP BY [entity];
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'008_create_qc_data_views.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'008_create_qc_data_views.sql');
GO
