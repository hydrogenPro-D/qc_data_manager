/* 004_create_request_procedures.sql
   ---------------------------------------------------------------------------
   The contract itself: one view to read, procedures to write.

     vw_qc_request                   every request and where it has got to
     usp_request_raise               QC asks production for an electrode
     usp_request_offer               a technician offers one to QC
     usp_request_production_approve  production says it can be sent
     usp_request_production_decline  production says it cannot
     usp_request_qc_approve          QC says it wants it
     usp_request_qc_decline          QC says it does not
     usp_request_mark_sent           the technician has sent it
     usp_raise_due_requests          the sampling rule, run by whoever is open

   EACH SIDE GETS ONLY ITS OWN. Whoever did not raise a request is the one who
   answers it, and the grants at the bottom are what enforce that rather than
   an agreement between two codebases. Production Data Manager cannot call QC's
   answers and this application cannot call production's.

   EVERY WRITE SAYS WHAT IT BELIEVES. @expected_state is the state the caller
   had on screen, and a write that does not match it changes nothing and comes
   back with changed = 0. Two applications edit these rows, so a decision can
   land while somebody is looking at the row it belongs to; what matters is
   that the caller is told, instead of the write quietly doing nothing and the
   screen looking broken.

   ONE SHARED MOVER, usp_request_move, granted to nobody. The six public moves
   are the same UPDATE with different states, and writing it six times is six
   places for the guard to drift. Ownership chaining lets a granted procedure
   call it; a caller cannot.

   GO SEPARATED rather than one batch of EXEC(N'...'). A file this size spent
   in a quoted string is a file where every apostrophe is a bug waiting to
   happen, and the only reason for the single batch convention is that CREATE
   VIEW and CREATE PROCEDURE each insist on starting one.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 003 first (creates the request tables).', 1;

/* Unlike a procedure, a function is not created against a table that is not
   there, and fn_vat_of reads the process log. */
IF OBJECT_ID(N'[dbo].[ProcessData]') IS NULL
    THROW 50000, N'No dbo.ProcessData in this database, so the rule cannot tell which vat an electrode came from. Load it with data/ETL/05 and 06 first.', 1;
GO

/* ---- read ------------------------------------------------------------------ */
CREATE OR ALTER VIEW [qc_requests].[vw_qc_request] AS
    SELECT r.[electrode],
           r.[kind],
           r.[state],
           s.[is_final],
           r.[vat],
           r.[created_at_utc],
           r.[updated_at_utc],
           r.[row_version]
      FROM [qc_requests].[qc_request] AS r
      JOIN [qc_requests].[qc_request_state] AS s ON s.[state] = r.[state];
GO

/* ---- the vat an electrode was plated in ------------------------------------ */
/* THE SAME ANSWER THE APPLICATION SHOWS. Only the production line numbers its
   plating vats 28, 29 and 30, so an A marking has none; and the process log
   records the plan, so a handful of electrodes list two. The application reads
   those as having no single vat and takes them out of every rule that counts
   along one. This does the same, through HAVING COUNT(DISTINCT) = 1, rather
   than picking one of the two and quietly disagreeing with the screen. */
CREATE OR ALTER FUNCTION [qc_requests].[fn_vat_of] (@electrode NVARCHAR(32))
RETURNS NVARCHAR(32)
AS
BEGIN
    RETURN (SELECT CONVERT(NVARCHAR(32), MIN(p.[Proces]))
              FROM [dbo].[ProcessData] AS p
             WHERE p.[Marking] = @electrode
               AND p.[Proces] IN (28, 29, 30)
            HAVING COUNT(DISTINCT p.[Proces]) = 1);
END;
GO

/* ---- open one -------------------------------------------------------------- */
/* ONE ROW PER ELECTRODE, EVER. Declined is final and sent cannot be asked
   again, so an electrode that already has a request is refused here rather
   than gaining a second one. The caller is told through changed = 0. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_open]
    @electrode NVARCHAR(32),
    @kind      NVARCHAR(16),
    @state     NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat])
    SELECT @electrode, @kind, @state, [qc_requests].[fn_vat_of](@electrode)
     WHERE NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request]
                        WHERE [electrode] = @electrode);

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- move one -------------------------------------------------------------- */
/* Granted to nobody. The guard is the WHERE: the row has to be the one the
   caller is looking at AND in the state this move starts from, so a decision
   that landed first simply matches nothing. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_move]
    @electrode      NVARCHAR(32),
    @from_state     NVARCHAR(24),
    @to_state       NVARCHAR(24),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    /* A caller that names a state other than the one this move starts from is
       asking for something this move cannot do, whatever the row says. */
    IF @expected_state IS NOT NULL AND @expected_state <> @from_state
    BEGIN
        SELECT 0 AS [changed];
        RETURN;
    END

    UPDATE [qc_requests].[qc_request]
       SET [state]          = @to_state,
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state]     = @from_state;

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- what QC may do -------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_raise]
    @electrode NVARCHAR(32),
    @kind      NVARCHAR(16) = N'manual'
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_open] @electrode, @kind, N'awaiting_production';
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_qc_approve]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_qc', N'to_be_sent', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_qc_decline]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_qc', N'declined', @expected_state;
END;
GO

/* ---- what production may do ------------------------------------------------ */
/* A technician offering one is always their own judgement, so the kind is
   fixed here rather than taken from the caller. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_offer]
    @electrode NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_open] @electrode, N'manual', N'awaiting_qc';
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_production_approve]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_production', N'to_be_sent', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_production_decline]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_production', N'declined', @expected_state;
END;
GO

/* Marking it sent is always the technician's action, whoever asked. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_mark_sent]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'to_be_sent', N'sent', @expected_state;
END;
GO

/* ---- the sampling rule ----------------------------------------------------- */
/* SAFE TO CALL OVER AND OVER, by either application, as often as it likes. It
   raises what is due and nothing else, so whoever has the app open is the one
   who keeps the sampling moving and neither has to know about the other. That
   is the whole reason the rule is here rather than in one of them: the
   database is the only thing that is always up, and a rule written twice in
   two languages is a rule that will disagree with itself.

   COUNTED FROM THE LAST ELECTRODE THAT WAS TESTED in that vat. Tested means
   one of three things, and all three count:

     * it is on the QC board now, at any stage, so somebody has it in hand
     * it was finalised with a verdict it earned itself, Tested and Released
       or Tested and Scrapped. A neighbour swept into plain Released or
       Scrapped was never tested and is not an anchor
     * its status was set by hand to one of those two

   The interval is a statement about how much production goes untested between
   samples, so what it counts from has to be a sample, not a request.

   WHICH MEANS A DECLINED REQUEST STOPS THAT VAT until something is tested.
   The due electrode keeps coming out the same, it already has a request, and
   a declined one is final, so nothing new is raised. That is the behaviour
   asked for: a decline is production saying they cannot reach that electrode,
   and somebody proposing or requesting another one by hand is what starts it
   again.

   A VAT WITH NOTHING TESTED EVER IS NOT SAMPLED. No anchor, no count. It
   starts the first time anything in it is tested.

   A VAT WITH NO INTERVAL IS NOT SAMPLED either. There is deliberately no
   default here, so a vat that appears on the line waits for somebody to set
   one. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

    IF OBJECT_ID(N'[dbo].[MetaData]') IS NULL
       OR OBJECT_ID(N'[dbo].[ProcessData]') IS NULL
    BEGIN
        /* Said out loud rather than returning nothing. The electrode list can
           live in another database, and a rule that silently raises nothing
           looks exactly like a rule that is working. */
        SELECT 0 AS [raised],
               N'No electrode list in this database, so nothing could be due.'
                 AS [note];
        RETURN;
    END

    ;WITH [plated] AS (
        SELECT m.[Marking] AS [electrode],
               CONVERT(NVARCHAR(32), MIN(p.[Proces])) AS [vat]
          FROM [dbo].[MetaData] AS m
          JOIN [dbo].[ProcessData] AS p ON p.[Marking] = m.[Marking]
         WHERE m.[Marking] LIKE N'B%'
           AND m.[Full_name] IN (N'CATHODE', N'ANODE', N'MM1', N'CATHODE NEW')
           AND p.[Proces] IN (28, 29, 30)
         GROUP BY m.[Marking]
        HAVING COUNT(DISTINCT p.[Proces]) = 1
    ),
    [ordered] AS (
        SELECT [electrode], [vat],
               ROW_NUMBER() OVER (
                   PARTITION BY [vat]
                   ORDER BY TRY_CONVERT(BIGINT,
                            SUBSTRING(LTRIM(RTRIM([electrode])), 2, 20))) AS [rn]
          FROM [plated]
         WHERE TRY_CONVERT(BIGINT,
               SUBSTRING(LTRIM(RTRIM([electrode])), 2, 20)) IS NOT NULL
    ),
    /* Everything that counts as having been tested, from the three places a
       test can be recorded. UNION, not UNION ALL: an electrode can easily be
       in two of them and it is the markings that are wanted, not a count. */
    [tested] AS (
        SELECT [electrode] FROM [qc_data].[qc_board_entry]
        UNION
        SELECT [electrode] FROM [qc_data].[qc_board_archive]
         WHERE [status] IN (N'Tested and Released', N'Tested and Scrapped')
        UNION
        SELECT [electrode] FROM [qc_data].[electrode_status]
         WHERE [status] IN (N'Tested and Released', N'Tested and Scrapped')
    ),
    [anchor] AS (
        SELECT o.[vat], MAX(o.[rn]) AS [rn]
          FROM [ordered] AS o
          JOIN [tested] AS t ON t.[electrode] = o.[electrode]
         GROUP BY o.[vat]
    )
    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat])
    SELECT o.[electrode], N'interval', N'awaiting_production', o.[vat]
      FROM [ordered] AS o
      JOIN [anchor] AS a ON a.[vat] = o.[vat]
      JOIN [qc_requests].[vat_sampling_interval] AS i ON i.[vat] = o.[vat]
     WHERE o.[rn] > a.[rn]
       AND (o.[rn] - a.[rn]) % i.[electrodes_between] = 0
       AND NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request] AS r
                        WHERE r.[electrode] = o.[electrode]);

    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

/* ---- grants ---------------------------------------------------------------- */
/* NAMED OBJECTS, NEVER THE SCHEMA. A grant on the schema would hand each
   application whatever is added here next without anybody deciding to, and
   would hand each of them the other's half of the workflow. usp_request_open
   and usp_request_move are granted to nobody: they are reached only through
   the procedures above, which is what pins each side to its own moves. */

GRANT SELECT  ON OBJECT::[qc_requests].[vw_qc_request]                 TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_raise]             TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_qc_approve]        TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_qc_decline]        TO [qc_data_manager_role];
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_raise_due_requests]        TO [qc_data_manager_role];
GO

/* Conditional, like the dbo grants in 002 and 014: a developer database with
   no Production Data Manager user still migrates, and says what it skipped. */
IF DATABASE_PRINCIPAL_ID(N'db_production_data_manager_user') IS NOT NULL
BEGIN
    GRANT SELECT  ON OBJECT::[qc_requests].[vw_qc_request]                   TO [db_production_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_offer]               TO [db_production_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_production_approve]  TO [db_production_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_production_decline]  TO [db_production_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_mark_sent]           TO [db_production_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_raise_due_requests]          TO [db_production_data_manager_user];
END
ELSE
    PRINT N'No db_production_data_manager_user here, so its grants were not issued. Production Data Manager cannot reach qc_requests on this database until they are.';
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'004_create_request_procedures.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'004_create_request_procedures.sql');
GO
