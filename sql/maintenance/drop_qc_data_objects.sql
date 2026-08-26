/* drop_qc_data_objects.sql
   ---------------------------------------------------------------------------
   Empty the qc_data schema so the migrations can be run again from scratch.

   WHY THIS EXISTS. Nothing is in production yet, so a design mistake is better
   corrected in the file that made it than papered over by a migration that
   undoes it. Once this schema is live that stops being true and this script
   stops being usable, which is why it lives in maintenance and not in
   migrations: it is not part of the history, it erases it.

   WHAT GOES
     every procedure in qc_data
     every view in qc_data
     every table in qc_data except schema_migrations
     the ledger rows for 004 onwards

   WHAT STAYS
     the schema itself, so the grants in 002 survive
     qc_data_manager_role and db_qc_data_manager_user, so the password in
       config/db_connection.json keeps working
     schema_migrations, and its rows for 001, 002 and 003, which are still true
     dbo.MetaData and everything else outside this schema

   dbo IS NEVER TOUCHED. Not by exception, by construction: every statement
   below is built from sys objects filtered on SCHEMA_ID('qc_data'), so there is
   no path from here to another schema. The MetaData stand-in on the test
   database is left exactly as it is.

   TEST ONLY. Run anywhere else and it throws before doing anything. This is the
   one script in the project that destroys data, so the guard names a single
   database rather than the usual pair.

   AFTER RUNNING: apply 004 onwards again, then reload with
   data/ETL/04_load_to_database.py.
   --------------------------------------------------------------------------- */

IF DB_NAME() <> N'dataloggingTest'
    THROW 50000, N'Refusing to run: this drops data and only dataloggingTest may be dropped.', 1;

IF OBJECT_ID(N'[qc_data].[schema_migrations]', N'U') IS NULL
    THROW 50000, N'Nothing to drop: qc_data.schema_migrations does not exist.', 1;

SET XACT_ABORT ON;

DECLARE @sql NVARCHAR(MAX);
DECLARE @procedures INT = 0, @views INT = 0, @tables INT = 0;

BEGIN TRANSACTION;

    /* ---- procedures ------------------------------------------------------ */
    SELECT @procedures = COUNT(*)
      FROM sys.procedures WHERE schema_id = SCHEMA_ID(N'qc_data');

    SELECT @sql = STRING_AGG(
               CONVERT(NVARCHAR(MAX),
                       N'DROP PROCEDURE [qc_data].[' + [name] + N'];'), NCHAR(10))
      FROM sys.procedures WHERE schema_id = SCHEMA_ID(N'qc_data');
    IF @sql IS NOT NULL EXEC (@sql);

    /* ---- views ----------------------------------------------------------- */
    SELECT @views = COUNT(*) FROM sys.views WHERE schema_id = SCHEMA_ID(N'qc_data');

    SET @sql = NULL;
    SELECT @sql = STRING_AGG(
               CONVERT(NVARCHAR(MAX),
                       N'DROP VIEW [qc_data].[' + [name] + N'];'), NCHAR(10))
      FROM sys.views WHERE schema_id = SCHEMA_ID(N'qc_data');
    IF @sql IS NOT NULL EXEC (@sql);

    /* ---- foreign keys, before the tables they point between --------------
       Dropped first so the tables can go in any order. The alternative is
       working out the dependency order by hand and keeping it correct as
       tables are added. */
    SET @sql = NULL;
    SELECT @sql = STRING_AGG(
               CONVERT(NVARCHAR(MAX),
                       N'ALTER TABLE [qc_data].[' + OBJECT_NAME(parent_object_id)
                       + N'] DROP CONSTRAINT [' + [name] + N'];'), NCHAR(10))
      FROM sys.foreign_keys WHERE schema_id = SCHEMA_ID(N'qc_data');
    IF @sql IS NOT NULL EXEC (@sql);

    /* ---- tables, except the ledger --------------------------------------- */
    SELECT @tables = COUNT(*)
      FROM sys.tables
     WHERE schema_id = SCHEMA_ID(N'qc_data') AND [name] <> N'schema_migrations';

    SET @sql = NULL;
    SELECT @sql = STRING_AGG(
               CONVERT(NVARCHAR(MAX),
                       N'DROP TABLE [qc_data].[' + [name] + N'];'), NCHAR(10))
      FROM sys.tables
     WHERE schema_id = SCHEMA_ID(N'qc_data') AND [name] <> N'schema_migrations';
    IF @sql IS NOT NULL EXEC (@sql);

    /* ---- the ledger, back to what is still true --------------------------
       001 to 003 built the schema, the role and the user, and those are all
       still standing. Everything after them described objects that no longer
       exist, so leaving those rows would tell the loader the tables are there. */
    DELETE FROM [qc_data].[schema_migrations]
     WHERE [filename] NOT IN (N'001_create_schema_and_ledger.sql',
                              N'002_create_role_qc_data_manager.sql',
                              N'003_create_user_db_qc_data_manager.sql');

COMMIT TRANSACTION;

PRINT CONCAT(N'Dropped ', @procedures, N' procedure(s), ', @views,
             N' view(s) and ', @tables, N' table(s) from qc_data.');
PRINT N'The schema, the role, the user and dbo are untouched.';
PRINT N'Run 004 onwards again, then data/ETL/04_load_to_database.py.';
GO
