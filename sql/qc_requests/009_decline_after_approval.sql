/* 009_decline_after_approval.sql
   ---------------------------------------------------------------------------
   Production can say an electrode cannot be found after it has agreed to send
   it. Answers QC_DECLINE_AFTER_APPROVAL.md.

     usp_request_production_decline   now also moves to_be_sent -> declined

   WHAT WAS WRONG. Production answers in two steps: a technician says the
   electrode can be sent, which puts the request in to_be_sent, and somebody
   then fetches it and marks it sent. In between it can turn out to be damaged,
   already shipped or simply not findable, and there was no way to say so: the
   only exit from to_be_sent was sent, which would record an electrode as
   delivered to QC when it never went.

   WHY THE OLD ONE REFUSED. It passed through usp_request_move with @from_state
   fixed to awaiting_production, and that procedure returns 0 when the caller's
   @expected_state names any other state. The guard is right, it stops a stale
   screen moving a row, but here it was firing on a call that was not stale at
   all, so the only thing the other application could say was "your screen is
   out of date" when the truth was "the electrode is missing".

   ONE STATE MORE, NOT A SECOND PROCEDURE. Declined is already terminal and
   already means production is not sending this one; WHY it is not being sent
   does not change what the row means. A separate usp_request_unavailable would
   add a procedure, a grant and a branch in the caller to reach the same end.

   NOT THROUGH usp_request_move, because that takes one from-state by design
   and widening it would loosen the guard for the four other moves that share
   it. The guard is kept here instead, and kept exactly as strict: the row moves
   only if its state is still the one the caller had on screen.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[usp_request_production_decline]', N'P') IS NULL
    THROW 50000, N'Run the qc_requests migrations first (001 to 008).', 1;
GO

CREATE OR ALTER PROCEDURE [qc_requests].[usp_request_production_decline]
    @electrode      NVARCHAR(32),
    @expected_state NVARCHAR(24)
AS
BEGIN
    SET NOCOUNT ON;

    SET @electrode = NULLIF(LTRIM(RTRIM(@electrode)), N'');
    IF @electrode IS NULL
        THROW 50000, N'An electrode is needed.', 1;
    IF @expected_state IS NULL
        THROW 50000, N'@expected_state is required: pass the state the caller believes the request is in.', 1;

    /* The two a request can be declined from. Anything else is a caller asking
       for something this move cannot do, whatever the row says, and is
       answered the same way a stale screen is: nothing happened. */
    IF @expected_state NOT IN (N'awaiting_production', N'to_be_sent')
    BEGIN
        SELECT 0 AS [changed];
        RETURN;
    END

    /* The guard, unchanged: the state the caller had on screen has to still be
       the state the row is in. Two applications write these rows, so a decision
       can land while a technician is looking at one. */
    UPDATE [qc_requests].[qc_request]
       SET [state]          = N'declined',
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode
       AND [state]     = @expected_state;

    SELECT @@ROWCOUNT AS [changed];
END;
GO

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'009_decline_after_approval.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'009_decline_after_approval.sql');
GO
