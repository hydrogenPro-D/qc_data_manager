/* 013_a_card_accepts_the_offer.sql
   ---------------------------------------------------------------------------
   A card on the QC board closes an offer as well as a request.

     usp_request_arrived_at_qc   now also takes awaiting_qc -> sent
     a one-time catch up for the cards already on the board

   WHAT 012 LEFT OUT, AND WHY IT WAS WRONG. 012 closed the two states where
   production had the move and deliberately left awaiting_qc alone, on the
   argument that an offer is QC's question to answer and a card appearing
   while one is open is worth somebody looking at.

   It is not. B15779 was offered by production on 13.10, QC took the electrode
   and put it on the board, and the row stayed at awaiting_qc: QCDM stopped
   showing it, because an electrode on a rig has nothing to ask about, while
   PDM went on reading "proposal pending" for an electrode QC had been holding
   for nine days. The offer had been accepted in the only way that matters.
   Nobody was going to press anything.

   SO THE CARD MEANS BOTH THINGS AT ONCE. QC accepted the offer, and the
   electrode arrived. Both are true the moment the card lands and both are
   evidenced by the same card, so the row goes straight to sent rather than
   through to_be_sent and out again in the same breath. Nothing records the
   steps a request took, only where it is, so a state it would have held for
   no time at all is not information anybody loses.

   STILL NOT usp_request_mark_sent, which remains production's alone. See 012.

   TERMINAL STAYS TERMINAL. sent, declined and expired are untouched, as
   QC_REQUEST_EXPIRY_CONTRACT.md promises.

   THE CATCH UP IS PART OF THE SAME RULE, not a second one. The procedure runs
   when a card is added, and the cards already on the board were added before
   this existed, so without it B15779 would wait until somebody took it off
   the board and put it back. It is written as the rule applied to every card
   standing today, which is what it is, and it reads qc_data because that is
   where the cards are. The procedure itself still takes one electrode and
   knows nothing about boards.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'012_arrival_marks_it_sent.sql')
    THROW 50000, N'Run qc_requests 012 first.', 1;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_arrived_at_qc]
    @electrode NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    /* All three live states. Whoever had the move, the card says it has been
       made: production handed the electrode over, or QC accepted what was
       offered, or both. No @expected_state, for the reason 012 gives: there is
       no screen here, only a physical fact the caller has just recorded. */
    UPDATE [qc_requests].[qc_request]
       SET [state]          = N'sent',
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state] IN (N'awaiting_production', N'to_be_sent', N'awaiting_qc');

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- the cards already on the board ---------------------------------------- */

/* What is about to move. Read it before the transaction below. */
SELECT r.[electrode], r.[kind], r.[state], b.[stage],
       CONVERT(NVARCHAR(30), r.[updated_at_utc], 120) AS [waiting_since]
  FROM [qc_requests].[qc_request] AS r
  JOIN [qc_data].[qc_board_entry] AS b ON b.[electrode] = r.[electrode]
 WHERE r.[state] IN (N'awaiting_production', N'to_be_sent', N'awaiting_qc')
 ORDER BY r.[electrode];
GO

BEGIN TRANSACTION;

UPDATE r
   SET r.[state]          = N'sent',
       r.[updated_at_utc] = SYSUTCDATETIME()
  FROM [qc_requests].[qc_request] AS r
  JOIN [qc_data].[qc_board_entry] AS b ON b.[electrode] = r.[electrode]
 WHERE r.[state] IN (N'awaiting_production', N'to_be_sent', N'awaiting_qc');

/* No guard on the count: unlike a cleanup script this is a rule, and however
   many cards are on the board when it runs is the right number. Printed so
   whoever ran it can check it against the list above. */
PRINT CONCAT(N'Closed ', @@ROWCOUNT, N' request(s) for electrodes already on the board.');

COMMIT TRANSACTION;
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'013_a_card_accepts_the_offer.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'013_a_card_accepts_the_offer.sql');
GO
