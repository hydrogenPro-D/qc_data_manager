/* 007_create_sem_edx_measurement_tables.sql
   ---------------------------------------------------------------------------
   The SEM EDX readings themselves.

     sem_electrode        one electrode: what it is, and where its captures live
     sem_sample           one spot on that electrode: B13853 bottom left
     sem_element_reading  one element's reading at one point of that spot
     sem_sample_image     the captures belonging to that spot

   NO POINT TABLE, AND NO FIXED THREE POINTS. The point is part of a reading's
   key, nothing more. A row saying only "point 2 of this sample exists" would
   restate what its readings already say, and a point with no readings is not
   something the application can produce. Three points is a habit either way:
   136 samples hold one, 169 hold two.

   ONE ROW PER ELEMENT, not a column per element. Which elements a sheet records
   has already changed once and differs by generation, so columns would mean a
   migration every time somebody starts measuring something new. This way a new
   element is a row, and the only thing that decides which are *shown* is
   sem_edx.layout in config/config.json.

   WHY sem_electrode EXISTS. Anode or cathode is a property of the electrode,
   not of the spot you measured, and so is the result folder. Held on the sample
   they would depend on part of its key, which is not merely untidy: nothing
   would stop B13853 top left being an anode and B13853 bottom left a cathode,
   putting one electrode on both sub tabs at once. Here it can only be said
   once. There is no foreign key onward to dbo.MetaData, since SEM regularly
   turns up electrodes that table has never heard of, and it is absent from the
   test database entirely.

   Nothing derived is stored. Every ratio, every per point ratio, every sample
   average and the per electrode average are all arithmetic over these
   readings, so storing them would only let the copy drift from the source.
   Correcting one reading corrects everything that follows from it.

   NATURAL KEYS THROUGHOUT, no identity columns. The tables are small, a few
   thousand rows, and the alternative costs the loader a round trip per level to
   read surrogate keys back before it can insert the next one. The wide keys
   also make the tables readable by hand, which matters while this is new.

   Every mutable table carries a ROWVERSION, because the application polls
   (MAX(row_version), COUNT(*)) to notice another machine's changes.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF OBJECT_ID(N'[qc_data].[sem_generation]', N'U') IS NULL
    THROW 50000, N'Run 006 first (creates the generations).', 1;

/* ---- one electrode -------------------------------------------------------- */
IF OBJECT_ID(N'[qc_data].[sem_electrode]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_electrode] (
        [generation]     NVARCHAR(16) NOT NULL
                         CONSTRAINT [FK_sem_electrode_generation]
                         REFERENCES [qc_data].[sem_generation]([generation]),
        /* Normalized: the workbook and the image library both pad markings,
           and B000897, B0897 and B897 are one electrode. */
        [electrode]      NVARCHAR(32) NOT NULL,
        [electrode_type] NVARCHAR(16) NOT NULL
                         CONSTRAINT [CK_sem_electrode_type]
                         CHECK ([electrode_type] IN (N'ANODE', N'CATHODE')),
        /* Relative to the shared SEM images library, so the path means the
           same on every machine. NULL for an electrode with no folder, which
           is six of the Gen 3.1 ones. */
        [folder]         NVARCHAR(400) NULL,
        [updated_at_utc] DATETIME2    NOT NULL
                         CONSTRAINT [DF_sem_electrode_at] DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION   NOT NULL,
        CONSTRAINT [PK_sem_electrode] PRIMARY KEY ([generation], [electrode])
    );

/* The same marking can appear under two generations, so this is not unique:
   it is for "show me everything measured on B13853". */
IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_sem_electrode_marking'
                 AND object_id = OBJECT_ID(N'[qc_data].[sem_electrode]'))
    CREATE INDEX [IX_sem_electrode_marking]
        ON [qc_data].[sem_electrode] ([electrode]);

/* ---- one spot on that electrode ------------------------------------------- */
IF OBJECT_ID(N'[qc_data].[sem_sample]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_sample] (
        [generation]     NVARCHAR(16) NOT NULL,
        [electrode]      NVARCHAR(32) NOT NULL,
        /* Where on the plate: 'top right', 'bottom left', 'disc'. Free text on
           purpose, the sheets use over a hundred spellings between them. */
        [position]       NVARCHAR(64) NOT NULL,
        [zoom]           NVARCHAR(16) NULL,
        /* A real person: nothing in a SEM export names who ran the microscope,
           so this is typed into the import dialog. */
        [operator]       NVARCHAR(128) NULL,
        [note]           NVARCHAR(512) NULL,
        /* The workbooks record no date at all. Only samples imported from a
           result folder have one, read off the report's page header. */
        [measured_on]    DATE          NULL,
        [updated_at_utc] DATETIME2    NOT NULL
                         CONSTRAINT [DF_sem_sample_at] DEFAULT SYSUTCDATETIME(),
        [row_version]    ROWVERSION   NOT NULL,
        CONSTRAINT [PK_sem_sample]
            PRIMARY KEY ([generation], [electrode], [position]),
        CONSTRAINT [FK_sem_sample_electrode]
            FOREIGN KEY ([generation], [electrode])
            REFERENCES [qc_data].[sem_electrode] ([generation], [electrode])
            ON DELETE CASCADE
    );

/* ---- one element's reading at one point ----------------------------------- */
IF OBJECT_ID(N'[qc_data].[sem_element_reading]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_element_reading] (
        [generation] NVARCHAR(16) NOT NULL,
        [electrode]  NVARCHAR(32) NOT NULL,
        [position]   NVARCHAR(64) NOT NULL,
        /* Which of the points taken at this spot, usually 1 to 3.
           Deliberately unconstrained. */
        [point]      INT          NOT NULL,
        /* A chemical symbol: Ni, S, Co, C, N, O, Cl, Al, Si, Mg. Kept as the
           symbol rather than as a foreign key, so a new element needs nothing
           creating first. */
        [symbol]     NVARCHAR(4)  NOT NULL,
        /* Atomic percent. A report lists one decimal, and the odd historical
           row was measured in weight percent and is flagged as such in the
           sample's note. */
        [value]      DECIMAL(9,4) NULL,
        [row_version] ROWVERSION  NOT NULL,
        CONSTRAINT [PK_sem_element_reading]
            PRIMARY KEY ([generation], [electrode], [position], [point], [symbol]),
        CONSTRAINT [FK_sem_element_reading_sample]
            FOREIGN KEY ([generation], [electrode], [position])
            REFERENCES [qc_data].[sem_sample]
                       ([generation], [electrode], [position])
            ON DELETE CASCADE
    );

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE name = N'IX_sem_element_reading_symbol'
                 AND object_id = OBJECT_ID(N'[qc_data].[sem_element_reading]'))
    CREATE INDEX [IX_sem_element_reading_symbol]
        ON [qc_data].[sem_element_reading] ([symbol])
        INCLUDE ([value]);

/* ---- the captures belonging to that spot ---------------------------------- */
IF OBJECT_ID(N'[qc_data].[sem_sample_image]', N'U') IS NULL
    CREATE TABLE [qc_data].[sem_sample_image] (
        [generation] NVARCHAR(16) NOT NULL,
        [electrode]  NVARCHAR(32) NOT NULL,
        [position]   NVARCHAR(64) NOT NULL,
        /* The order they were taken in, which is the order they are paged
           through, and it is NOT the filename order. This library holds five
           naming conventions, and where one sample mixes them the characters
           stop matching the sequence: B943 has an underscore in one name and a
           space in the other, so its second capture sorts first. */
        [ordinal]    INT          NOT NULL,
        /* A filename within the electrode's folder, not a path. */
        [filename]   NVARCHAR(260) NOT NULL,
        [row_version] ROWVERSION  NOT NULL,
        CONSTRAINT [PK_sem_sample_image]
            PRIMARY KEY ([generation], [electrode], [position], [ordinal]),
        /* The key above orders the captures but says nothing about repeats. */
        CONSTRAINT [UQ_sem_sample_image_filename]
            UNIQUE ([generation], [electrode], [position], [filename]),
        CONSTRAINT [FK_sem_sample_image_sample]
            FOREIGN KEY ([generation], [electrode], [position])
            REFERENCES [qc_data].[sem_sample]
                       ([generation], [electrode], [position])
            ON DELETE CASCADE
    );

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'007_create_sem_edx_measurement_tables.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'007_create_sem_edx_measurement_tables.sql');
GO
