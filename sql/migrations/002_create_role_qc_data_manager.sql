/* 002_create_role_qc_data_manager.sql
   ---------------------------------------------------------------------------
   The role the application connects as. Granted at schema level on purpose, so
   tables added by later migrations are covered without revisiting this file.

   READS DIRECTLY, WRITES ONLY THROUGH PROCEDURES. The role has SELECT and
   EXECUTE, and deliberately no INSERT, UPDATE or DELETE. A bug in the
   application, or somebody pointing a query tool at the credential, cannot
   change or remove a row: the only way data moves is a qc_data.usp_* procedure
   that was written to move it that way.

   That works through ownership chaining. A procedure in [qc_data] and the
   tables it touches share one owner, so executing it does not re-check the
   caller's rights on those tables. Two things break the chain and must be
   avoided when writing one:
     * dynamic SQL (EXEC / sp_executesql), which is checked against the caller.
       A procedure that needs it has to say EXECUTE AS OWNER.
     * touching a table in another schema with a different owner.

   dbo.MetaData is granted SELECT only, and only where it exists, which today
   means dataloggingDev: production metadata is written by the plating line, and
   this application must never change it.

   The ETL loader is NOT this user. data/ETL/04_load_to_database.py merges into
   the tables directly, so it signs in with an admin credential, the same one
   that runs these migrations.
   --------------------------------------------------------------------------- */

IF DB_NAME() NOT IN (N'dataloggingDev', N'dataloggingTest')
    THROW 50000, N'Refusing to run: not an expected database.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Run 001 first (creates the schema + ledger).', 1;

IF NOT EXISTS (SELECT 1 FROM sys.database_principals
               WHERE name = N'qc_data_manager_role' AND type = 'R')
    CREATE ROLE [qc_data_manager_role];

/* GRANT and REVOKE are both idempotent, so these just run. The REVOKE also
   repairs a database where an earlier copy of this file granted the three write
   permissions before the rule above was settled. */
REVOKE INSERT, UPDATE, DELETE ON SCHEMA::[qc_data] FROM [qc_data_manager_role];

GRANT SELECT  ON SCHEMA::[qc_data] TO [qc_data_manager_role];
GRANT EXECUTE ON SCHEMA::[qc_data] TO [qc_data_manager_role];

/* Only dataloggingDev carries the production metadata, so granting it anywhere
   else fails on an object that is not there. Checked by presence as well as by
   name, so the day it lands on another database this file needs no edit. */
IF OBJECT_ID(N'[dbo].[MetaData]') IS NOT NULL
    GRANT SELECT ON OBJECT::[dbo].[MetaData] TO [qc_data_manager_role];
ELSE
    PRINT N'No dbo.MetaData here, SELECT on it was not granted.';

IF NOT EXISTS (SELECT 1 FROM [qc_data].[schema_migrations]
               WHERE filename = N'002_create_role_qc_data_manager.sql')
    INSERT INTO [qc_data].[schema_migrations] (filename)
    VALUES (N'002_create_role_qc_data_manager.sql');
GO
