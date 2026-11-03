/* 006_vat_sampling_status.sql
   ---------------------------------------------------------------------------
   What Production Data Manager asked for in QC_SAMPLING_STATUS_SPEC.md: a view
   saying how many more electrodes a vat will make before QC asks for one.

     vw_sampling_position     the electrodes the rule counts, numbered
     vw_tested_electrode      what counts as having been tested
     vw_vat_sampling_status   the panel PDM draws
     usp_raise_due_requests   rewritten onto the two views above

   THE RULE AND THE VIEW NOW SHARE ONE DEFINITION. The spec says the two must
   count the same way, and the only way to be sure of that is to stop writing it
   twice. The procedure keeps its behaviour exactly: checked against the live
   test data, both formulations pick the same 82 electrodes, with nothing in
   either that is not in the other.

   EACH VIEW IS REFERENCED ONCE per query, deliberately. SQL Server expands a
   view at every mention rather than materialising it, and the electrode set
   costs about a second to build, so a formulation that mentioned it four times
   took 9 seconds where this one takes under 2.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    THROW 50000, N'Run the qc_requests migrations first (001 to 005).', 1;
GO

/* ---- the electrodes the rule counts, in order ------------------------------ */
/* The same exclusions the application makes: B markings on the production
   recipes, in a vat the process log names once. An electrode listing two vats
   is in no vat's count, which is what the screen does with it. */
CREATE OR ALTER VIEW [qc_requests].[vw_sampling_position] AS
    SELECT [electrode], [vat],
           ROW_NUMBER() OVER (
               PARTITION BY [vat]
               ORDER BY TRY_CONVERT(BIGINT,
                        SUBSTRING(LTRIM(RTRIM([electrode])), 2, 20))) AS [position]
      FROM (SELECT m.[Marking] AS [electrode],
                   CONVERT(NVARCHAR(32), MIN(p.[Proces])) AS [vat]
              FROM [dbo].[MetaData] AS m
              JOIN [dbo].[ProcessData] AS p ON p.[Marking] = m.[Marking]
             WHERE m.[Marking] LIKE N'B%'
               AND m.[Full_name] IN (N'CATHODE', N'ANODE', N'MM1', N'CATHODE NEW')
               AND p.[Proces] IN (28, 29, 30)
             GROUP BY m.[Marking]
            HAVING COUNT(DISTINCT p.[Proces]) = 1) AS [plated]
     WHERE TRY_CONVERT(BIGINT,
           SUBSTRING(LTRIM(RTRIM([electrode])), 2, 20)) IS NOT NULL;
GO

/* ---- what counts as having been tested ------------------------------------- */
/* Three places a test can be recorded, and the count runs from the last of
   them in each vat. */
CREATE OR ALTER VIEW [qc_requests].[vw_tested_electrode] AS
    SELECT [electrode], MAX([at_utc]) AS [at_utc]
      FROM (
        /* NO DATE FROM HERE. These rows are rebuilt in bulk by the loader, so
           updated_at_utc is when it last ran: 592 rows carrying 2 distinct
           times on the test database. A date that means "when the load ran"
           would be worse on an operator's screen than no date at all. */
        SELECT [electrode], CONVERT(DATETIME2, NULL) AS [at_utc]
          FROM [qc_data].[electrode_status]
         WHERE [status] IN (N'Tested and Released', N'Tested and Scrapped')
        UNION ALL
        SELECT [electrode], [updated_at_utc]
          FROM [qc_data].[qc_board_entry]
        UNION ALL
        SELECT [electrode], [archived_at_utc]
          FROM [qc_data].[qc_board_archive]
         WHERE [status] IN (N'Tested and Released', N'Tested and Scrapped')
      ) AS [recorded]
     GROUP BY [electrode];
GO

/* ---- the panel ------------------------------------------------------------- */
/* One row per vat the line actually plates, whether or not QC samples it: a vat
   with no interval set is one PDM should say is not being sampled, and it can
   only say that if the vat is on the list. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_status] AS
    SELECT [vat],
           MIN([electrodes_between]) AS [sampling_interval],
           MAX(CASE WHEN [position] = [anchor] THEN [electrode] END)
               AS [last_sampled_electrode],
           MAX(CASE WHEN [position] = [anchor] THEN [at_utc] END)
               AS [last_sampled_at_utc],
           CONVERT(INT, MIN([latest]) - MIN([anchor])) AS [produced_since],
           CASE
               /* Nothing tested in this vat, or nobody has set an interval.
                  Either way the rule raises nothing here, so there is no
                  number to count down and NULL says so. */
               WHEN MIN([anchor]) IS NULL
                 OR MIN([electrodes_between]) IS NULL THEN NULL
               /* One is due and unrequested, which is the procedure's own
                  WHERE clause: it would raise it on the next sweep. */
               WHEN MAX(CASE WHEN [position] > [anchor]
                              AND [has_request] = 0
                              AND ([position] - [anchor])
                                  % [electrodes_between] = 0
                             THEN 1 ELSE 0 END) = 1 THEN 0
               ELSE MIN([electrodes_between])
                    - ((MIN([latest]) - MIN([anchor]))
                       % MIN([electrodes_between]))
           END AS [remaining]
      FROM (
        SELECT p.[vat], p.[position], p.[electrode],
               t.[at_utc],
               i.[electrodes_between],
               CASE WHEN r.[electrode] IS NULL THEN 0 ELSE 1 END AS [has_request],
               /* The anchor and the end of the vat, as windows over the one
                  pass, rather than as two more visits to the electrode set. */
               MAX(CASE WHEN t.[electrode] IS NOT NULL THEN p.[position] END)
                   OVER (PARTITION BY p.[vat]) AS [anchor],
               MAX(p.[position]) OVER (PARTITION BY p.[vat]) AS [latest]
          FROM [qc_requests].[vw_sampling_position] AS p
          LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
                 ON t.[electrode] = p.[electrode]
          LEFT JOIN [qc_requests].[qc_request] AS r
                 ON r.[electrode] = p.[electrode]
          LEFT JOIN [qc_requests].[vat_sampling_interval] AS i
                 ON i.[vat] = p.[vat]
      ) AS [placed]
     GROUP BY [vat];
GO

/* ---- the rule, on the same two views --------------------------------------- */
/* Unchanged in behaviour. A vat with nothing tested has a NULL anchor, so every
   comparison below is NULL and it raises nothing, which is what the old JOIN to
   an aggregate did by dropping the vat. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat])
    SELECT [electrode], N'interval', N'awaiting_production', [vat]
      FROM (
        SELECT p.[electrode], p.[vat], p.[position],
               i.[electrodes_between],
               MAX(CASE WHEN t.[electrode] IS NOT NULL THEN p.[position] END)
                   OVER (PARTITION BY p.[vat]) AS [anchor]
          FROM [qc_requests].[vw_sampling_position] AS p
          LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
                 ON t.[electrode] = p.[electrode]
          JOIN [qc_requests].[vat_sampling_interval] AS i
                 ON i.[vat] = p.[vat]
      ) AS [placed]
     WHERE [position] > [anchor]
       AND ([position] - [anchor]) % [electrodes_between] = 0
       AND NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request] AS r
                        WHERE r.[electrode] = [placed].[electrode]);

    /* [note] kept, and always NULL, so a caller already reading the second
       column does not have to change to keep working. */
    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

/* ---- the grant ------------------------------------------------------------- */
/* The one view PDM reads, by name. The two it is built on stay ungranted:
   ownership chaining covers reading them from here, and they are this
   project's working parts, not a contract. */
IF EXISTS (SELECT 1 FROM sys.database_principals
            WHERE name = N'db_production_data_manager_user')
    GRANT SELECT ON OBJECT::[qc_requests].[vw_vat_sampling_status]
        TO [db_production_data_manager_user];
ELSE
    PRINT 'db_production_data_manager_user does not exist here, grant skipped. Re-run this file once it does.';
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'006_vat_sampling_status.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'006_vat_sampling_status.sql');
GO
