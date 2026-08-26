/* 010_create_electrode_status_procedures.sql
   ---------------------------------------------------------------------------
   The one write the Electrodes tab makes.

     usp_electrode_status_set   set one electrode's status by hand

   It writes the electrode's own row and marks it source = 'manual'. There is no
   separate override table and no way to clear a correction: to change a hand
   set status you set another one.

   WHY THERE IS NO CLEAR. Clearing would mean putting back whatever the status
   was before, and nothing here can work that out. A status of 'Tested and
   Released' comes from tested_electrodes.csv, which the ETL reads but never
   loads, so the database could only ever restore the range value, which for a
   tested electrode is the wrong answer.

   A hand set status deliberately does not propagate. A range says something
   about a stretch of production and a QC board decision sweeps onto neighbours;
   a correction says something about one electrode.

   THE LOADER MUST LEAVE THESE ROWS ALONE. data/ETL/04_load_to_database.py
   rebuilds this table from the ranges on every run and skips source = 'manual',
   or every correction would be undone by the next load.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[electrode_status]', N'U') IS NULL
    THROW 50000, N'Run 004 first (creates the status tables).', 1;

EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_electrode_status_set]
    @electrode NVARCHAR(32),
    @status    NVARCHAR(32)
AS
BEGIN
    SET NOCOUNT ON;

    IF NULLIF(LTRIM(RTRIM(@electrode)), N'''') IS NULL
        THROW 50000, N''An electrode marking is required.'', 1;

    SET @status = NULLIF(LTRIM(RTRIM(@status)), N'''');
    IF @status IS NULL
        THROW 50000, N''A status is required. There is no way to clear one.'', 1;

    IF NOT EXISTS (SELECT 1 FROM [qc_data].[electrode_status_kind]
                    WHERE [status] = @status)
        THROW 50000, N''Unknown status. See qc_data.electrode_status_kind.'', 1;

    UPDATE [qc_data].[electrode_status]
       SET [status]         = @status,
           [source]         = N''manual'',
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [electrode] = @electrode;

    /* An electrode no range covers, which is how B14272 got a status at all. */
    IF @@ROWCOUNT = 0
        INSERT INTO [qc_data].[electrode_status] ([electrode], [status], [source])
        VALUES (@electrode, @status, N''manual'');
END;
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'010_create_electrode_status_procedures.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'010_create_electrode_status_procedures.sql');
GO
