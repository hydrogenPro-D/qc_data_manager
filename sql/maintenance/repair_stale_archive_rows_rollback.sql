/* repair_stale_archive_rows_rollback.sql
   ---------------------------------------------------------------------------
   Puts qc_data.qc_board_archive back exactly as it was before
   repair_stale_archive_rows.sql ran: the placeholders restored and the rows it
   inserted removed.

   GENERATED beside that script, from the same read. Only undoes rows that
   still hold what the repair wrote, so a finalise made since is left alone.
   --------------------------------------------------------------------------- */

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

DECLARE @undo TABLE (
    [electrode] NVARCHAR(32) NOT NULL PRIMARY KEY,
    [was]       NVARCHAR(32) NULL,
    [written]   NVARCHAR(32) NOT NULL);

INSERT INTO @undo ([electrode], [was], [written])
VALUES
    (N'B14972', N'Not Tested', N'Released'),
    (N'B14975', N'Not Tested', N'Released'),
    (N'B14978', N'Not Tested', N'Released'),
    (N'B14983', N'Not Tested', N'Released'),
    (N'B14986', N'Not Tested', N'Released'),
    (N'B14989', N'Not Tested', N'Released'),
    (N'B15044', N'Not Tested', N'Released'),
    (N'B15050', N'Not Tested', N'Released'),
    (N'B15053', N'Not Tested', N'Released'),
    (N'B15056', N'Not Tested', N'Released'),
    (N'B15059', N'Not Tested', N'Released'),
    (N'B15062', N'Not Tested', N'Released'),
    (N'B15065', N'Not Tested', N'Released'),
    (N'B15069', N'Not Tested', N'Released'),
    (N'B15074', N'Not Tested', N'Released'),
    (N'B15077', N'Not Tested', N'Released'),
    (N'B15080', N'Not Tested', N'Released'),
    (N'B15083', N'Not Tested', N'Released'),
    (N'B15088', N'Not Tested', N'Released'),
    (N'B15089', N'Not Tested', N'Released'),
    (N'B15092', N'Not Tested', N'Released'),
    (N'B15095', N'Not Tested', N'Released'),
    (N'B15098', N'Not Tested', N'Released'),
    (N'B15101', N'Not Tested', N'Released'),
    (N'B15104', N'Not Tested', N'Released'),
    (N'B15109', N'Not Tested', N'Released'),
    (N'B15164', NULL, N'Scrapped'),
    (N'B15173', NULL, N'Scrapped'),
    (N'B15328', N'Not Tested', N'Released'),
    (N'B15332', N'Not Tested', N'Released'),
    (N'B15334', N'Not Tested', N'Released'),
    (N'B15335', N'Not Tested', N'Released'),
    (N'B15336', N'Not Tested', N'Released'),
    (N'B15337', N'Not Tested', N'Released'),
    (N'B15338', N'Not Tested', N'Released'),
    (N'B15339', N'Not Tested', N'Released'),
    (N'B15340', N'Not Tested', N'Released'),
    (N'B15341', N'Not Tested', N'Released'),
    (N'B15342', N'Not Tested', N'Released'),
    (N'B15343', N'Not Tested', N'Released'),
    (N'B15344', N'Not Tested', N'Released'),
    (N'B15345', N'Not Tested', N'Released'),
    (N'B15346', N'Not Tested', N'Released'),
    (N'B15347', N'Not Tested', N'Released'),
    (N'B15349', N'Not Tested', N'Released'),
    (N'B15350', N'Not Tested', N'Released'),
    (N'B15351', N'Not Tested', N'Released'),
    (N'B15352', N'Not Tested', N'Released'),
    (N'B15356', N'Not Tested', N'Released'),
    (N'B15357', N'Not Tested', N'Released'),
    (N'B15360', N'Not Tested', N'Released'),
    (N'B15362', N'Not Tested', N'Released'),
    (N'B15364', N'Not Tested', N'Released'),
    (N'B15365', N'Not Tested', N'Released'),
    (N'B15366', N'Not Tested', N'Released'),
    (N'B15369', N'Not Tested', N'Released'),
    (N'B15370', N'Not Tested', N'Released'),
    (N'B15372', N'Not Tested', N'Released'),
    (N'B15373', N'Not Tested', N'Released'),
    (N'B15376', N'Not Tested', N'Released'),
    (N'B15379', N'Not Tested', N'Released'),
    (N'B15382', N'Not Tested', N'Released'),
    (N'B15385', N'Not Tested', N'Released'),
    (N'B15388', N'Not Tested', N'Released'),
    (N'B15392', N'Not Tested', N'Released'),
    (N'B15459', N'Not Tested', N'Released'),
    (N'B15462', N'Not Tested', N'Released'),
    (N'B15502', N'Not Tested', N'Released'),
    (N'B15505', N'Not Tested', N'Released'),
    (N'B15507', N'Not Tested', N'Released'),
    (N'B15510', N'Not Tested', N'Released'),
    (N'B15513', N'Not Tested', N'Released'),
    (N'B15517', N'Not Tested', N'Released'),
    (N'B15523', N'Not Tested', N'Released'),
    (N'B15525', N'Not Tested', N'Released'),
    (N'B16188', N'Needs testing', N'Released'),
    (N'B16191', N'Needs testing', N'Released'),
    (N'B16194', N'Needs testing', N'Released'),
    (N'B16198', N'Needs testing', N'Released'),
    (N'B16201', N'Needs testing', N'Released'),
    (N'B16204', N'Needs testing', N'Released'),
    (N'B16212', N'Needs testing', N'Released'),
    (N'B16213', N'Needs testing', N'Released'),
    (N'B16216', N'Needs testing', N'Released'),
    (N'B16219', N'Needs testing', N'Released'),
    (N'B16222', N'Needs testing', N'Released');

BEGIN TRANSACTION;

    UPDATE a SET a.[status] = u.[was]
      FROM [qc_data].[qc_board_archive] AS a
      JOIN @undo AS u ON u.[electrode] = a.[electrode]
     WHERE u.[was] IS NOT NULL AND a.[status] = u.[written];

    DELETE a
      FROM [qc_data].[qc_board_archive] AS a
      JOIN @undo AS u ON u.[electrode] = a.[electrode]
     WHERE u.[was] IS NULL AND a.[status] = u.[written];

COMMIT TRANSACTION;

SELECT COUNT(*) AS [restored] FROM [qc_data].[qc_board_archive] AS a
  JOIN @undo AS u ON u.[electrode] = a.[electrode]
 WHERE u.[was] IS NOT NULL AND a.[status] = u.[was];
