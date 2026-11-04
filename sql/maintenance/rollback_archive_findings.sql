/* rollback_archive_findings.sql
   ---------------------------------------------------------------------------
   Undo the 121 rows that backfill_archive_findings.sql INSERTED.

   WHY: qc_board_archive is not just a record, it is the set of decisions the
   propagation sweeps from. db_manager._settled_verdicts hands every archived
   verdict to propagate_detailed as a decision, and each scrapped decision
   condemns its margin and opens a run of Needs testing beyond it. Those 121
   failures had only ever been a base status, so they had never swept. Giving
   them an archive row turned 121 year-old failures into 121 live
   decisions at once, which is where the Needs testing rows and the 808
   Waiting for additional SEM approval came from.

   The 64 rows that were UPDATED are left exactly as they are: they were
   already decisions and already swept, so their findings changed nothing
   about the propagation. This only removes what was invented.

   Identified by archived_at_utc and by marking, both, so it cannot reach a row
   the board wrote. No genuine archive row predates 27 Aug 2026.
   --------------------------------------------------------------------------- */

SET NOCOUNT ON;
SET XACT_ABORT ON;

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

DECLARE @stamp DATETIME2 = N'2026-08-26T07:50:42.148033';

DECLARE @mine TABLE ([electrode] NVARCHAR(32) NOT NULL PRIMARY KEY);
INSERT INTO @mine ([electrode])
VALUES
    (N'B11541'),
    (N'B11544'),
    (N'B11851'),
    (N'B11854'),
    (N'B11857'),
    (N'B13707'),
    (N'B14092'),
    (N'B14095'),
    (N'B14098'),
    (N'B14116'),
    (N'B14137'),
    (N'B14140'),
    (N'B14141'),
    (N'B14144'),
    (N'B14151'),
    (N'B14154'),
    (N'B14165'),
    (N'B14168'),
    (N'B14172'),
    (N'B14174'),
    (N'B14175'),
    (N'B14182'),
    (N'B14234'),
    (N'B14237'),
    (N'B14352'),
    (N'B14358'),
    (N'B14381'),
    (N'B14421'),
    (N'B14525'),
    (N'B14542'),
    (N'B14548'),
    (N'B14560'),
    (N'B14566'),
    (N'B3095'),
    (N'B3135'),
    (N'B3233'),
    (N'B3234'),
    (N'B3248'),
    (N'B3259'),
    (N'B3282'),
    (N'B3313'),
    (N'B3591'),
    (N'B3771'),
    (N'B3796'),
    (N'B3810'),
    (N'B3834'),
    (N'B3908'),
    (N'B4008'),
    (N'B4061'),
    (N'B4076'),
    (N'B4089'),
    (N'B4090'),
    (N'B4091'),
    (N'B4140'),
    (N'B4141'),
    (N'B4147'),
    (N'B4148'),
    (N'B4149'),
    (N'B4156'),
    (N'B4157'),
    (N'B4181'),
    (N'B4253'),
    (N'B4319'),
    (N'B4343'),
    (N'B4396'),
    (N'B4420'),
    (N'B4452'),
    (N'B4508'),
    (N'B4598'),
    (N'B4599'),
    (N'B4656'),
    (N'B4731'),
    (N'B4889'),
    (N'B4918'),
    (N'B5006'),
    (N'B5174'),
    (N'B5180'),
    (N'B5320'),
    (N'B5366'),
    (N'B5376'),
    (N'B5573'),
    (N'B5816'),
    (N'B5832'),
    (N'B5892'),
    (N'B5965'),
    (N'B6137'),
    (N'B6195'),
    (N'B6207'),
    (N'B6233'),
    (N'B6356'),
    (N'B6381'),
    (N'B6475'),
    (N'B6490'),
    (N'B6498'),
    (N'B6601'),
    (N'B6703'),
    (N'B7088'),
    (N'B7208'),
    (N'B7462'),
    (N'B7588'),
    (N'B7605'),
    (N'B7611'),
    (N'B7623'),
    (N'B7651'),
    (N'B7902'),
    (N'B7962'),
    (N'B7967'),
    (N'B7970'),
    (N'B7993'),
    (N'B8351'),
    (N'B8364'),
    (N'B8469'),
    (N'B8477'),
    (N'B8512'),
    (N'B8520'),
    (N'B8869'),
    (N'B8913'),
    (N'B8933'),
    (N'B9349'),
    (N'B9566'),
    (N'B9758');

SELECT COUNT(*) AS [before]
  FROM [qc_data].[qc_board_archive]
 WHERE [archived_at_utc] = @stamp;

BEGIN TRANSACTION;

    DELETE a
      FROM [qc_data].[qc_board_archive] AS a
      JOIN @mine AS m ON m.[electrode] = a.[electrode]
     WHERE a.[archived_at_utc] = @stamp;

COMMIT TRANSACTION;

SELECT COUNT(*) AS [left_behind]
  FROM [qc_data].[qc_board_archive]
 WHERE [archived_at_utc] = @stamp;
