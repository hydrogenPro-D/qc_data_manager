/* 003_create_request_tables.sql
   ---------------------------------------------------------------------------
   The requests themselves.

     qc_request_kind   why a request exists at all
     qc_request_state  where it has got to
     qc_request        one row per electrode, ever

   ONE ROW PER ELECTRODE, AND THE MARKING IS THE KEY. An electrode is either
   asked about or it is not. Declined is final and sent cannot be asked again,
   so there is never a second row for the same marking and no surrogate id to
   map a clicked row back to. The consequence is worth saying out loud: once an
   electrode has been declined it is out of the sampling pool for good, which
   is the intended reading, since the reason for a decline is that nobody can
   get hold of it.

   NO ACTOR COLUMN. Every operator of either application connects as the same
   contained user, so a "who" column would be one constant on every row. Any
   technician on shift is an acceptable answer to who did this.

   TIMES, THOUGH, YES. Not as a record of anything: the queue reads oldest
   first, the window is counted in days, and row_version is what the polling
   compares. Those are three uses with no substitute.

   WHICH SIDE DECIDES is the state, not a column. A request raised by QC is
   awaiting_production and only production can answer it; one offered by a
   technician is awaiting_qc and only QC can. 004 gives each side EXECUTE on
   its own answers and nothing else, so that reading is enforced rather than
   agreed.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_requests].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run qc_requests 001 first (creates the schema + ledger).', 1;

/* ---- why a request exists -------------------------------------------------- */
/* interval  the sampling rule came round to this electrode
   fault     a failure nearby, and this is one of the ones to test next
   manual    somebody asked for this one on purpose

   The kind is kept because the three behave differently when they go wrong: an
   interval request that is declined leaves a gap in the sampling, a fault one
   leaves a fault zone unresolved, and a manual one is somebody's own business.
   Telling them apart afterwards is impossible without recording it. */
IF OBJECT_ID(N'[qc_requests].[qc_request_kind]', N'U') IS NULL
    CREATE TABLE [qc_requests].[qc_request_kind] (
        [kind] NVARCHAR(16) NOT NULL
               CONSTRAINT [PK_qc_request_kind] PRIMARY KEY
    );

MERGE [qc_requests].[qc_request_kind] AS target
USING (VALUES (N'interval'), (N'fault'), (N'manual')) AS source ([kind])
   ON target.[kind] = source.[kind]
WHEN NOT MATCHED THEN INSERT ([kind]) VALUES (source.[kind]);

/* ---- where it has got to --------------------------------------------------- */
/* [is_final] is what tells the two terminal states from the three live ones,
   so nothing has to name them in SQL. A row in a final state never moves
   again. */
IF OBJECT_ID(N'[qc_requests].[qc_request_state]', N'U') IS NULL
    CREATE TABLE [qc_requests].[qc_request_state] (
        [state]    NVARCHAR(24) NOT NULL
                   CONSTRAINT [PK_qc_request_state] PRIMARY KEY,
        [is_final] BIT NOT NULL
    );

MERGE [qc_requests].[qc_request_state] AS target
USING (VALUES (N'awaiting_production', 0),   /* QC asked, production to answer */
              (N'awaiting_qc',         0),   /* a technician offered one       */
              (N'to_be_sent',          0),   /* agreed, not yet gone           */
              (N'sent',                1),
              (N'declined',            1))
      AS source ([state], [is_final])
   ON target.[state] = source.[state]
WHEN MATCHED AND target.[is_final] <> source.[is_final]
    THEN UPDATE SET [is_final] = source.[is_final]
WHEN NOT MATCHED
    THEN INSERT ([state], [is_final]) VALUES (source.[state], source.[is_final]);

/* ---- the request ----------------------------------------------------------- */
IF OBJECT_ID(N'[qc_requests].[qc_request]', N'U') IS NULL
    CREATE TABLE [qc_requests].[qc_request] (
        /* The marking, as dbo.MetaData spells it. A natural key: an electrode
           is identified by what it is, so a re-sorted table cannot make an
           answer land on the wrong request. */
        [electrode]      NVARCHAR(32) NOT NULL
                         CONSTRAINT [PK_qc_request] PRIMARY KEY,
        [kind]           NVARCHAR(16) NOT NULL
                         CONSTRAINT [FK_qc_request_kind]
                         REFERENCES [qc_requests].[qc_request_kind]([kind]),
        [state]          NVARCHAR(24) NOT NULL
                         CONSTRAINT [FK_qc_request_state]
                         REFERENCES [qc_requests].[qc_request_state]([state]),
        /* The vat as it stood when the request was raised, copied rather than
           joined. The sampling rule counts within a vat, and a request has to
           go on meaning what it meant even if the process log is reloaded. */
        [vat]            NVARCHAR(32) NULL,
        [created_at_utc] DATETIME2 NOT NULL
                         CONSTRAINT [DF_qc_request_created]
                         DEFAULT SYSUTCDATETIME(),
        [updated_at_utc] DATETIME2 NOT NULL
                         CONSTRAINT [DF_qc_request_updated]
                         DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION NOT NULL
    );

/* The sampling rule asks one question over and over: what is the highest
   numbered electrode in this vat that already has a request? */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_qc_request_vat'
                 AND object_id = OBJECT_ID(N'[qc_requests].[qc_request]'))
    CREATE INDEX [IX_qc_request_vat]
        ON [qc_requests].[qc_request] ([vat]) INCLUDE ([electrode], [state]);

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'003_create_request_tables.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'003_create_request_tables.sql');
GO
