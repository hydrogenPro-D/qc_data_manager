/* 008_coupon_settings.sql
   ---------------------------------------------------------------------------
   Which coupons a request wants, and the settings that decide it.

     coupon_choice          the three values, as a domain
     coupon_setting         one row per reason a request can be raised for
     qc_request.coupons     what THIS request asks for, stamped when raised
     vw_qc_request.coupons  what Production Data Manager draws

   WHY A SETTING RATHER THAN A RULE IN CODE. QC asks for all five coupons from a
   sampled electrode, the top right one around a SEM failure and nothing at all
   around an IC failure, and those three answers change with what production is
   doing. A release per change is not a sensible way to run it, so the three
   live in a table the QC manager edits.

   NOTHING MEANS NO REQUEST. A reason set to [none] raises nothing at all: an
   electrode production is asked for but cuts no coupons from is a trip for
   nobody. It also marks no neighbours, see the note on the Needs testing zone
   in status_propagation, which is the visible half of the same decision.

   STAMPED WHEN RAISED, NOT DERIVED ON READ. A request that has gone to
   production means what it meant when it was sent: a technician part way
   through cutting one should not have the answer change under them because
   somebody edited a setting. Older rows carry NULL, which reads as not
   recorded.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    THROW 50000, N'Run the qc_requests migrations first (001 to 007).', 1;
GO

/* ---- the three values ------------------------------------------------------ */
/* A table rather than a CHECK, like the kinds and the states beside it: the set
   is readable, and a fourth pattern is one row rather than a constraint rebuild.
   The spellings are the ones Production Data Manager asked for. */
IF OBJECT_ID(N'[qc_requests].[coupon_choice]', N'U') IS NULL
    CREATE TABLE [qc_requests].[coupon_choice] (
        [coupons] NVARCHAR(16) NOT NULL
                  CONSTRAINT [PK_coupon_choice] PRIMARY KEY,
        /* Which beats which when two reasons apply to one electrode. Higher is
           stricter, so the answer is simply the larger of the two. */
        [strictness] INT NOT NULL
    );

MERGE [qc_requests].[coupon_choice] AS target
USING (VALUES (N'none', 0), (N'top_right', 1), (N'all_five', 2))
      AS source ([coupons], [strictness])
   ON target.[coupons] = source.[coupons]
WHEN MATCHED AND target.[strictness] <> source.[strictness]
    THEN UPDATE SET [strictness] = source.[strictness]
WHEN NOT MATCHED
    THEN INSERT ([coupons], [strictness])
         VALUES (source.[coupons], source.[strictness]);
GO

/* ---- one setting per reason ------------------------------------------------ */
/* ONE SET FOR THE WHOLE LINE, not one per vat. What QC wants out of a sampled
   electrode is a property of the test, not of the vat it came from.

   The reasons are not the request kinds: a fault splits into the two tests that
   can find one, and they are set differently, which is the point of this. */
IF OBJECT_ID(N'[qc_requests].[coupon_setting]', N'U') IS NULL
    CREATE TABLE [qc_requests].[coupon_setting] (
        [reason]  NVARCHAR(16) NOT NULL
                  CONSTRAINT [PK_coupon_setting] PRIMARY KEY
                  CONSTRAINT [CK_coupon_setting_reason]
                  CHECK ([reason] IN (N'interval', N'sem', N'ic')),
        [coupons] NVARCHAR(16) NOT NULL
                  CONSTRAINT [FK_coupon_setting_choice]
                  REFERENCES [qc_requests].[coupon_choice]([coupons]),
        [updated_at_utc] DATETIME2 NOT NULL
                  CONSTRAINT [DF_coupon_setting_at] DEFAULT SYSUTCDATETIME(),
        [row_version] ROWVERSION NOT NULL
    );

/* Seeded with what QC does today. WHEN NOT MATCHED only: re-running this must
   not undo a change somebody has since made in the application. */
MERGE [qc_requests].[coupon_setting] AS target
USING (VALUES (N'interval', N'all_five'),
              (N'sem',      N'top_right'),
              (N'ic',       N'none'))
      AS source ([reason], [coupons])
   ON target.[reason] = source.[reason]
WHEN NOT MATCHED
    THEN INSERT ([reason], [coupons]) VALUES (source.[reason], source.[coupons]);
GO

/* ---- what one request asks for --------------------------------------------- */
/* NULL on every row that already exists, which is honest: nothing recorded what
   those asked for, and Production Data Manager reads NULL as not recorded. */
IF COL_LENGTH(N'[qc_requests].[qc_request]', N'coupons') IS NULL
    ALTER TABLE [qc_requests].[qc_request] ADD [coupons] NVARCHAR(16) NULL
        CONSTRAINT [FK_qc_request_coupons]
        REFERENCES [qc_requests].[coupon_choice]([coupons]);
GO

/* ---- read ------------------------------------------------------------------ */
CREATE OR ALTER VIEW [qc_requests].[vw_coupon_setting] AS
    SELECT s.[reason], s.[coupons], c.[strictness],
           s.[updated_at_utc], s.[row_version]
      FROM [qc_requests].[coupon_setting] AS s
      JOIN [qc_requests].[coupon_choice]  AS c ON c.[coupons] = s.[coupons];
GO

/* The contract's column, added to the end so a caller reading by name is
   unaffected and one reading by position still finds what it had. */
CREATE OR ALTER VIEW [qc_requests].[vw_qc_request] AS
    SELECT r.[electrode],
           r.[kind],
           r.[state],
           s.[is_final],
           r.[vat],
           r.[created_at_utc],
           r.[updated_at_utc],
           r.[row_version],
           r.[coupons]
      FROM [qc_requests].[qc_request] AS r
      JOIN [qc_requests].[qc_request_state] AS s ON s.[state] = r.[state];
GO

/* ---- write ----------------------------------------------------------------- */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_set_coupon_setting]
    @reason  NVARCHAR(16),
    @coupons NVARCHAR(16)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM [qc_requests].[coupon_choice]
                    WHERE [coupons] = @coupons)
        THROW 50000, N'Not one of the coupon choices.', 1;

    UPDATE [qc_requests].[coupon_setting]
       SET [coupons] = @coupons, [updated_at_utc] = SYSUTCDATETIME()
     WHERE [reason] = @reason AND [coupons] <> @coupons;

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- raising one, now carrying what it wants ------------------------------- */
/* @coupons defaults to NULL so the offer from Production Data Manager, which
   passes three arguments and knows nothing about coupons, goes on working
   unchanged. An offer is answered by QC, and that is where it gets its value. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_open]
    @electrode NVARCHAR(32),
    @kind      NVARCHAR(16),
    @state     NVARCHAR(24),
    @coupons   NVARCHAR(16) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    /* [none] is not a request. Asking production for an electrode nobody will
       cut a coupon from wastes a trip, so it is refused the same way a repeat
       is: nothing happened, and the caller is told nothing happened. */
    IF @coupons = N'none'
    BEGIN
        SELECT 0 AS [changed];
        RETURN;
    END

    IF NOT EXISTS (SELECT 1 FROM [dbo].[MetaData]
                    WHERE [Marking] = @electrode)
    BEGIN
        SELECT 0 AS [changed];
        RETURN;
    END

    INSERT INTO [qc_requests].[qc_request]
                ([electrode], [kind], [state], [vat], [coupons])
    SELECT @electrode, @kind, @state,
           [qc_requests].[fn_vat_of](@electrode), @coupons
     WHERE NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request]
                        WHERE [electrode] = @electrode);

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- QC asking, which is the only caller that chooses the coupons ---------- */
/* The wrapper this application calls. usp_request_offer is left alone: it is
   Production Data Manager's, it passes no coupons and must go on working. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_raise]
    @electrode NVARCHAR(32),
    @kind      NVARCHAR(16),
    @coupons   NVARCHAR(16) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    EXEC [qc_requests].[usp_request_open]
         @electrode, @kind, N'awaiting_production', @coupons;
END;
GO

/* ---- what QC wants from an electrode it accepts ---------------------------- */
/* A technician's offer arrives with no coupons, because production does not
   decide that. QC says what it wants when it accepts, which is the same moment
   it agrees to the electrode at all. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_set_coupons]
    @electrode NVARCHAR(32),
    @coupons   NVARCHAR(16)
AS
BEGIN
    SET NOCOUNT ON;

    IF NOT EXISTS (SELECT 1 FROM [qc_requests].[coupon_choice]
                    WHERE [coupons] = @coupons)
        THROW 50000, N'Not one of the coupon choices.', 1;

    /* Only while it is still a question. A sent request has been acted on and
       what it asked for is a matter of record. */
    UPDATE [qc_requests].[qc_request]
       SET [coupons] = @coupons, [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state] NOT IN (N'sent', N'declined');

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- the sampling rule, stamping what it asks for -------------------------- */
/* Unchanged except for the coupons: it raises what the interval says is due,
   one per vat per run, counting from the last request. See 007. */
CREATE OR ALTER PROCEDURE [qc_requests].[usp_raise_due_requests]
AS
BEGIN
    SET NOCOUNT ON;

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
/* Production Data Manager reads the coupons through the view it already has, so
   nothing new is granted to it. The settings are QC's and stay QC's. */
IF EXISTS (SELECT 1 FROM sys.database_principals
            WHERE name = N'db_qc_data_manager_user')
BEGIN
    GRANT SELECT  ON OBJECT::[qc_requests].[vw_coupon_setting]
        TO [db_qc_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_set_coupon_setting]
        TO [db_qc_data_manager_user];
    GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_set_coupons]
        TO [db_qc_data_manager_user];
END
ELSE
    PRINT 'db_qc_data_manager_user does not exist here, grants skipped.';
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'008_coupon_settings.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'008_coupon_settings.sql');
GO
