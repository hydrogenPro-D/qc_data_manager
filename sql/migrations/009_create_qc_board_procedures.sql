/* 009_create_qc_board_procedures.sql
   ---------------------------------------------------------------------------
   Every write the QC board makes. The application has SELECT and EXECUTE only
   (see 002), so these procedures are the whole of what it can change.

     usp_qc_board_add          put an electrode on the board
     usp_qc_board_move         move one to another stage
     usp_qc_board_set_findings record cracked / IC result against one
     usp_qc_board_remove       take one off the board
     usp_qc_board_restore      put one back exactly as it was (undo)
     usp_qc_board_archive      freeze verdicts and clear what produced them
     usp_qc_board_unarchive    reverse one archive (undo)

   WHAT THESE DO NOT DECIDE. Which electrodes a verdict sweeps onto is worked
   out by src/data_access/electrodes/status_propagation.py, and usp_qc_board_archive
   is handed the finished answer. The rules stay in one place, tested with plain
   data in and data out, rather than being written twice in two languages.

   SETS OF ROWS ARRIVE AS JSON, through OPENJSON. A finalise can freeze several
   hundred electrodes and one round trip per electrode would be visible on
   screen. JSON rather than a table valued parameter because a TVP needs a type
   the caller has rights to, and the caller has almost none.

   All static SQL on purpose: the ownership chain that lets EXECUTE alone stand
   in for INSERT is broken by dynamic SQL.

   Nothing records who made a change. Every operator connects as the same
   contained user, so it would be one constant on every row.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[qc_board_entry]', N'U') IS NULL
    THROW 50000, N'Run 005 first (creates the board tables).', 1;

/* ---- put an electrode on the board ---------------------------------------- */
/* Re-adding one that is already there moves it, which is what the board does
   today: the operator sees one card per electrode either way. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_add]
    @electrode      NVARCHAR(32),
    @electrode_type NVARCHAR(16),
    @stage          NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    IF NULLIF(LTRIM(RTRIM(@electrode)), N'''') IS NULL
        THROW 50000, N''An electrode marking is required.'', 1;

    UPDATE [qc_data].[qc_board_entry]
       SET [stage]          = @stage,
           [electrode_type] = @electrode_type,
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode;

    IF @@ROWCOUNT = 0
        INSERT INTO [qc_data].[qc_board_entry]
               ([electrode], [stage], [electrode_type], [cracked], [ic_result])
        VALUES (@electrode, @stage, @electrode_type, 0, NULL);
END;
');

/* ---- move one to another stage -------------------------------------------- */
/* Returns how many rows it changed, so the application can tell "moved" from
   "it was not on the board", which is what its own move() promises. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_move]
    @electrode NVARCHAR(32),
    @stage     NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [qc_data].[qc_board_entry]
       SET [stage]          = @stage,
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode;

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- record findings ------------------------------------------------------- */
/* Findings, not verdicts: neither of these moves the electrode or decides its
   status. The empty string the application uses for "IC not run" becomes NULL
   here, so the column means one thing only. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_set_findings]
    @electrode NVARCHAR(32),
    @cracked   BIT,
    @ic_result NVARCHAR(16) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [qc_data].[qc_board_entry]
       SET [cracked]        = ISNULL(@cracked, 0),
           [ic_result]      = NULLIF(@ic_result, N''''),
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND ([cracked]  <> ISNULL(@cracked, 0)
            OR ISNULL([ic_result], N'''') <> ISNULL(@ic_result, N''''));

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- take one off the board ------------------------------------------------ */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_remove]
    @electrode NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    DELETE FROM [qc_data].[qc_board_entry] WHERE [electrode] = @electrode;

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- put one back exactly as it was ---------------------------------------- */
/* The undo button. A NULL stage means the electrode was not on the board before
   the change, so undoing it removes the card again. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_restore]
    @electrode      NVARCHAR(32),
    @stage          NVARCHAR(32) = NULL,
    @electrode_type NVARCHAR(16) = NULL,
    @cracked        BIT          = 0,
    @ic_result      NVARCHAR(16) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    IF @stage IS NULL
    BEGIN
        DELETE FROM [qc_data].[qc_board_entry] WHERE [electrode] = @electrode;
        RETURN;
    END;

    UPDATE [qc_data].[qc_board_entry]
       SET [stage]          = @stage,
           [electrode_type] = ISNULL(@electrode_type, [electrode_type]),
           [cracked]        = ISNULL(@cracked, 0),
           [ic_result]      = NULLIF(@ic_result, N''''),
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode;

    IF @@ROWCOUNT = 0
        INSERT INTO [qc_data].[qc_board_entry]
               ([electrode], [stage], [electrode_type], [cracked], [ic_result])
        VALUES (@electrode, @stage, ISNULL(@electrode_type, N''CATHODE''),
                ISNULL(@cracked, 0), NULLIF(@ic_result, N''''));
END;
');

/* ---- freeze verdicts, clear what produced them ----------------------------- */
/* @frozen is the finished sweep:
       [{"electrode": "B14020", "status": "Tested and Scrapped",
         "caused_by": "B14019", "test_first": 1}, ...]
   @consumed names the decided electrodes to take off the board, or is NULL for
   "every decided one", which is what "mark all as done" means.

   One transaction: a half applied finalise would leave verdicts frozen while
   the cards that justified them still sat on the board. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_archive]
    @frozen   NVARCHAR(MAX),
    @consumed NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF ISJSON(@frozen) <> 1
        THROW 50000, N''@frozen is not valid JSON.'', 1;
    IF @consumed IS NOT NULL AND ISJSON(@consumed) <> 1
        THROW 50000, N''@consumed is not valid JSON.'', 1;

    DECLARE @rows TABLE (
        [electrode]  NVARCHAR(32)  NOT NULL PRIMARY KEY,
        [status]     NVARCHAR(32)  NOT NULL,
        [caused_by]  NVARCHAR(32)  NOT NULL,
        [test_first] BIT           NOT NULL);

    INSERT INTO @rows ([electrode], [status], [caused_by], [test_first])
    SELECT j.[electrode], j.[status],
           ISNULL(j.[caused_by], j.[electrode]), ISNULL(j.[test_first], 0)
      FROM OPENJSON(@frozen) WITH (
               [electrode]  NVARCHAR(32),
               [status]     NVARCHAR(32),
               [caused_by]  NVARCHAR(32),
               [test_first] BIT) AS j
     WHERE NULLIF(LTRIM(RTRIM(j.[electrode])), N'''') IS NOT NULL;

    BEGIN TRANSACTION;

        MERGE [qc_data].[qc_board_archive] WITH (HOLDLOCK) AS t
        USING @rows AS s ON t.[electrode] = s.[electrode]
        WHEN MATCHED THEN UPDATE SET
                 t.[status]          = s.[status],
                 t.[caused_by]       = s.[caused_by],
                 t.[test_first]      = s.[test_first],
                 t.[archived_at_utc] = SYSUTCDATETIME()
        WHEN NOT MATCHED BY TARGET THEN
            INSERT ([electrode], [status], [caused_by], [test_first])
            VALUES (s.[electrode], s.[status], s.[caused_by], s.[test_first]);

        /* Only the electrodes that were actually decided leave the board: a
           neighbour swept by the verdict keeps its card. */
        DELETE e
          FROM [qc_data].[qc_board_entry] AS e
          JOIN [qc_data].[qc_board_stage] AS g ON g.[stage] = e.[stage]
         WHERE g.[status] IS NOT NULL
           AND (@consumed IS NULL
                OR e.[electrode] IN (SELECT [value] FROM OPENJSON(@consumed)));

    COMMIT TRANSACTION;

    SELECT (SELECT COUNT(*) FROM @rows) AS [frozen];
END;
');

/* ---- reverse one archive --------------------------------------------------- */
/* @markings are the frozen rows to drop again, @board the cards to put back,
   both as JSON. The pair is what one undo step of "mark all as done" is. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_unarchive]
    @markings NVARCHAR(MAX),
    @board    NVARCHAR(MAX) = NULL
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF ISJSON(@markings) <> 1
        THROW 50000, N''@markings is not valid JSON.'', 1;
    IF @board IS NOT NULL AND ISJSON(@board) <> 1
        THROW 50000, N''@board is not valid JSON.'', 1;

    BEGIN TRANSACTION;

        DELETE FROM [qc_data].[qc_board_archive]
         WHERE [electrode] IN (SELECT [value] FROM OPENJSON(@markings));

        IF @board IS NOT NULL
        BEGIN
            MERGE [qc_data].[qc_board_entry] WITH (HOLDLOCK) AS t
            USING (SELECT j.[electrode], j.[stage], j.[electrode_type],
                          ISNULL(j.[cracked], 0) AS [cracked],
                          NULLIF(j.[ic_result], N'''') AS [ic_result]
                     FROM OPENJSON(@board) WITH (
                              [electrode]      NVARCHAR(32),
                              [stage]          NVARCHAR(32),
                              [electrode_type] NVARCHAR(16),
                              [cracked]        BIT,
                              [ic_result]      NVARCHAR(16)) AS j
                    WHERE j.[electrode] IS NOT NULL) AS s
               ON t.[electrode] = s.[electrode]
            WHEN MATCHED THEN UPDATE SET
                     t.[stage]          = s.[stage],
                     t.[electrode_type] = s.[electrode_type],
                     t.[cracked]        = s.[cracked],
                     t.[ic_result]      = s.[ic_result],
                     t.[updated_at_utc] = SYSUTCDATETIME()
            WHEN NOT MATCHED BY TARGET THEN
                INSERT ([electrode], [stage], [electrode_type], [cracked],
                        [ic_result])
                VALUES (s.[electrode], s.[stage], s.[electrode_type],
                        s.[cracked], s.[ic_result]);
        END;

    COMMIT TRANSACTION;
END;
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'009_create_qc_board_procedures.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'009_create_qc_board_procedures.sql');
GO
