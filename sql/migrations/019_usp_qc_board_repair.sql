/* 019_usp_qc_board_repair.sql
   ---------------------------------------------------------------------------
   Let the application correct a frozen row that time has overtaken.

     qc_data.usp_qc_board_repair   @rows, the plan, as JSON

   A finalise writes one archive row per electrode in a card''s stretch, as the
   status stood that second. Nothing rewrites it afterwards, so a row frozen as
   Not Tested stays Not Tested even after a later verdict has released the
   stretch it sits in. The Electrodes tab re-runs the sweeps on every render
   and shows the right answer; the table keeps the old one, and every other
   reader of this database, reports and the sibling analyser among them, sees
   the old one. 86 rows were in that state when this was written.

   WHAT IT MAY CHANGE IS DELIBERATELY NARROW:

     * the [status] of a row, and only where that row still reads exactly what
       the caller expected it to. A row a newer finalise has moved on belongs
       to that finalise, which is newer than the plan in hand.
     * never a row whose [caused_by] is the electrode itself. That is a real
       measurement, and no inference drawn from a neighbour overrules one.
     * [caused_by] and [archived_at_utc] are never updated. Correcting the
       provenance as well regroups the frozen stretches and moves the finalised
       dates on screen, some of them to blank.

   It also inserts an electrode a verdict condemned that no row has ever named,
   which is the only way such an electrode becomes visible to anything but the
   app itself.

   SAFE TO REPEAT. The second call matches nothing and writes nothing, which is
   what lets every copy of the application run it at startup.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[qc_board_archive]', N'U') IS NULL
    THROW 50000, N'Run 005 first (creates the board tables).', 1;
GO

EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_qc_board_repair]
    @rows NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF ISJSON(@rows) <> 1
        THROW 50000, N''@rows is not valid JSON.'', 1;

    DECLARE @fix TABLE (
        [electrode]       NVARCHAR(32) NOT NULL PRIMARY KEY,
        /* What the row must still say for us to touch it. NULL means the
           electrode is not expected to have a row at all. */
        [was]             NVARCHAR(32) NULL,
        [status]          NVARCHAR(32) NOT NULL,
        [caused_by]       NVARCHAR(32) NOT NULL,
        [archived_at_utc] DATETIME2    NOT NULL);

    INSERT INTO @fix ([electrode], [was], [status], [caused_by], [archived_at_utc])
    SELECT j.[electrode], j.[was], j.[status],
           ISNULL(j.[caused_by], j.[electrode]),
           /* A plan with no date behind it settles now, which is the worst
              answer available and still better than refusing the row. */
           ISNULL(j.[archived_at_utc], SYSUTCDATETIME())
      FROM OPENJSON(@rows) WITH (
               [electrode]       NVARCHAR(32),
               [was]             NVARCHAR(32),
               [status]          NVARCHAR(32),
               [caused_by]       NVARCHAR(32),
               [archived_at_utc] DATETIME2) AS j
     WHERE NULLIF(LTRIM(RTRIM(j.[electrode])), N'''') IS NOT NULL
       AND NULLIF(LTRIM(RTRIM(j.[status])), N'''')    IS NOT NULL;

    BEGIN TRANSACTION;

        MERGE [qc_data].[qc_board_archive] WITH (HOLDLOCK) AS t
        USING @fix AS s ON t.[electrode] = s.[electrode]
        WHEN MATCHED AND t.[status] = s.[was]
                     AND t.[caused_by] <> t.[electrode]
            THEN UPDATE SET t.[status] = s.[status]
        WHEN NOT MATCHED BY TARGET AND s.[was] IS NULL THEN
            INSERT ([electrode], [status], [caused_by], [test_first],
                    [archived_at_utc])
            VALUES (s.[electrode], s.[status], s.[caused_by], 0,
                    s.[archived_at_utc]);

        DECLARE @repaired INT = @@ROWCOUNT;

    COMMIT TRANSACTION;

    /* What actually moved, so a caller that was handed a stale plan can say so
       rather than assume it worked. */
    SELECT @repaired AS [repaired];
END;
');
GO

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'019_usp_qc_board_repair.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'019_usp_qc_board_repair.sql');
