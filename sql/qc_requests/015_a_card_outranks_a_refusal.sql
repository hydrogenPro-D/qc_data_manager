/* 015_a_card_outranks_a_refusal.sql
   ---------------------------------------------------------------------------
   An electrode that arrived is an electrode that arrived, whatever anybody
   said before it did.

     usp_request_arrived_at_qc   now moves a request from ANY state but sent
     a one-time close for the ones that got here anyway

   WHAT WENT WRONG. B15749 was requested, production declined it, and QC got
   hold of the electrode anyway and put it on the board. The request stayed at
   declined, which is final, so both screens went on saying "Request declined"
   about an electrode sitting on a QC rig. The row said production was not
   sending this one. Production had not sent it; it got there regardless, and
   that is the fact worth recording.

   WHY IT WAS EXCLUDED, AND WHY THAT WAS WRONG. 012 to 014 moved only the live
   states and left sent, declined and expired alone, on the principle that a
   terminal state is a decision nothing should quietly undo. That principle is
   right about DECISIONS and wrong about FACTS. Declined and expired are both
   claims about where the electrode is: nobody is sending it, nobody could be
   asked for it. A card is the electrode being here, which settles the question
   those states were guessing at.

   SO THE RULE IS NOW SIMPLY: a card means sent, from wherever the row was.
   The only state left alone is sent itself, which is already right.

   WHAT IT COSTS. The row stops recording that it was ever declined. Nothing
   here keeps a history, only a current state, so there is no column that could
   hold both; [updated_at_utc] still shows when it changed. If somebody later
   needs to know that production refused an electrode QC then tested anyway,
   that is a history table and a bigger change than this.

   PDM IS TOLD. This breaks a guarantee QC_ARRIVAL_MARKS_SENT_CONTRACT.md and
   QC_REQUEST_EXPIRY_CONTRACT.md both gave them, that a final row never moves
   again. Their state guard means no move of theirs can land wrongly because of
   it, but anything that caches a terminal row as settled for ever would go
   stale. Both pages now say so.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'014_a_card_records_its_own_arrival.sql')
    THROW 50000, N'Run qc_requests 014 first.', 1;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_arrived_at_qc]
    @electrode NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    /* Any state but the one this would set. Whoever had the move and whatever
       they said, the card is the electrode being here. No @expected_state, for
       the reason 012 gives: there is no screen here, only a physical fact the
       caller has just recorded. */
    UPDATE [qc_requests].[qc_request]
       SET [state]          = N'sent',
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state]    <> N'sent';

    DECLARE @changed INT = @@ROWCOUNT;

    /* Nothing to move, and nothing on file: the electrode got here without
       anybody asking, so the card is the only record there will be of it. */
    IF @changed = 0
    BEGIN
        INSERT INTO [qc_requests].[qc_request]
                    ([electrode], [kind], [state], [vat])
        SELECT @electrode, N'manual', N'sent',
               [qc_requests].[fn_vat_of](@electrode)
         WHERE NOT EXISTS (SELECT 1 FROM [qc_requests].[qc_request]
                            WHERE [electrode] = @electrode);
        SET @changed = @@ROWCOUNT;
    END

    SELECT @changed AS [changed];
END;
GO

/* ---- the ones that got here anyway ----------------------------------------- */

/* What is about to move. Read it before the transaction below. On
   dataloggingTest it should be B15749 and nothing else. */
SELECT r.[electrode], r.[kind], r.[state],
       CASE WHEN b.[electrode] IS NOT NULL THEN N'on the board'
            ELSE N'tested, archived' END AS [evidence],
       CONVERT(NVARCHAR(30), r.[updated_at_utc], 120) AS [said_since]
  FROM [qc_requests].[qc_request] AS r
  LEFT JOIN [qc_data].[qc_board_entry] AS b ON b.[electrode] = r.[electrode]
  LEFT JOIN [qc_data].[qc_board_archive] AS a
         ON a.[electrode] = r.[electrode] AND a.[caused_by] = a.[electrode]
        AND a.[status] IN (N'Tested and Released', N'Tested and Scrapped')
 WHERE r.[state] <> N'sent'
   AND (b.[electrode] IS NOT NULL OR a.[electrode] IS NOT NULL)
 ORDER BY r.[electrode];
GO

BEGIN TRANSACTION;

UPDATE r
   SET r.[state]          = N'sent',
       r.[updated_at_utc] = SYSUTCDATETIME()
  FROM [qc_requests].[qc_request] AS r
  LEFT JOIN [qc_data].[qc_board_entry] AS b ON b.[electrode] = r.[electrode]
  LEFT JOIN [qc_data].[qc_board_archive] AS a
         ON a.[electrode] = r.[electrode] AND a.[caused_by] = a.[electrode]
        AND a.[status] IN (N'Tested and Released', N'Tested and Scrapped')
 WHERE r.[state] <> N'sent'
   AND (b.[electrode] IS NOT NULL OR a.[electrode] IS NOT NULL);

/* No guard on the count, for the reason 013 gives: this is a rule, not a
   cleanup, and whatever matches it is the right number. */
PRINT CONCAT(N'Closed ', @@ROWCOUNT, N' request(s) for electrodes that reached QC anyway.');

COMMIT TRANSACTION;
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'015_a_card_outranks_a_refusal.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'015_a_card_outranks_a_refusal.sql');
GO
