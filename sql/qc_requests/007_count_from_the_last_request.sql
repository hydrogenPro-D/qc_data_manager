/* 007_count_from_the_last_request.sql
   ---------------------------------------------------------------------------
   Changing a sampling interval raised requests for electrodes made weeks ago.
   Reported from Production Data Manager, 13.10.2026.

     usp_raise_due_requests   counts from the last request, not a fixed origin
     vw_vat_sampling_status   counts the same way, so the panel still agrees

   WHAT WENT WRONG. The rule planned the whole schedule from one origin, the
   last electrode tested in the vat, every time it ran. At an interval of 25 the
   boundaries land on one set of electrodes and at 26 on a different set, so
   changing the number made every past boundary move onto an electrode with no
   request, and the sweep raised all of them at once. Vat 29 gained 15 requests
   in one instant for electrodes nobody can retrieve.

   NOT A ONE OFF. Every interval floods, not just 26: on the test data, setting
   vat 28 to 21 would have raised 46 and vat 30 to 21 would have raised 31. Any
   operator touching the field in the QC Sampling tab triggered it.

   THE FIX IS TO COUNT FROM THE LAST REQUEST. A new interval then decides what
   happens next and reconsiders nothing already produced, because everything
   before the last request is behind the anchor. A vat with no requests yet
   still starts from the last electrode tested in it.

   ONE PER VAT PER RUN falls out of this rather than being bolted on: the anchor
   moves to each new request, so a run can only ever reach the first boundary
   past it. A backlog drains at one a minute instead of arriving as a flood,
   which is what Production Data Manager asked for.

   ONLY AUTOMATIC REQUESTS ANCHOR IT. A manual request is somebody asking for
   one electrode on purpose, not the sampling coming round, so it no longer
   shifts the schedule. Interval and fault requests both do.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[vw_sampling_position]', N'V') IS NULL
    THROW 50000, N'Run qc_requests 006 first (creates the shared views).', 1;
GO

/* ---- where each vat's count starts from ------------------------------------ */
/* The last automatic request in the vat, or the last electrode tested in it
   when there are none. One row per vat, and the only place this is decided:
   the rule and the panel both read it, so they cannot drift apart. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_count_origin] AS
    SELECT p.[vat],
           MAX(CASE WHEN r.[electrode] IS NOT NULL THEN p.[position] END)
               AS [requested_at],
           MAX(CASE WHEN t.[electrode] IS NOT NULL THEN p.[position] END)
               AS [tested_at],
           /* Preferring the request: it is the later of the two in practice,
              and a vat that has been sampled is counted from its sampling. */
           COALESCE(
               MAX(CASE WHEN r.[electrode] IS NOT NULL THEN p.[position] END),
               MAX(CASE WHEN t.[electrode] IS NOT NULL THEN p.[position] END))
               AS [position],
           MAX(p.[position]) AS [latest]
      FROM [qc_requests].[vw_sampling_position] AS p
      LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
             ON t.[electrode] = p.[electrode]
      LEFT JOIN [qc_requests].[qc_request] AS r
             ON r.[electrode] = p.[electrode]
            AND r.[kind] <> N'manual'
     GROUP BY p.[vat];
GO

/* ---- the rule -------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

    /* At most one row per vat: [position] is unique within a vat, so the
       equality can match only the single electrode one interval past the
       anchor. It raises nothing when that electrode has not been made yet. */
    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat])
    SELECT p.[electrode], N'interval', N'awaiting_production', p.[vat]
      FROM [qc_requests].[vw_sampling_position] AS p
      JOIN [qc_requests].[vw_vat_count_origin] AS o ON o.[vat] = p.[vat]
      JOIN [qc_requests].[vat_sampling_interval] AS i ON i.[vat] = p.[vat]
     WHERE p.[position] = o.[position] + i.[electrodes_between]
       /* Belt and braces. The anchor is the last request, so the electrode an
          interval past it cannot already have one, but two sweeps racing would
          otherwise both try to insert the same marking. */
       AND NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request] AS r
                        WHERE r.[electrode] = p.[electrode]);

    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

/* ---- the panel, counting the same way -------------------------------------- */
/* [last_sampled_electrode] is whatever the countdown counts from, which is what
   the specification asked for. It is now the last electrode REQUESTED in the
   vat, falling back to the last one tested, so the two lines still agree. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_status] AS
    SELECT o.[vat],
           i.[electrodes_between]                        AS [sampling_interval],
           a.[electrode]                                 AS [last_sampled_electrode],
           t.[at_utc]                                    AS [last_sampled_at_utc],
           CONVERT(INT, o.[latest] - o.[position])       AS [produced_since],
           CASE
               /* Nothing tested and nothing requested, or no interval set.
                  Either way the rule raises nothing here, so there is no
                  number to count down. */
               WHEN o.[position] IS NULL
                 OR i.[electrodes_between] IS NULL THEN NULL
               /* The next one is already made, so the next sweep raises it. */
               WHEN o.[latest] >= o.[position] + i.[electrodes_between] THEN 0
               ELSE CONVERT(INT, o.[position] + i.[electrodes_between]
                                 - o.[latest])
           END                                           AS [remaining]
      FROM [qc_requests].[vw_vat_count_origin] AS o
      LEFT JOIN [qc_requests].[vw_sampling_position] AS a
             ON a.[vat] = o.[vat] AND a.[position] = o.[position]
      LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
             ON t.[electrode] = a.[electrode]
      LEFT JOIN [qc_requests].[vat_sampling_interval] AS i
             ON i.[vat] = o.[vat];
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'007_count_from_the_last_request.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'007_count_from_the_last_request.sql');
GO
