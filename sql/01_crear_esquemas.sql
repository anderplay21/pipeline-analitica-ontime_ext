-- Crea la base de datos y los esquemas staging/core
-- Ejecutar una sola vez, como paso inicial de instalación.
--
-- En el servidor corporativo ICOLDATA01N la base AnaliticaOperativa ya existe, así que
-- el CREATE DATABASE no se dispara. Crear los esquemas sí requiere permisos elevados
-- (db_owner o CREATE SCHEMA), por lo que este script y los siguientes (02..04) los
-- ejecuta el DBA, no la cuenta del pipeline. Los permisos de la cuenta del pipeline se
-- otorgan aparte, en sql/06_permisos_pipeline.sql.

IF NOT EXISTS (SELECT 1 FROM sys.databases WHERE name = 'AnaliticaOperativa')
BEGIN
    CREATE DATABASE AnaliticaOperativa;
END
GO

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'staging')
    EXEC('CREATE SCHEMA staging');
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = 'core')
    EXEC('CREATE SCHEMA core');
GO
