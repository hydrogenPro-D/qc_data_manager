/* 014_a_card_records_its_own_arrival.sql
   ---------------------------------------------------------------------------
   An electrode can reach QC without anybody having asked for it. Answers
   QC_BOARD_WITHOUT_REQUEST.md.

     usp_request_arrived_at_qc   now CREATES a sent request where none exists
     a one-time close for the requests left by the gap this fills

   WHAT WENT WRONG. B15719 was put on the board with no request behind it: QC
   wanted it, walked over, tested it, marked it Tested, failed. Nothing was
   written to qc_requests, so to Production Data Manager that electrode looked
   exactly like one nobody had touched, and their screen correctly offered
   Propose for QC. Somebody pressed it. The schema then held an offer, in
   awaiting_qc, for an electrode whose testing had finished a day earlier.
   Neither application could clear it: the offer is QC's to answer and there
   was nothing left to decide.

   012 DID NOT COVER IT because it moves a request that already exists, and
   there was none to move. The arrival itself went unrecorded, which is the
   hole the rest fell through.

   SO THE CARD WRITES THE ROW. A card is evidence the electrode reached QC,
   and that is precisely what sent means, so where no request exists the card
   creates one in that state. Kind [manual], which reads correctly: QC asked
   for it informally, by walking over. PDM then shows Sent, offers nothing to
   press, and the mistake cannot be made a second time.

   NO COUPONS ON IT, deliberately. Nobody chose any, because nobody raised the
   request. An empty value says "not recorded", which is true; filling in the
   default would be inventing an instruction that was never given.

   NO WINDOW CHECK EITHER. This records a fact rather than asking for
   anything, so how old the electrode is does not come into it.

   ---- the ones already there -------------------------------------------------

   013 closes an open request when a card appears, including an open offer,
   which is the second half of what PDM asked for. It does not reach B15719:
   that card has already been finalised and is off the board, so there is no
   card for 013's catch up to find.

   THE ARCHIVE IS EVIDENCE TOO. An electrode whose OWN archived row is a
   tested verdict was on a rig, whatever the board holds today, so an open
   request against it is closed here the same way. Only its own verdict counts:
   an electrode swept by somebody else's decision carries an archive row and
   never went anywhere.

   AND NOTHING IS BACKFILLED. Ten tested electrodes on the test database have
   no request at all, from before any of this existed. Creating sent requests
   for them would invent a record of arrivals nobody logged, and would move
   PDM's "sent in the last 7 days" counts for days that are already over. The
   new rule applies to cards from here on.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'013_a_card_accepts_the_offer.sql')
    THROW 50000, N'Run qc_requests 013 first.', 1;
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

    DECLARE @changed INT = @@ROWCOUNT;

    /* Nothing to move, and nothing on file: the electrode got here without
       anybody asking, so the card is the only record there will be of it. The
       NOT EXISTS also leaves a finished request alone, so an electrode that
       was declined or expired and then tested anyway keeps its history. */
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

/* ---- the requests this gap left behind ------------------------------------- */

/* What is about to move. Read it before the transaction below. B15719 is the
   one from the report; anything else here is the same shape. */
SELECT r.[electrode], r.[kind], r.[state], a.[status] AS [verdict],
       CONVERT(NVARCHAR(30), r.[created_at_utc], 120) AS [asked],
       CONVERT(NVARCHAR(30), a.[archived_at_utc], 120) AS [tested]
  FROM [qc_requests].[qc_request] AS r
  JOIN [qc_data].[qc_board_archive] AS a ON a.[electrode] = r.[electrode]
 WHERE r.[state] IN (N'awaiting_production', N'to_be_sent', N'awaiting_qc')
   AND a.[caused_by] = a.[electrode]
   AND a.[status] IN (N'Tested and Released', N'Tested and Scrapped')
 ORDER BY r.[electrode];
GO

BEGIN TRANSACTION;

UPDATE r
   SET r.[state]          = N'sent',
       r.[updated_at_utc] = SYSUTCDATETIME()
  FROM [qc_requests].[qc_request] AS r
  JOIN [qc_data].[qc_board_archive] AS a ON a.[electrode] = r.[electrode]
 WHERE r.[state] IN (N'awaiting_production', N'to_be_sent', N'awaiting_qc')
   AND a.[caused_by] = a.[electrode]
   AND a.[status] IN (N'Tested and Released', N'Tested and Scrapped');

/* No guard on the count, for the reason 013 gives: this is a rule, not a
   cleanup, and whatever matches it is the right number. Printed so whoever
   ran it can check it against the list above. On dataloggingTest it should be
   B15719 and nothing else, B16194 being a genuine open offer. */
PRINT CONCAT(N'Closed ', @@ROWCOUNT, N' request(s) for electrodes already tested.');

COMMIT TRANSACTION;
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'014_a_card_records_its_own_arrival.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'014_a_card_records_its_own_arrival.sql');
GO
