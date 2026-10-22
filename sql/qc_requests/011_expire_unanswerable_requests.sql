/* 011_expire_unanswerable_requests.sql
   ---------------------------------------------------------------------------
   Requests for electrodes production can no longer be shown. Answers
   QC_REQUEST_EXPIRY.md.

     expired                            a sixth state, terminal
     usp_expire_unanswerable_requests   the rule that sets it
     usp_raise_due_requests             now runs that rule first

   WHAT THEY ARE. Production Data Manager lists the last 30 days of finished
   electrodes, which is the list a technician scans during a shift. A request
   naming an electrode older than that has no row to appear on, so there is no
   button to press and no way to answer it. In this application it sits in
   Requested for ever. 57 of them on the test database, every one created by
   the interval bug that 007 fixed.

   TERMINAL, LIKE declined AND sent. [is_final] already tells the live states
   from the closed ones, so nothing that reads this schema has to learn a sixth
   name: a row that is final never moves again, and that is as true of this one
   as of the other two.

   NOT A DELETION. The row stays, with its dates, so "what happened to B13739"
   has an answer. And not a re-raise: if QC still wants an old electrode that
   is a fresh request with a fresh date.

   IN SQL, NOT IN EITHER APPLICATION. A rule that runs only while somebody has
   a window open is a rule that stops at the end of the shift. This one hangs
   off usp_raise_due_requests, which both applications already call every 60
   seconds, so it runs whenever either of them is running and neither had to
   change to get it.

   ---- the two conditions, and where they differ from the specification ----

   1. THE ELECTRODE WAS MADE MORE THAN @window_days AGO. PDM's window, so PDM's
      number, and it is a parameter rather than a literal for that reason.
      THE SAME NUMBER IS IN config/config.json, as [request_window_days], where
      the Electrodes tab reads it to decide which rows still get a Request for
      QC button. The two have to move together: a button that raises a request
      this rule expires within the minute is worse than no button.
      Applied to [interval] and [fault] only, NOT to [manual]. A manual request
      is somebody deciding on purpose that they want this one, and expiring it
      inside the minute would make the button useless for exactly the
      electrodes it is most needed for. A manual request for an old electrode
      still expires, through the second condition, after a fortnight.

   2. IT HAS NOT MOVED IN @stale_days. The backstop, and the only thing that
      catches a request whose electrode is not in [dbo].[MetaData] at all,
      where the first condition has no date to judge by and deliberately does
      nothing. Measured from [updated_at_utc], which is when the row entered
      the state it is in, so it reads as "nobody has touched this in a
      fortnight" whichever state that is.

   BOTH STATES WHERE PRODUCTION HAS THE MOVE, not only awaiting_production.
   3 of the 57 are in to_be_sent: a technician agreed to send one and then the
   electrode left the list, so it can no longer be marked sent either. Waiting
   for it is exactly the thing this is meant to stop. awaiting_qc is NOT
   expired: that one is QC's to answer and QC can answer it from here whatever
   the window says.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request_state]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 003 first (creates the request tables).', 1;

/* ---- the state ------------------------------------------------------------- */
/* The same MERGE 003 uses, so running either file again is harmless. */
MERGE [qc_requests].[qc_request_state] AS target
USING (VALUES (N'expired', 1)) AS source ([state], [is_final])
   ON target.[state] = source.[state]
WHEN MATCHED AND target.[is_final] <> source.[is_final]
    THEN UPDATE SET [is_final] = source.[is_final]
WHEN NOT MATCHED
    THEN INSERT ([state], [is_final]) VALUES (source.[state], source.[is_final]);
GO

/* ---- the rule -------------------------------------------------------------- */
/* NO RESULT SET, a count through an OUTPUT parameter instead. It is called
   from inside usp_raise_due_requests, and a SELECT here would arrive at that
   procedure's callers as a first result set in front of the one they read. By
   hand:

     DECLARE @n INT;
     EXEC [qc_requests].[usp_expire_unanswerable_requests] @expired = @n OUTPUT;
     SELECT @n;
*/
CREATE OR ALTER PROCEDURE [qc_requests].[usp_expire_unanswerable_requests]
    @window_days INT = 30,
    @stale_days  INT = 14,
    @expired     INT = NULL OUTPUT
AS
BEGIN
    SET NOCOUNT ON;

    IF @window_days IS NULL OR @window_days < 1
        THROW 50000, N'@window_days has to be at least 1.', 1;
    IF @stale_days IS NULL OR @stale_days < 1
        THROW 50000, N'@stale_days has to be at least 1.', 1;

    /* [Date] is a DATETIMEOFFSET, so it is compared against one. Taking
       SYSUTCDATETIME() here would compare an offset-aware value with a naive
       one and the answer would quietly follow the server's own offset. */
    DECLARE @made_before DATETIMEOFFSET = DATEADD(DAY, -@window_days,
                                                  SYSDATETIMEOFFSET());
    DECLARE @idle_before DATETIME2      = DATEADD(DAY, -@stale_days,
                                                  SYSUTCDATETIME());

    UPDATE r
       SET r.[state]          = N'expired',
           r.[updated_at_utc] = SYSUTCDATETIME()
      FROM [qc_requests].[qc_request] AS r
      LEFT JOIN [dbo].[MetaData] AS m ON m.[Marking] = r.[electrode]
     WHERE r.[state] IN (N'awaiting_production', N'to_be_sent')
       AND (
             /* Out of the window, and raised by a rule rather than by hand. */
             (r.[kind] IN (N'interval', N'fault')
              AND m.[Date] IS NOT NULL
              AND m.[Date] < @made_before)
             /* Or nobody has touched it in a fortnight. */
          OR r.[updated_at_utc] < @idle_before
           );

    SET @expired = @@ROWCOUNT;
END;
GO

/* ---- the sweep that runs it ------------------------------------------------ */
/* 008's procedure, unchanged below the first statement. The expiry goes first
   so that a request raised by this same call cannot be expired by it: the two
   never touch the same row, but reading it in that order says so. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @gone INT;
    EXEC [qc_requests].[usp_expire_unanswerable_requests] @expired = @gone OUTPUT;

    DECLARE @coupons NVARCHAR(16) =
        (SELECT [coupons] FROM [qc_requests].[coupon_setting]
          WHERE [reason] = N'interval');

    /* Automatic sampling turned off. Nothing to raise and nothing to explain:
       a setting of [none] is QC saying it does not want these. */
    IF @coupons IS NULL OR @coupons = N'none'
    BEGIN
        SELECT 0 AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
        RETURN;
    END

    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat], [coupons])
    SELECT p.[electrode], N'interval', N'awaiting_production', p.[vat], @coupons
      FROM [qc_requests].[vw_sampling_position] AS p
      JOIN [qc_requests].[vw_vat_count_origin] AS o ON o.[vat] = p.[vat]
      JOIN [qc_requests].[vat_sampling_interval] AS i ON i.[vat] = p.[vat]
     WHERE p.[position] = o.[position] + i.[electrodes_between]
       AND NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request] AS r
                        WHERE r.[electrode] = p.[electrode]);

    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

/* ---- grants ---------------------------------------------------------------- */
/* Called from inside the sweep, which ownership chaining covers, so neither
   application needs this to get the behaviour. Granted anyway, to both, so
   that running the rule once on purpose is something either side can do
   without a database administrator. */
IF EXISTS (SELECT 1 FROM sys.database_principals
            WHERE name = N'db_production_data_manager_user')
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_expire_unanswerable_requests]
        TO [db_production_data_manager_user];
ELSE
    PRINT 'db_production_data_manager_user does not exist here, grant skipped.';

GRANT EXECUTE ON OBJECT::[qc_requests].[usp_expire_unanswerable_requests]
    TO [qc_data_manager_role];
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'011_expire_unanswerable_requests.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'011_expire_unanswerable_requests.sql');
GO
