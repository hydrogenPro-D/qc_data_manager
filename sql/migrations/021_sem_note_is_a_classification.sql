/* 021_sem_note_is_a_classification.sql
   ---------------------------------------------------------------------------
   Close the door behind the transformation: from here the SEM note can only
   be one of the five results.

     FK_sem_sample_note        sem_sample.note -> sem_classification_type.note
     usp_sem_sample_import     an unrecognised note is filed as a comment
     sem_sample.note           narrowed to NVARCHAR(64), since a key needs
                               both sides the same length

   RUN THIS LAST. 020 adds the shapes, sql/maintenance/transform_sem_notes.sql
   rewrites the old free text, and only then is there nothing left for the key
   to reject. It checks, and says which samples are in the way rather than
   failing with a constraint name.

   THE IMPORT NEVER FAILS OVER A WORD. An older build of the application still
   sends whatever was typed into its import dialog, so the procedure now tests
   the note against the five and files anything else as the sample's comment.
   That keeps a folder import working on every build, before and after this,
   and nothing anybody typed is lost.

   The procedure below is 011's, with those edits and no others.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[sem_classification_type]', N'U') IS NULL
    THROW 50000, N'Run 020 first (creates the list of results).', 1;

/* ---- is the old text still there? ------------------------------------------ */
IF EXISTS (SELECT 1 FROM [qc_data].[sem_sample] AS s
            WHERE s.[note] IS NOT NULL
              AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_classification_type] AS k
                               WHERE k.[note] = s.[note]))
BEGIN
    SELECT TOP (50) s.[generation], s.[electrode], s.[position], s.[note]
      FROM [qc_data].[sem_sample] AS s
     WHERE s.[note] IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_classification_type] AS k
                        WHERE k.[note] = s.[note])
     ORDER BY s.[electrode], s.[position];

    THROW 50000, N'Notes that are not results are still here. Run sql/maintenance/transform_sem_notes.sql first; the rows above are a sample of what is in the way.', 1;
END;
GO

/* ---- the import, with an unknown note filed as a comment ------------------- */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_sem_sample_import]
    @payload NVARCHAR(MAX)
AS
BEGIN
    SET NOCOUNT ON;
    SET XACT_ABORT ON;

    IF ISJSON(@payload) <> 1
        THROW 50000, N''@payload is not valid JSON.'', 1;

    DECLARE @generation     NVARCHAR(16),
            @electrode      NVARCHAR(32),
            @electrode_type NVARCHAR(16),
            @folder         NVARCHAR(400);

    SELECT @generation     = JSON_VALUE(@payload, ''$.generation''),
           @electrode      = JSON_VALUE(@payload, ''$.electrode''),
           @electrode_type = UPPER(JSON_VALUE(@payload, ''$.electrode_type'')),
           @folder         = JSON_VALUE(@payload, ''$.folder'');

    IF NULLIF(LTRIM(RTRIM(@electrode)), N'''') IS NULL
        THROW 50000, N''An electrode marking is required.'', 1;
    IF NOT EXISTS (SELECT 1 FROM [qc_data].[sem_generation]
                    WHERE [generation] = @generation)
        THROW 50000, N''Unknown generation. See qc_data.sem_generation.'', 1;
    IF @electrode_type NOT IN (N''ANODE'', N''CATHODE'')
        THROW 50000, N''The electrode type must be ANODE or CATHODE.'', 1;

    /* The samples of this folder, flattened once and reused by every step
       below, so the JSON is parsed a single time. */
    DECLARE @sample TABLE (
        [position]    NVARCHAR(64) NOT NULL PRIMARY KEY,
        [zoom]        NVARCHAR(16)  NULL,
        [operator]    NVARCHAR(128) NULL,
        [note]        NVARCHAR(512) NULL,
        /* Where a note that is not one of the five results ends up. */
        [comment]     NVARCHAR(1024) NULL,
        [measured_on] DATE          NULL);

    INSERT INTO @sample ([position], [zoom], [operator], [note], [measured_on])
    SELECT s.[position],
           NULLIF(s.[zoom], N''''),
           NULLIF(s.[operator], N''''),
           NULLIF(s.[note], N''''),
           TRY_CONVERT(DATE, NULLIF(s.[measured_on], N''''))
      FROM OPENJSON(@payload, ''$.samples'') WITH (
               [position]    NVARCHAR(64),
               [zoom]        NVARCHAR(16),
               [operator]    NVARCHAR(128),
               [note]        NVARCHAR(512),
               [measured_on] NVARCHAR(32)) AS s
     WHERE NULLIF(LTRIM(RTRIM(s.[position])), N'''') IS NOT NULL;

    /* A NOTE THAT IS NOT A RESULT IS A COMMENT, never an error. An older
       build still sends whatever was typed into its import dialog, and losing
       a folder import over a word is a worse outcome than filing that word one
       column to the right. */
    UPDATE s
       SET s.[comment] = s.[note], s.[note] = NULL
      FROM @sample AS s
     WHERE s.[note] IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_classification_type] AS k
                        WHERE k.[note] = s.[note]);

    DECLARE @skipped INT =
        (SELECT COUNT(*) FROM @sample AS s
          WHERE EXISTS (SELECT 1 FROM [qc_data].[sem_sample] AS x
                         WHERE x.[generation] = @generation
                           AND x.[electrode]  = @electrode
                           AND x.[position]   = s.[position]));

    BEGIN TRANSACTION;

        /* The electrode first: it is what the samples hang off. An existing one
           keeps its type, and only gains a folder if it had none. */
        UPDATE [qc_data].[sem_electrode]
           SET [folder]         = ISNULL([folder], @folder),
               [updated_at_utc] = SYSUTCDATETIME()
         WHERE [generation] = @generation AND [electrode] = @electrode;

        IF @@ROWCOUNT = 0
            INSERT INTO [qc_data].[sem_electrode]
                   ([generation], [electrode], [electrode_type], [folder])
            VALUES (@generation, @electrode, @electrode_type,
                    NULLIF(@folder, N''''));

        INSERT INTO [qc_data].[sem_sample]
               ([generation], [electrode], [position], [zoom], [operator],
                [note], [comment], [measured_on])
        SELECT @generation, @electrode, s.[position], s.[zoom], s.[operator],
               s.[note], s.[comment], s.[measured_on]
          FROM @sample AS s
         WHERE NOT EXISTS (SELECT 1 FROM [qc_data].[sem_sample] AS x
                            WHERE x.[generation] = @generation
                              AND x.[electrode]  = @electrode
                              AND x.[position]   = s.[position]);

        INSERT INTO [qc_data].[sem_element_reading]
               ([generation], [electrode], [position], [point], [symbol],
                [value])
        SELECT @generation, @electrode, s.[position], p.[point], r.[symbol],
               r.[value]
          FROM OPENJSON(@payload, ''$.samples'') WITH (
                   [position] NVARCHAR(64),
                   [points]   NVARCHAR(MAX) AS JSON) AS s
         CROSS APPLY OPENJSON(s.[points]) WITH (
                   [point]    INT,
                   [readings] NVARCHAR(MAX) AS JSON) AS p
         CROSS APPLY OPENJSON(p.[readings]) WITH (
                   [symbol] NVARCHAR(4),
                   [value]  DECIMAL(9,4)) AS r
         WHERE p.[point] IS NOT NULL
           AND NULLIF(LTRIM(RTRIM(r.[symbol])), N'''') IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_element_reading] AS x
                            WHERE x.[generation] = @generation
                              AND x.[electrode]  = @electrode
                              AND x.[position]   = s.[position]
                              AND x.[point]      = p.[point]
                              AND x.[symbol]     = r.[symbol]);

        /* The captures keep the order they were chosen in, which is the order
           the viewer pages through them. */
        INSERT INTO [qc_data].[sem_sample_image]
               ([generation], [electrode], [position], [ordinal], [filename])
        SELECT @generation, @electrode, s.[position],
               CONVERT(INT, i.[key]) + 1, i.[value]
          FROM OPENJSON(@payload, ''$.samples'') WITH (
                   [position] NVARCHAR(64),
                   [images]   NVARCHAR(MAX) AS JSON) AS s
         CROSS APPLY OPENJSON(s.[images]) AS i
         WHERE NULLIF(LTRIM(RTRIM(i.[value])), N'''') IS NOT NULL
           AND NOT EXISTS (SELECT 1 FROM [qc_data].[sem_sample_image] AS x
                            WHERE x.[generation] = @generation
                              AND x.[electrode]  = @electrode
                              AND x.[position]   = s.[position]
                              AND x.[ordinal]    = CONVERT(INT, i.[key]) + 1);

    COMMIT TRANSACTION;

    SELECT (SELECT COUNT(*) FROM @sample) - @skipped AS [imported],
           @skipped                                  AS [skipped];
END;
');
GO

/* ---- the note is a short word now ------------------------------------------ */
/* A foreign key needs both sides declared the same length. 007 gave the note
   512 characters because it held a sentence, and by here it holds at most
   'Few micro cracks', so the column is narrowed to the list rather than the
   list widened to a size nothing will ever use. The check above has already
   proved there is nothing longer left in it. */
IF COL_LENGTH(N'[qc_data].[sem_sample]', N'note') <> 128
    ALTER TABLE [qc_data].[sem_sample] ALTER COLUMN [note] NVARCHAR(64) NULL;
GO

/* ---- and the key ----------------------------------------------------------- */
/* NULL stays allowed: 442 samples carry no result at all, which is a fact
   about them rather than a gap to fill. */
IF NOT EXISTS (SELECT 1 FROM sys.foreign_keys
               WHERE name = N'FK_sem_sample_note')
    ALTER TABLE [qc_data].[sem_sample] WITH CHECK
        ADD CONSTRAINT [FK_sem_sample_note]
            FOREIGN KEY ([note]) REFERENCES [qc_data].[sem_classification_type] ([note]);

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'021_sem_note_is_a_classification.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'021_sem_note_is_a_classification.sql');
