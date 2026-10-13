/* 005_fix_caller_visibility.sql
   ---------------------------------------------------------------------------
   Three corrections, all found by wiring Production Data Manager to 004.

     usp_raise_due_requests  stop guarding with OBJECT_ID
     usp_request_open        refuse an electrode that was never made
     every move               @expected_state is no longer optional

   WHY THE GUARD WAS WRONG. 004 began with

       IF OBJECT_ID(N'[dbo].[MetaData]') IS NULL ... RETURN

   to say something useful on a database where the electrode list is somewhere
   else. SQL Server hides metadata for objects the caller has no permission on,
   and that hiding applies to OBJECT_ID, INFORMATION_SCHEMA and the catalog
   views alike. Production Data Manager holds no rights at all under dbo, so
   OBJECT_ID returned NULL for a table that was right there, and the procedure
   reported "no electrode list" and raised nothing. Every minute, successfully,
   for ever.

   OWNERSHIP CHAINING COVERS THE SELECT, which is the whole point of granting
   EXECUTE and no table rights, but it does not cover a metadata function. So
   the guard asked a question the caller was not allowed to answer, while the
   work it was guarding would have succeeded.

   The guard is dropped rather than rewritten. A missing electrode list is a
   deployment fault, not a state the application should cope with: 004 already
   refuses to create fn_vat_of without dbo.ProcessData, so for the table to be
   gone at this point somebody has removed it afterwards. Failing loudly is the
   honest answer to that, and a note nobody reads is not.

   EXECUTE AS OWNER would have made OBJECT_ID work for any caller and was the
   obvious alternative. It is not worth it: it runs the whole body as dbo to
   answer one question, and the next person to edit this procedure inherits
   those rights without noticing.

   The [note] column stays, always NULL, so the result shape does not change
   under a caller already reading it.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[usp_raise_due_requests]', N'P') IS NULL
    THROW 50000, N'Run qc_requests 004 first (creates the procedures).', 1;
GO

/* ---- an electrode nobody made is not a request ----------------------------- */
/* THE MARKING IS THE PRIMARY KEY AND THERE IS NO WITHDRAWAL, so a typo from
   any route leaves a row that can only ever be declined. Production Data
   Manager offers only from its own list and cannot produce one, but the
   procedure is reachable by other means and should not take it on trust.

   A SELECT, not OBJECT_ID: ownership chaining covers reading dbo.MetaData from
   here, which is exactly the distinction the guard above got wrong. */
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

    IF NOT EXISTS (SELECT 1 FROM [dbo].[MetaData]
                    WHERE [Marking] = @electrode)
    BEGIN
        /* Not an error: the caller asked about something that is not an
           electrode, and nothing happened. The same answer as asking twice. */
        SELECT 0 AS [changed];
        RETURN;
    END

    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat])
    SELECT @electrode, @kind, @state, [qc_requests].[fn_vat_of](@electrode)
     WHERE NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request]
                        WHERE [electrode] = @electrode);

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- the state the caller believes, now required --------------------------- */
/* It defaulted to NULL and NULL meant "no guard", so a caller that forgot the
   parameter lost its concurrency protection with nothing to show for it. Both
   applications pass it today, so making it required costs nothing and removes
   a way to be wrong silently. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_move]
    @electrode      NVARCHAR(32),
    @from_state     NVARCHAR(24),
    @to_state       NVARCHAR(24),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;
    IF @expected_state IS NULL
        THROW 50000, N'@expected_state is required: pass the state the caller believes the request is in.', 1;

    /* A caller that names a state other than the one this move starts from is
       asking for something this move cannot do, whatever the row says. */
    IF @expected_state <> @from_state
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

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_qc_approve]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_qc', N'to_be_sent', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_qc_decline]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_qc', N'declined', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_production_approve]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_production', N'to_be_sent', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_production_decline]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'awaiting_production', N'declined', @expected_state;
END;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_mark_sent]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_move]
         @electrode, N'to_be_sent', N'sent', @expected_state;
END;
GO

/* ---- the sampling rule, without the guard that could not see --------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

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

    /* [note] kept, and always NULL, so a caller already reading the second
       column does not have to change to keep working. */
    SELECT @@ROWCOUNT AS [raised], CONVERT(NVARCHAR(200), NULL) AS [note];
END;
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'005_fix_caller_visibility.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'005_fix_caller_visibility.sql');
GO
