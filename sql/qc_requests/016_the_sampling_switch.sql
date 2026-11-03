/* 016_the_sampling_switch.sql
   ---------------------------------------------------------------------------
   Automatic sampling can be switched off, and switched back on at a chosen
   starting point.

     sampling_switch            one row, on or off, for the whole line
     vw_sampling_switch         what both applications read
     usp_sampling_switch_set    what the QC Sampling tab writes
     vat_sampling_interval      gains where its count restarts from
     vw_vat_count_origin        counts from that anchor, and carries the step
     usp_raise_due_requests     raises nothing while the switch is off
     vw_vat_sampling_status     says so, rather than counting down to nothing

   WHY IT IS NEEDED. The applications are being released before the line is
   running. A couple of electrodes will be made to try them out, and the rule
   cannot tell those from production: it would ask QC to test them and ask
   production to fetch them, and there is nobody to do either.

   THE SAME PROCEDURE, STILL CALLED BY BOTH. Nothing moves out of the database
   and no caller changes: usp_raise_due_requests goes on being called every 60
   seconds from both applications, and while the switch is off it raises
   nothing and says why in [note]. A switch held in one application would only
   have been a switch for that application.

   OFF IS WHAT THIS FILE SEEDS, which makes it the one migration here that
   changes behaviour by running. It is deliberate, and it is the safe end on a
   database that has never raised a request: switching on is one button, and it
   asks where to start, where a flood of requests for electrodes made months
   ago is a mess somebody has to clear up by hand.

   ---- where the count restarts ------------------------------------------------

   WHY SWITCHING ON NEEDS AN ANSWER. The count runs from the last request in
   the vat, or the last electrode tested in it (007). Production carries on
   while the switch is off, so by the time somebody switches it back on that
   anchor is far behind, and the rule would start marching through everything
   made since, one electrode a minute, every one of them too old to fetch.

   So switching on moves each vat's anchor to the newest electrode it has, and
   the person switching it on says what happens next:

     the very next electrode     [due_next] = 1, the first one made from now
     a full interval from here   [due_next] = 0, the 25th, or whatever is set

   [due_next] CLEARS ITSELF. It applies only while the stored anchor is still
   what the count runs from, so the first request past it supersedes the anchor
   and the ordinary interval resumes. Nothing has to remember to switch it off.

   ONE SWITCH FOR THE LINE, not one per vat, like the coupon settings in 008.
   Whether QC is sampling at all is a question about QC, not about a vat. The
   per vat anchors below are the consequence of a switch-on, not settings.

   WHAT IT DOES NOT TOUCH. A request somebody raises by hand, and the requests
   raised around a SEM or IC failure. Those are answers to something that has
   happened, not a sampling rate, and they are still wanted on a line that is
   only being tried out. Requests already raised are left exactly as they are:
   switching off stops new ones, it does not withdraw old ones.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'015_a_card_outranks_a_refusal.sql')
    THROW 50000, N'Run qc_requests 015 first.', 1;
GO

/* ---- the switch ------------------------------------------------------------ */
/* One row, forced by the check on the key: a second row would be a second
   answer to a question with one. */
IF OBJECT_ID(N'[qc_requests].[sampling_switch]', N'U') IS NULL
    CREATE TABLE [qc_requests].[sampling_switch] (
        [id]             TINYINT NOT NULL
                         CONSTRAINT [PK_sampling_switch] PRIMARY KEY
                         CONSTRAINT [CK_sampling_switch_one_row]
                         CHECK ([id] = 1),
        [is_on]          BIT NOT NULL,
        [updated_at_utc] DATETIME2 NOT NULL
                         CONSTRAINT [DF_sampling_switch_at]
                         DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION NOT NULL
    );

/* Seeded off, once. A second run never writes over the answer somebody has
   given since: re-running a migration must not stop the line's sampling. */
IF NOT EXISTS (SELECT 1 FROM [qc_requests].[sampling_switch])
    INSERT INTO [qc_requests].[sampling_switch] ([id], [is_on]) VALUES (1, 0);
GO

/* ---- where each vat's count restarts --------------------------------------- */
/* The position, because that is what the rule compares, and the marking beside
   it so that a person reading this table can tell what the number was. The
   marking is not read by anything. */
IF COL_LENGTH(N'[qc_requests].[vat_sampling_interval]', N'count_from_position') IS NULL
    ALTER TABLE [qc_requests].[vat_sampling_interval]
        ADD [count_from_position] INT NULL;
GO

IF COL_LENGTH(N'[qc_requests].[vat_sampling_interval]', N'count_from_electrode') IS NULL
    ALTER TABLE [qc_requests].[vat_sampling_interval]
        ADD [count_from_electrode] NVARCHAR(32) NULL;
GO

/* NULL would have done, but a flag that is sometimes missing is read wrongly
   sooner or later, and there is a true answer for every existing row: no, the
   next one is not due. */
IF COL_LENGTH(N'[qc_requests].[vat_sampling_interval]', N'due_next') IS NULL
    ALTER TABLE [qc_requests].[vat_sampling_interval]
        ADD [due_next] BIT NOT NULL
            CONSTRAINT [DF_vat_sampling_interval_due_next] DEFAULT 0;
GO

/* ---- what the count runs from, and how far it counts ----------------------- */
/* Three candidates for the origin and the larger wins: the anchor a switch-on
   stored, the last automatic request, the last electrode tested. The anchor is
   the newest electrode at the moment it was set, so it beats everything behind
   it; the first request raised after that is ahead of it and takes over, which
   is what retires [due_next] without anything having to clear it.

   AN ELECTRODE TESTED AFTER A SWITCH-ON also takes over, and so cancels "the
   very next one". That is the existing rule, not a new one: a vat is counted
   from its last test, and a test is better evidence of where sampling stands
   than a setting from last week.

   [step] IS HERE rather than in each caller for the reason 006 gives: the rule
   and the panel have to count the same way, and the only way to be sure of
   that is to write it once. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_count_origin] AS
    SELECT g.[vat], g.[requested_at], g.[tested_at], g.[anchor], g.[latest],
           o.[position],
           CASE WHEN g.[due_next] = 1 AND o.[position] = g.[anchor] THEN 1
                ELSE g.[electrodes_between] END AS [step]
      FROM (SELECT p.[vat],
                   MAX(CASE WHEN r.[electrode] IS NOT NULL
                            THEN p.[position] END)        AS [requested_at],
                   MAX(CASE WHEN t.[electrode] IS NOT NULL
                            THEN p.[position] END)        AS [tested_at],
                   MIN(i.[count_from_position])           AS [anchor],
                   MIN(i.[electrodes_between])            AS [electrodes_between],
                   MIN(CONVERT(INT, i.[due_next]))        AS [due_next],
                   MAX(p.[position])                      AS [latest]
              FROM [qc_requests].[vw_sampling_position] AS p
              LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
                     ON t.[electrode] = p.[electrode]
              LEFT JOIN [qc_requests].[qc_request] AS r
                     ON r.[electrode] = p.[electrode]
                    AND r.[kind] <> N'manual'
              /* LEFT, so a vat nobody has set an interval for still appears.
                 Its [step] is NULL, so the rule matches nothing for it, which
                 is what 002 promised: no default hiding in SQL. */
              LEFT JOIN [qc_requests].[vat_sampling_interval] AS i
                     ON i.[vat] = p.[vat]
             GROUP BY p.[vat]) AS g
     CROSS APPLY (SELECT MAX([v]) AS [position]
                    FROM (VALUES (g.[anchor]), (g.[requested_at]),
                                 (g.[tested_at])) AS [candidate]([v])) AS o;
GO

/* ---- the rule -------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

    /* Before the switch is read, deliberately. Expiring a request nobody can
       answer is housekeeping on what is already there, and it should go on
       being done while sampling is off. */
    DECLARE @gone INT;
    EXEC [qc_requests].[usp_expire_unanswerable_requests] @expired = @gone OUTPUT;

    IF NOT EXISTS (SELECT 1 FROM [qc_requests].[sampling_switch]
                    WHERE [is_on] = 1)
    BEGIN
        /* [note] has been NULL on every row this ever returned. It is a
           sentence now, and this is the only case that puts one there, so a
           caller showing it tells whoever is watching why the number is 0. */
        SELECT 0 AS [raised],
               CONVERT(NVARCHAR(200),
                       N'Automatic sampling is switched off.') AS [note];
        RETURN;
    END

    DECLARE @coupons NVARCHAR(16) =
        (SELECT [coupons] FROM [qc_requests].[coupon_setting]
          WHERE [reason] = N'interval');

    /* A setting of [none] is QC saying it does not want these at all, which
       predates the switch and still holds. */
    IF @coupons IS NULL OR @coupons = N'none'
    BEGIN
        SELECT 0 AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
        RETURN;
    END

    /* No join to the interval table any more: [step] carries the interval, and
       is NULL for a vat that has none, so such a vat matches nothing. */
    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat], [coupons])
    SELECT p.[electrode], N'interval', N'awaiting_production', p.[vat], @coupons
      FROM [qc_requests].[vw_sampling_position] AS p
      JOIN [qc_requests].[vw_vat_count_origin] AS o ON o.[vat] = p.[vat]
     WHERE p.[position] = o.[position] + o.[step]
       AND NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request] AS r
                        WHERE r.[electrode] = p.[electrode]);

    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

/* ---- the panel, counting the same way -------------------------------------- */
/* [sampling_on] is added at the end, so a caller reading by name is unaffected
   and one reading by position still finds what it had.

   [remaining] IS NULL WHILE THE SWITCH IS OFF. NULL already means "the rule
   raises nothing here", which is exactly the case, and a countdown to a
   request that will not be raised is worse than no countdown. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_status] AS
    SELECT o.[vat],
           i.[electrodes_between]                        AS [sampling_interval],
           a.[electrode]                                 AS [last_sampled_electrode],
           t.[at_utc]                                    AS [last_sampled_at_utc],
           CONVERT(INT, o.[latest] - o.[position])       AS [produced_since],
           CASE
               WHEN s.[is_on] = 0 THEN NULL
               /* Nothing tested, nothing requested and no switch-on behind it,
                  or no interval set. Either way there is nothing to count. */
               WHEN o.[position] IS NULL OR o.[step] IS NULL THEN NULL
               WHEN o.[latest] >= o.[position] + o.[step] THEN 0
               ELSE CONVERT(INT, o.[position] + o.[step] - o.[latest])
           END                                           AS [remaining],
           CONVERT(BIT, COALESCE(s.[is_on], 1))          AS [sampling_on]
      FROM [qc_requests].[vw_vat_count_origin] AS o
      LEFT JOIN [qc_requests].[vw_sampling_position] AS a
             ON a.[vat] = o.[vat] AND a.[position] = o.[position]
      LEFT JOIN [qc_requests].[vw_tested_electrode] AS t
             ON t.[electrode] = a.[electrode]
      LEFT JOIN [qc_requests].[vat_sampling_interval] AS i
             ON i.[vat] = o.[vat]
      LEFT JOIN [qc_requests].[sampling_switch] AS s ON s.[id] = 1;
GO

/* ---- read ------------------------------------------------------------------ */
CREATE OR ALTER VIEW [qc_requests].[vw_sampling_switch] AS
    SELECT [is_on], [updated_at_utc], [row_version]
      FROM [qc_requests].[sampling_switch];
GO

/* The marker Production Data Manager polls (010) now moves when the switch
   moves as well as when an interval does, so flipping it shows up there within
   a poll. One MAX over the view still answers both, because a ROWVERSION comes
   from a single counter for the whole database and the values from the two
   tables are comparable.

   LEFT JOIN rather than CROSS JOIN: a missing switch row would otherwise empty
   the view and PDM would read that as every interval having been deleted. */
CREATE OR ALTER VIEW [qc_requests].[vw_vat_sampling_watermark] AS
    SELECT i.[vat],
           i.[electrodes_between] AS [sampling_interval],
           CONVERT(BIT, COALESCE(s.[is_on], 1)) AS [sampling_on],
           CASE WHEN s.[row_version] > i.[row_version] THEN s.[updated_at_utc]
                ELSE i.[updated_at_utc] END AS [updated_at_utc],
           CASE WHEN s.[row_version] > i.[row_version] THEN s.[row_version]
                ELSE i.[row_version] END AS [row_version]
      FROM [qc_requests].[vat_sampling_interval] AS i
      LEFT JOIN [qc_requests].[sampling_switch] AS s ON s.[id] = 1;
GO

/* ---- write ----------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_sampling_switch_set]
    @on        BIT,
    @start_now BIT = 0
AS
BEGIN
    SET NOCOUNT ON;

    IF @on IS NULL
        THROW 50000, N'On or off is needed.', 1;

    DECLARE @was BIT = (SELECT [is_on] FROM [qc_requests].[sampling_switch]
                         WHERE [id] = 1);

    IF @was = @on
    BEGIN
        /* Already there. Said rather than silently re-anchoring: switching on
           twice must not push the next sample a further interval away. */
        SELECT 0 AS [changed];
        RETURN;
    END

    BEGIN TRANSACTION;

    /* Only on the way on. Switching off leaves the anchors where they are;
       the next switch-on sets them again, and until then nothing reads them. */
    IF @on = 1
    BEGIN
        /* The semicolon is the CTE's, not a stray: a WITH has to be the first
           thing in its statement, and the IF above is a statement. */
        ;WITH [newest] AS (
            SELECT [vat], [position], [electrode]
              FROM (SELECT [vat], [position], [electrode],
                           ROW_NUMBER() OVER (PARTITION BY [vat]
                                              ORDER BY [position] DESC) AS [rn]
                      FROM [qc_requests].[vw_sampling_position]) AS [ordered]
             WHERE [rn] = 1)
        UPDATE i
           /* 0 for a vat that has made nothing yet, so that its first
              electrode is position 1 and "the very next one" still means
              something there. */
           SET i.[count_from_position]  = COALESCE(n.[position], 0),
               i.[count_from_electrode] = n.[electrode],
               i.[due_next]             = @start_now,
               i.[updated_at_utc]       = SYSUTCDATETIME()
          FROM [qc_requests].[vat_sampling_interval] AS i
          LEFT JOIN [newest] AS n ON n.[vat] = i.[vat];
    END

    UPDATE [qc_requests].[sampling_switch]
       SET [is_on] = @on, [updated_at_utc] = SYSUTCDATETIME()
     WHERE [id] = 1;

    COMMIT TRANSACTION;

    SELECT 1 AS [changed];
END;
GO

/* ---- grants ---------------------------------------------------------------- */
/* By name, as everything in this schema is. The switch is QC's to throw, so
   this application writes it and Production Data Manager reads it. */
GRANT SELECT  ON OBJECT::[qc_requests].[vw_sampling_switch]        TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_sampling_switch_set]   TO [qc_data_manager_role];
GO

IF EXISTS (SELECT 1 FROM sys.database_principals
            WHERE name = N'db_production_data_manager_user')
    GRANT SELECT ON OBJECT::[qc_requests].[vw_sampling_switch]
        TO [db_production_data_manager_user];
ELSE
    PRINT 'db_production_data_manager_user does not exist here, grant skipped. Re-run this file once it does.';
GO

/* ---- what it looks like now ------------------------------------------------ */
SELECT [is_on] AS [sampling_on],
       CONVERT(NVARCHAR(30), [updated_at_utc], 120) AS [since]
  FROM [qc_requests].[sampling_switch];

SELECT [vat], [sampling_interval], [sampling_on], [remaining]
  FROM [qc_requests].[vw_vat_sampling_status]
 ORDER BY [vat];
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'016_the_sampling_switch.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'016_the_sampling_switch.sql');
GO
