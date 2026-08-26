/* 011_create_sem_edx_procedures.sql
   ---------------------------------------------------------------------------
   Every write the SEM EDX tab makes.

     usp_sem_sample_import   one result folder, electrode to readings, in one call
     usp_sem_sample_update   the by-hand fields of one sample
     usp_sem_reading_set     correct one measured percentage

   ONE CALL PER IMPORT, not per row. A folder holds up to five samples of three
   points of ten elements, so row at a time would be a hundred and fifty round
   trips while an operator waits. The whole nested shape arrives as JSON and is
   unpacked with OPENJSON:

     {"generation": "Gen4.1", "electrode": "B13853",
      "electrode_type": "CATHODE", "folder": "Line 1/B13853",
      "samples": [
        {"position": "top left", "zoom": "x1000", "operator": "AB",
         "note": "", "measured_on": "2026-08-01",
         "images": ["capture1.tif", "capture2.tif"],
         "points": [{"point": 1,
                     "readings": [{"symbol": "Ni", "value": 38.2}]}]}]}

   A SAMPLE ALREADY RECORDED IS LEFT ALONE. Re-importing a folder must not
   silently replace readings somebody has since corrected, so the import adds
   what is new and reports how much it skipped. Correcting a figure is what
   usp_sem_reading_set is for.

   Only the readings themselves are ever written. Every ratio and average is
   arithmetic over them, which is why none of them can be set here.

   Points are not stored anywhere of their own: a point exists because a reading
   names it, so the import writes readings and the point follows.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_sample]', N'U') IS NULL
    THROW 50000, N'Run 007 first (creates the SEM tables).', 1;

/* ---- import one result folder ---------------------------------------------- */
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
                [note], [measured_on])
        SELECT @generation, @electrode, s.[position], s.[zoom], s.[operator],
               s.[note], s.[measured_on]
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

/* ---- the by-hand fields of one sample -------------------------------------- */
/* Zoom, operator and note belong to the spot rather than to any one point, so
   they are set once here and not repeated per reading. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_sem_sample_update]
    @generation NVARCHAR(16),
    @electrode  NVARCHAR(32),
    @position   NVARCHAR(64),
    @zoom       NVARCHAR(16)  = NULL,
    @operator   NVARCHAR(128) = NULL,
    @note       NVARCHAR(512) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    UPDATE [qc_data].[sem_sample]
       SET [zoom]           = NULLIF(@zoom, N''''),
           [operator]       = NULLIF(@operator, N''''),
           [note]           = NULLIF(@note, N''''),
           [updated_at_utc] = SYSUTCDATETIME()
     WHERE [generation] = @generation
       AND [electrode]  = @electrode
       AND [position]   = @position;

    SELECT @@ROWCOUNT AS [changed];
END;
');

/* ---- correct one measured percentage --------------------------------------- */
/* NULL is a reading that was never taken, which is stored as missing rather
   than as a zero: a zero would drag every average that includes it. */
EXEC (N'
CREATE OR ALTER PROCEDURE [qc_data].[usp_sem_reading_set]
    @generation NVARCHAR(16),
    @electrode  NVARCHAR(32),
    @position   NVARCHAR(64),
    @point      INT,
    @symbol     NVARCHAR(4),
    @value      DECIMAL(9,4) = NULL
AS
BEGIN
    SET NOCOUNT ON;

    /* The point has to already exist, and it exists because something was read
       at it. Tested for ANY element rather than for this one, so measuring a
       new element on an existing point still goes through. */
    IF NOT EXISTS (SELECT 1 FROM [qc_data].[sem_element_reading]
                    WHERE [generation] = @generation
                      AND [electrode]  = @electrode
                      AND [position]   = @position
                      AND [point]      = @point)
        THROW 50000, N''No such measurement point.'', 1;

    UPDATE [qc_data].[sem_element_reading]
       SET [value] = @value
     WHERE [generation] = @generation
       AND [electrode]  = @electrode
       AND [position]   = @position
       AND [point]      = @point
       AND [symbol]     = @symbol;

    /* An element measured for the first time on an existing point, which is
       what happens when a sheet starts recording one more. */
    IF @@ROWCOUNT = 0
        INSERT INTO [qc_data].[sem_element_reading]
               ([generation], [electrode], [position], [point], [symbol],
                [value])
        VALUES (@generation, @electrode, @position, @point, @symbol, @value);
END;
');

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'011_create_sem_edx_procedures.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'011_create_sem_edx_procedures.sql');
GO
