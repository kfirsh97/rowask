-- Creates a read-only login for the SQL agent.
-- Run once as `sa`, after 01_schema.sql and 02_seed_data.sql.
-- The agent can only SELECT: it cannot insert, update, delete or drop anything.

USE master;
GO
CREATE LOGIN agent_reader WITH PASSWORD = 'Reader!Passw0rd';  -- change this
GO

USE Porsche;
GO
CREATE USER agent_reader FOR LOGIN agent_reader;
ALTER ROLE db_datareader ADD MEMBER agent_reader;
GO
