-- ============================================================================
-- Permisos del pipeline analitica-ontime en el servidor corporativo ICOLDATA01N
-- ----------------------------------------------------------------------------
-- Ejecutar UNA sola vez, con una cuenta sysadmin (el DBA), sobre ICOLDATA01N.
--
-- El pipeline se conecta por autenticación de Windows: entra con la cuenta de
-- dominio que ejecuta el proceso (hoy la tarea programada corre como
-- ICOLTRANS\aguzman). Esa cuenta necesita:
--   * un login en el servidor,
--   * un usuario en la base AnaliticaOperativa,
--   * lectura/escritura sobre core y staging.
--
-- El ALTER sobre staging es opcional: habilita vaciar la tabla con TRUNCATE TABLE.
-- Sin él la carga la vacía con DELETE, que ya cubre db_datawriter (ver
-- carga.cargar_sql._vaciar_staging). Se otorga acá porque no cuesta nada cuando se
-- tiene control del esquema.
--
-- Si más adelante la tarea programada pasa a una cuenta de servicio, cambiar el
-- valor de @cuenta y volver a ejecutar el script: es idempotente.
-- ============================================================================

DECLARE @cuenta sysname = N'ICOLTRANS\aguzman';
DECLARE @sql nvarchar(max);

-- 1) Login a nivel de servidor -----------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = @cuenta)
BEGIN
    SET @sql = N'CREATE LOGIN ' + QUOTENAME(@cuenta) + N' FROM WINDOWS;';
    EXEC sys.sp_executesql @sql;
    PRINT 'Login creado: ' + @cuenta;
END
ELSE
    PRINT 'Login ya existía: ' + @cuenta;
GO

USE AnaliticaOperativa;
GO

DECLARE @cuenta sysname = N'ICOLTRANS\aguzman';
DECLARE @sql nvarchar(max);

-- 2) Usuario de base de datos ------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @cuenta)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@cuenta) + N' FOR LOGIN ' + QUOTENAME(@cuenta) + N';';
    EXEC sys.sp_executesql @sql;
    PRINT 'Usuario creado en AnaliticaOperativa: ' + @cuenta;
END
ELSE
    PRINT 'Usuario ya existía en AnaliticaOperativa: ' + @cuenta;

-- 3) Permisos sobre los esquemas del pipeline --------------------------------
-- Se otorgan por esquema (no db_owner) para que el alcance quede acotado a las
-- dos áreas del pipeline y no a toda la base, que es compartida.
SET @sql = N'
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::core    TO ' + QUOTENAME(@cuenta) + N';
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::staging TO ' + QUOTENAME(@cuenta) + N';
-- ALTER sobre staging: habilita el TRUNCATE TABLE de cada corrida.
GRANT ALTER ON SCHEMA::staging TO ' + QUOTENAME(@cuenta) + N';';
EXEC sys.sp_executesql @sql;
PRINT 'Permisos otorgados sobre los esquemas core y staging';
GO

-- 4) Verificación ------------------------------------------------------------
-- Se evalúa suplantando a la cuenta del pipeline (EXECUTE AS): consultado como
-- sysadmin, HAS_PERMS_BY_NAME devolvería 1 siempre y no probaría nada.
-- Debe devolver 1 en las cuatro columnas.
-- Nota: requiere que las tablas ya existan (sql/01..04); si no, devuelve NULL.
EXECUTE AS USER = N'ICOLTRANS\aguzman';

SELECT
    puede_leer_core        = HAS_PERMS_BY_NAME('core.HechosOnTime',          'OBJECT', 'SELECT'),
    puede_escribir_core    = HAS_PERMS_BY_NAME('core.HechosOnTime',          'OBJECT', 'INSERT'),
    puede_escribir_staging = HAS_PERMS_BY_NAME('staging.HechosOnTime_Stage', 'OBJECT', 'INSERT'),
    puede_truncar_staging  = HAS_PERMS_BY_NAME('staging.HechosOnTime_Stage', 'OBJECT', 'ALTER');

REVERT;
GO
