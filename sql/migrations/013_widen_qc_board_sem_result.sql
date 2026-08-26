/* 013_widen_qc_board_sem_result.sql
   ---------------------------------------------------------------------------
   The crack tick box becomes the SEM result, with three values.

     qc_board_entry.cracked BIT  ->  qc_board_entry.sem_result NVARCHAR(16)
     vw_qc_board                     carries it out
     usp_qc_board_add / _set_findings / _restore / _unarchive   take it in

   IT IS ONE FINDING, NOT TWO. Looking for cracks is what SEM does on this
   board, so the tick box was already the SEM result, spelled as a bit and
   named after only one of its outcomes.

   WHY A BIT COULD NOT STAY. With 0 and 1, "nobody has run SEM yet" and "SEM
   ran, no crack" were the same value. Those are different facts and the board
   reported the first as the second. NULL for not tested, 'cracked' or
   'accepted' once it has run, which is the shape [ic_result] already had.

   A FINDING, NOT A VERDICT. Like the IC result, this changes no status on its
   own: the verdict is the column the card sits in, and freezing it is still
   "mark all as done". The propagation rules never read it.

   THE FOUR PROCEDURES ARE REPEATED HERE IN FULL rather than patched, since a
   procedure has no ALTER COLUMN. They are 009's, with the bit swapped out.
   009 stays as it was: it records what that migration did, not today's shape.

   NOT ADDITIVE. An older build of the application writes [cracked] and will
   fail against this. Nothing else reads that column and the board is small,
   so keeping both was not worth it.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[qc_board_entry]', N'U') IS NULL
    THROW 50000, N'Run 005 first (creates the board tables).', 1;

/* ---- the column ------------------------------------------------------------ */
IF COL_LENGTH(N'[qc_data].[qc_board_entry]', N'sem_result') IS NULL
    EXEC (N'
        ALTER TABLE [qc_data].[qc_board_entry]
          ADD [sem_result] NVARCHAR(16) NULL
              CONSTRAINT [CK_qc_board_entry_sem]
              CHECK ([sem_result] IN (N''cracked'', N''accepted''));
    ');

/* A ticked box was a crack somebody saw. An unticked one says nothing about
   whether SEM ran at all, so it stays NULL rather than becoming 'accepted',
   which would invent a result that was never recorded. */
IF COL_LENGTH(N'[qc_data].[qc_board_entry]', N'cracked') IS NOT NULL
    EXEC (N'
        UPDATE [qc_data].[qc_board_entry]
           SET [sem_result] = N''cracked''
         WHERE [cracked] = 1 AND [sem_result] IS NULL;
    ');

/* The view first: a column cannot be dropped while a view still names it. */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_qc_board] AS
    SELECT [electrode],
           [stage],
           [electrode_type],
           [ic_result],
           [sem_result],
           [updated_at_utc]
      FROM [qc_data].[qc_board_entry];
');

IF COL_LENGTH(N'[qc_data].[qc_board_entry]', N'cracked') IS NOT NULL
    EXEC (N'
        ALTER TABLE [qc_data].[qc_board_entry]
          DROP CONSTRAINT [DF_qc_board_entry_cracked];
        ALTER TABLE [qc_data].[qc_board_entry] DROP COLUMN [cracked];
    ');

/* ---- put an electrode on the board ---------------------------------------- */
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
               ([electrode], [stage], [electrode_type])
        VALUES (@electrode, @stage, @electrode_type);
END;
');

/* ---- record what was found ------------------------------------------------- */
/* Both in one statement: an operator writes down what they know in one go, and
   the row only moves when something actually differs. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_set_findings]
    @electrode  NVARCHAR(32),
    @ic_result  NVARCHAR(16) = NULL,
    @sem_result NVARCHAR(16) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [qc_data].[qc_board_entry]
       SET [ic_result]      = NULLIF(@ic_result, N''''),
           [sem_result]     = NULLIF(@sem_result, N''''),
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND (ISNULL([ic_result],  N'''') <> ISNULL(@ic_result,  N'''')
            OR ISNULL([sem_result], N'''') <> ISNULL(@sem_result, N''''));

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- put one back exactly as it was ---------------------------------------- */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_restore]
    @electrode      NVARCHAR(32),
    @stage          NVARCHAR(32) = NULL,
    @electrode_type NVARCHAR(16) = NULL,
    @ic_result      NVARCHAR(16) = NULL,
    @sem_result     NVARCHAR(16) = NULL
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
           [ic_result]      = NULLIF(@ic_result, N''''),
           [sem_result]     = NULLIF(@sem_result, N''''),
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode;

    IF @@ROWCOUNT = 0
        INSERT INTO [qc_data].[qc_board_entry]
               ([electrode], [stage], [electrode_type], [ic_result], [sem_result])
        VALUES (@electrode, @stage, ISNULL(@electrode_type, N''CATHODE''),
                NULLIF(@ic_result, N''''), NULLIF(@sem_result, N''''));
END;
');

/* ---- reverse one archive (undo) -------------------------------------------- */
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
                          NULLIF(j.[ic_result], N'''')  AS [ic_result],
                          NULLIF(j.[sem_result], N'''') AS [sem_result]
                     FROM OPENJSON(@board) WITH (
                              [electrode]      NVARCHAR(32),
                              [stage]          NVARCHAR(32),
                              [electrode_type] NVARCHAR(16),
                              [ic_result]      NVARCHAR(16),
                              [sem_result]     NVARCHAR(16)) AS j
                    WHERE j.[electrode] IS NOT NULL) AS s
               ON t.[electrode] = s.[electrode]
            WHEN MATCHED THEN UPDATE SET
                     t.[stage]          = s.[stage],
                     t.[electrode_type] = s.[electrode_type],
                     t.[ic_result]      = s.[ic_result],
                     t.[sem_result]     = s.[sem_result],
                     t.[updated_at_utc] = SYSUTCDATETIME()
            WHEN NOT MATCHED BY TARGET THEN
                INSERT ([electrode], [stage], [electrode_type], [ic_result],
                        [sem_result])
                VALUES (s.[electrode], s.[stage], s.[electrode_type],
                        s.[ic_result], s.[sem_result]);
        END;

    COMMIT TRANSACTION;
END;
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'013_widen_qc_board_sem_result.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'013_widen_qc_board_sem_result.sql');
GO
