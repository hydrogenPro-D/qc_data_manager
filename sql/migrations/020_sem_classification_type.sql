/* 020_sem_classification_type.sql
   ---------------------------------------------------------------------------
   Turn the SEM note into a result chosen from a list, and give the rest of
   what people write a column of its own.

     sem_classification_type   the five results, as a lookup, so a typo
                                cannot enter the data and a rename is a migration
     sem_sample.comment         everything else the operator wants to record

   The note was free text and collected 163 spellings of five opinions:
   Microcracked, Mickrocracked, Microcraks, micro cracks and Microcracking are
   all the same result, and nothing can count them. From here the result is
   picked from the list and the typing goes next door.

   IN THREE PARTS, deliberately. This one adds the shapes and leaves every
   existing note exactly as it is, so the application keeps working the moment
   it is applied. Then sql/maintenance/transform_sem_notes.sql rewrites the old
   text into (result, comment). Then 021 adds the foreign key, once there is
   nothing left for it to reject.

   THE SEVERITY COLUMN IS THE ORDER THE LIST IS SHOWN IN, best first. It is not
   a score and nothing computes with it: it exists so the five read as a scale
   rather than as an alphabetical jumble (Cracked, Few micro cracks, Fine...).
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_sample]', N'U') IS NULL
    THROW 50000, N'Run 007 first (creates the SEM tables).', 1;

/* ---- the five results ----------------------------------------------------- */
IF OBJECT_ID(N'[qc_data].[sem_classification_type]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_classification_type] (
        [note]     NVARCHAR(64) NOT NULL
                   CONSTRAINT [PK_sem_classification_type] PRIMARY KEY,
        /* 1 is the best plate, 5 the worst. See the header. */
        [severity] TINYINT      NOT NULL
    );

MERGE [qc_data].[sem_classification_type] AS target
USING (VALUES (N'Fine', 1), (N'Few micro cracks', 2), (N'Microcracks', 3),
              (N'Less cracked', 4), (N'Cracked', 5))
      AS source ([note], [severity])
   ON target.[note] = source.[note]
 WHEN MATCHED AND target.[severity] <> source.[severity]
      THEN UPDATE SET target.[severity] = source.[severity]
 WHEN NOT MATCHED BY TARGET
      THEN INSERT ([note], [severity]) VALUES (source.[note], source.[severity]);

/* ---- everything that is not the result ------------------------------------ */
/* NULL means nobody wrote anything, which is different from an empty string:
   every write below goes through NULLIF so the two cannot drift apart. */
IF COL_LENGTH(N'[qc_data].[sem_sample]', N'comment') IS NULL
    ALTER TABLE [qc_data].[sem_sample] ADD [comment] NVARCHAR(1024) NULL;
GO

/* ---- the view the application reads ---------------------------------------- */
/* Redefined whole rather than patched: a view has to be the first statement in
   its batch, so the playbook keeps each one to a single CREATE OR ALTER. The
   only change from 008 is s.[comment]. */
EXEC (N'
CREATE OR ALTER VIEW [qc_data].[vw_sem_measurement] AS
    SELECT e.[generation],
           e.[electrode],
           e.[electrode_type],
           e.[folder],
           s.[position],
           s.[zoom],
           s.[operator],
           s.[note],
           s.[comment],
           s.[measured_on],
           r.[point],
           r.[symbol],
           r.[value],
           i.[images]
      FROM [qc_data].[sem_electrode] AS e
      JOIN [qc_data].[sem_sample] AS s
        ON s.[generation] = e.[generation]
       AND s.[electrode]  = e.[electrode]
      LEFT JOIN [qc_data].[sem_element_reading] AS r
        ON r.[generation] = s.[generation]
       AND r.[electrode]  = s.[electrode]
       AND r.[position]   = s.[position]
      OUTER APPLY (
            SELECT STRING_AGG(x.[filename], N''|'')
                   WITHIN GROUP (ORDER BY x.[ordinal]) AS [images]
              FROM [qc_data].[sem_sample_image] AS x
             WHERE x.[generation] = s.[generation]
               AND x.[electrode]  = s.[electrode]
               AND x.[position]   = s.[position]
      ) AS i;
');
GO

/* ---- writing the two of them ----------------------------------------------- */
/* @comment defaults to NULL and NULL means "leave it alone", so a caller that
   has not been updated yet still works and cannot wipe a comment it does not
   know about. Clearing one is done by sending an empty string. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_sem_sample_update]
    @generation NVARCHAR(16),
    @electrode  NVARCHAR(32),
    @position   NVARCHAR(64),
    @zoom       NVARCHAR(16)   = NULL,
    @operator   NVARCHAR(128)  = NULL,
    @note       NVARCHAR(512)  = NULL,
    @comment    NVARCHAR(1024) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    DECLARE @result NVARCHAR(512) = NULLIF(LTRIM(RTRIM(@note)), N'''');

    /* Said plainly here rather than left to the foreign key, so the message
       names the column and the value instead of a constraint. */
    IF @result IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_classification_type]
                        WHERE [note] = @result)
        THROW 50000, N''That is not one of the five SEM results.'', 1;

    UPDATE [qc_data].[sem_sample]
       SET [zoom]           = NULLIF(@zoom, N''''),
           [operator]       = NULLIF(@operator, N''''),
           [note]           = @result,
           [comment]        = CASE WHEN @comment IS NULL THEN [comment]
                                   ELSE NULLIF(LTRIM(RTRIM(@comment)), N'''') END,
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [generation] = @generation
       AND [electrode]  = @electrode
       AND [position]   = @position;

    SELECT @@ROWCOUNT AS [changed];
END;
');
GO

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'020_sem_classification_type.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'020_sem_classification_type.sql');
