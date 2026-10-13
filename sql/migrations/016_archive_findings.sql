/* 016_archive_findings.sql
   ---------------------------------------------------------------------------
   Keep WHY an electrode failed, not just that it did.

     qc_board_archive.ic_result   carried over from the card when it is frozen
     qc_board_archive.sem_result

   The findings live on the card, and "mark all as done" deletes the card. So
   the moment a failure was finalised, the reason it failed was thrown away:
   the Electrodes tab could say Tested, failed and nothing more, which is the
   one thing somebody looking at a failed electrode wants to know.

   ONLY THE ELECTRODES THAT WERE ON THE BOARD have findings. A neighbour swept
   by somebody else''s verdict was never tested and never observed, so its two
   columns stay NULL, which is a different fact from "tested and nothing
   found".

   NOT BACKFILLED, because there is nothing to backfill from: the cards whose
   findings these were are already gone. Everything frozen before this reads as
   no findings recorded, which is also what an electrode whose operator never
   filled them in reads as, and the tab shows no badge for either.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[qc_board_archive]', N'U') IS NULL
    THROW 50000, N'Run 005 first (creates the board tables).', 1;

/* ---- the columns ----------------------------------------------------------- */
/* NULL means nobody wrote one down. The live card uses an empty string for the
   same idea, but a column added to rows that already exist cannot tell the two
   apart, so the application reads either as "not recorded". */
IF COL_LENGTH(N'[qc_data].[qc_board_archive]', N'ic_result') IS NULL
    ALTER TABLE [qc_data].[qc_board_archive] ADD [ic_result] NVARCHAR(16) NULL;

IF COL_LENGTH(N'[qc_data].[qc_board_archive]', N'sem_result') IS NULL
    ALTER TABLE [qc_data].[qc_board_archive] ADD [sem_result] NVARCHAR(16) NULL;
GO

/* ---- carry them over when a card is frozen --------------------------------- */
/* The card is still there at this point: the DELETE that consumes it comes
   afterwards in the same transaction, so a LEFT JOIN to it is what copies the
   findings across. */
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
        [test_first] BIT           NOT NULL,
        [ic_result]  NVARCHAR(16)  NULL,
        [sem_result] NVARCHAR(16)  NULL);

    INSERT INTO @rows ([electrode], [status], [caused_by], [test_first],
                       [ic_result], [sem_result])
    SELECT j.[electrode], j.[status],
           ISNULL(j.[caused_by], j.[electrode]), ISNULL(j.[test_first], 0),
           NULLIF(e.[ic_result], N''''), NULLIF(e.[sem_result], N'''')
      FROM OPENJSON(@frozen) WITH (
               [electrode]  NVARCHAR(32),
               [status]     NVARCHAR(32),
               [caused_by]  NVARCHAR(32),
               [test_first] BIT) AS j
      LEFT JOIN [qc_data].[qc_board_entry] AS e
             ON e.[electrode] = j.[electrode]
     WHERE NULLIF(LTRIM(RTRIM(j.[electrode])), N'''') IS NOT NULL;

    BEGIN TRANSACTION;

        MERGE [qc_data].[qc_board_archive] WITH (HOLDLOCK) AS t
        USING @rows AS s ON t.[electrode] = s.[electrode]
        WHEN MATCHED THEN UPDATE SET
                 t.[status]          = s.[status],
                 t.[caused_by]       = s.[caused_by],
                 t.[test_first]      = s.[test_first],
                 /* Kept where this freeze has nothing to say, so re-freezing a
                    row that is already there cannot erase what was observed. */
                 t.[ic_result]       = ISNULL(s.[ic_result], t.[ic_result]),
                 t.[sem_result]      = ISNULL(s.[sem_result], t.[sem_result]),
                 t.[archived_at_utc] = SYSUTCDATETIME()
        WHEN NOT MATCHED BY TARGET THEN
            INSERT ([electrode], [status], [caused_by], [test_first],
                    [ic_result], [sem_result])
            VALUES (s.[electrode], s.[status], s.[caused_by], s.[test_first],
                    s.[ic_result], s.[sem_result]);

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

/* ---- and hand them out ------------------------------------------------------ */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_qc_board_archive] AS
    SELECT [electrode],
           [status],
           [caused_by],
           [test_first],
           [ic_result],
           [sem_result],
           [archived_at_utc]
      FROM [qc_data].[qc_board_archive];
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'016_archive_findings.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'016_archive_findings.sql');
GO
