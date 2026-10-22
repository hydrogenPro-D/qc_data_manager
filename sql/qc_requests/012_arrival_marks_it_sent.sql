/* 012_arrival_marks_it_sent.sql
   ---------------------------------------------------------------------------
   An electrode on the QC board is an electrode that arrived at QC, whatever
   anybody remembered to press.

     usp_request_arrived_at_qc   awaiting_production | to_be_sent -> sent

   WHAT GOES WRONG WITHOUT IT. QC asks for an electrode, somebody fetches it
   and walks it over, and nobody marks it sent in Production Data Manager.
   The request then sits in Requested or Pending for a fortnight, reads as
   work outstanding on both screens, and is eventually expired by 011 as
   unanswerable, which is the one thing it is not: it was answered by hand and
   the hand never wrote it down.

   A CARD IS THE EVIDENCE. Nothing else in either application says "this
   physical electrode is here" as plainly, so the card is what closes the
   request. QC does not have to remember a second step, and production is not
   chased for a tick on something they already did.

   NOT usp_request_mark_sent, WHICH QC IS NOT GRANTED. Marking a request sent
   is production's move and stays production's move: it is them saying they
   have handed it over. This is a different statement, made by a different
   application, from different evidence, and it is deliberately a different
   procedure so that neither grant implies the other.

   TWO STATES ONLY, both of them production's:

     awaiting_production   they never answered, but the electrode is here
     to_be_sent            they agreed and then forgot the last tick

   awaiting_qc is NOT included. That one is a question QC has been asked and
   has not answered, and a card appearing while an offer is open is a thing
   somebody should look at rather than a thing to close quietly. The state
   machine has no awaiting_qc to sent step either, so adding one here would
   widen the contract from the wrong side.

   TERMINAL STAYS TERMINAL. sent, declined and expired are untouched, as
   QC_REQUEST_EXPIRY_CONTRACT.md promises. An electrode whose request was
   declined or expired can still go on the board, and should: the board is
   about testing it, not about the paperwork. It simply gets no new request
   and tells no tale about one.

   SAFE TO CALL ON ANYTHING. An electrode with no request, or one already
   sent, changes nothing and says so with [changed] = 0, so the caller can
   fire it after every card without asking first whether there is a request.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 003 first (creates the request tables).', 1;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_arrived_at_qc]
    @electrode NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;

    /* No @expected_state. The other moves guard against a stale screen,
       because two applications write these rows and a decision can land while
       somebody is looking at one. There is no screen here: the caller is
       reporting a physical fact it has just recorded, and that fact is true
       whatever the row said a moment ago. */
    UPDATE [qc_requests].[qc_request]
       SET [state]          = N'sent',
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state] IN (N'awaiting_production', N'to_be_sent');

    SELECT @@ROWCOUNT AS [changed];
END;
GO

/* ---- grants ---------------------------------------------------------------- */
/* QC's, and only QC's: the board is theirs and so is the evidence. */
GRANT EXECUTE ON OBJECT::[qc_requests].[usp_request_arrived_at_qc]
    TO [qc_data_manager_role];
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'012_arrival_marks_it_sent.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'012_arrival_marks_it_sent.sql');
GO
