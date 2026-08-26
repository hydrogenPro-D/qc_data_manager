/* 012_create_sem_edx_delete_procedure.sql
   ---------------------------------------------------------------------------
   Removing one SEM sample, from the sample window.

     usp_sem_sample_delete   one spot on one electrode, and everything read there

   The cascades in 007 do the work: deleting the sample takes its element
   readings and its captures with it. Only the filenames go. The images
   themselves live in the shared library and are not this application's to
   delete, which is worth knowing before pressing the button.

   THE ELECTRODE GOES TOO once its last sample has, since a sem_electrode row
   with no samples says only that somebody once measured something and then
   unsaid it. Its type and folder come back by themselves on the next import.

   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_sample]', N'U') IS NULL
    THROW 50000, N'Run 007 first (creates the SEM tables).', 1;

EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_sem_sample_delete]
    @generation NVARCHAR(16),
    @electrode  NVARCHAR(32),
    @position   NVARCHAR(64)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    BEGIN TRANSACTION;

        DELETE FROM [qc_data].[sem_sample]
         WHERE [generation] = @generation
           AND [electrode]  = @electrode
           AND [position]   = @position;

        DECLARE @deleted INT = @@ROWCOUNT;

        IF @deleted > 0
           AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_sample]
                            WHERE [generation] = @generation
                              AND [electrode]  = @electrode)
            DELETE FROM [qc_data].[sem_electrode]
             WHERE [generation] = @generation
               AND [electrode]  = @electrode;

    COMMIT TRANSACTION;

    SELECT @deleted AS [deleted];
END;
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'012_create_sem_edx_delete_procedure.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'012_create_sem_edx_delete_procedure.sql');
GO
