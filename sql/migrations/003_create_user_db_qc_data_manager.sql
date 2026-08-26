/* 003_create_user_db_qc_data_manager.sql
   ---------------------------------------------------------------------------
   The contained user the application signs in as, and its membership of the
   role from 002.

   HOW TO RUN THIS ONE: type a real password into @pw below, run the file, then
   close it WITHOUT SAVING. The committed copy keeps only the placeholder, and
   the guard throws if you forget to set one.

   Two gotchas this encodes:
     * EXEC() takes concatenated literals and variables, never function calls,
       so the escaping happens in a variable first and EXEC runs the variable.
     * REPLACE doubles any single quote, so a password containing one cannot
       break out of the string literal.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals
               WHERE name = N'qc_data_manager_role' AND type = 'R')
    THROW 50000, N'Run 002 first (creates the role).', 1;

DECLARE @pw NVARCHAR(128) = N'<<SET_PASSWORD_HERE_THEN_DO_NOT_SAVE>>';

IF @pw LIKE N'%<<%' OR @pw LIKE N'%>>%' OR LEN(@pw) < 8
    THROW 50000, N'Set a real password before running this file.', 1;

DECLARE @sql NVARCHAR(MAX) =
    N'CREATE USER [db_qc_data_manager_user] WITH PASSWORD = N'''
    + REPLACE(@pw, N'''', N'''''') + N'''';

IF NOT EXISTS (SELECT 1 FROM sys.database_principals
               WHERE name = N'db_qc_data_manager_user')
    EXEC (@sql);

IF NOT EXISTS (
    SELECT 1 FROM sys.database_role_members rm
    JOIN sys.database_principals r ON rm.role_principal_id   = r.principal_id
    JOIN sys.database_principals m ON rm.member_principal_id = m.principal_id
    WHERE r.name = N'qc_data_manager_role'
      AND m.name = N'db_qc_data_manager_user')
    ALTER ROLE [qc_data_manager_role] ADD MEMBER [db_qc_data_manager_user];

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'003_create_user_db_qc_data_manager.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'003_create_user_db_qc_data_manager.sql');
GO
