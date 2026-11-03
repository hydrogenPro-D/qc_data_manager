/* 001_create_qc_requests_schema.sql
   ---------------------------------------------------------------------------
   The schema the two applications share.

     qc_requests.schema_migrations   its own ledger, numbered from 001

   A SEPARATE FOLDER AND A SEPARATE LEDGER from sql/migrations. Those are
   qc_data's, which is this application's own business and nobody else's.
   Everything under qc_requests is the published interface between QC Data
   Manager and Production Data Manager, so it versions on its own: a change
   here is a change to a contract and has to be read as one, while a change in
   qc_data is a change to an implementation.

   Keeping one ledger for both would also have made each schema's history
   depend on the other's numbering, and they are applied by different people at
   different times.

   OWNED BY dbo, deliberately. The due-requests procedure reads dbo.MetaData
   and dbo.ProcessData to work out which electrode is next in a vat. With the
   same owner either side the ownership chain is unbroken, so EXECUTE on the
   procedure is the whole of what a caller needs and neither application has to
   be granted anything under dbo.

   WHO OWNS THE FILES. This project. Production Data Manager consumes what is
   here and asks for changes rather than making them. See QC_WORKFLOW_DESIGN.md
   in that repository.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

/* CREATE SCHEMA has to be the first statement in its batch, and the playbook
   keeps each file to a single batch, so it goes through EXEC. */
IF SCHEMA_ID(N'qc_requests') IS NULL
    EXEC (N'CREATE SCHEMA [qc_requests] AUTHORIZATION [dbo]');

IF OBJECT_ID(N'[qc_requests].[schema_migrations]', N'U') IS NULL
    CREATE TABLE [qc_requests].[schema_migrations] (
        [id]             INT IDENTITY PRIMARY KEY,
        [filename]       NVARCHAR(260) NOT NULL UNIQUE,
        [applied_at_utc] DATETIME2     NOT NULL
                         CONSTRAINT [DF_qc_requests_migrations_at] DEFAULT SYSUTCDATETIME(),
        [applied_by]     NVARCHAR(256) NOT NULL
                         CONSTRAINT [DF_qc_requests_migrations_by] DEFAULT SUSER_SNAME()
    );

IF NOT EXISTS (SELECT 1 FROM [qc_requests].[schema_migrations]
               WHERE filename = N'001_create_qc_requests_schema.sql')
    INSERT INTO [qc_requests].[schema_migrations] (filename)
    VALUES (N'001_create_qc_requests_schema.sql');
GO
