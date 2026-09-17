-- ============================================================================
-- Login SQL dedicado para el pipeline analitica-ontime (ICOLDATA01N)
-- ----------------------------------------------------------------------------
-- Alternativa recomendada a usar `sa` en config/.env.
--
-- `sa` es sysadmin: su clave, guardada en texto plano en el .env de la estación
-- que corre el pipeline, da control total sobre las ~79 bases del servidor. Este
-- script crea un login que solo puede hacer lo que el pipeline hace: leer y
-- escribir en core y staging de AnaliticaOperativa, nada más.
--
-- Cómo usarlo:
--   1. Reemplazar el valor de @clave por una clave nueva (no reutilizar la de sa).
--   2. Ejecutarlo con `sa` (o cualquier sysadmin) contra ICOLDATA01N.
--   3. En config/.env poner  SQL_USUARIO=etl_ontime  y esa clave en SQL_PASSWORD.
--   4. Validar con:  cd src && python -m utils.verificar_conexion
--
-- Es idempotente: si el login ya existe no lo recrea ni le cambia la clave.
-- ============================================================================

DECLARE @cuenta sysname       = N'etl_ontime';
DECLARE @clave  nvarchar(128) = N'CAMBIAR_ESTA_CLAVE';   -- <<< reemplazar antes de ejecutar
DECLARE @sql    nvarchar(max);

IF @clave = N'CAMBIAR_ESTA_CLAVE'
BEGIN
    RAISERROR('Reemplaza @clave por una clave real antes de ejecutar el script.', 16, 1);
    RETURN;
END

-- 1) Login a nivel de servidor -----------------------------------------------
-- CHECK_POLICY ON deja que la política de contraseñas de Windows aplique también
-- a este login. Sin caducidad porque es una cuenta de servicio: si expira, el
-- pipeline empieza a fallar de madrugada sin que nadie lo note.
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = @cuenta)
BEGIN
    SET @sql = N'CREATE LOGIN ' + QUOTENAME(@cuenta) + N'
                 WITH PASSWORD = ' + QUOTENAME(@clave, '''') + N',
                      DEFAULT_DATABASE = AnaliticaOperativa,
                      CHECK_EXPIRATION = OFF,
                      CHECK_POLICY = ON;';
    EXEC sys.sp_executesql @sql;
    PRINT 'Login creado: ' + @cuenta;
END
ELSE
    PRINT 'Login ya existía (clave sin cambios): ' + @cuenta;
GO

USE AnaliticaOperativa;
GO

DECLARE @cuenta sysname = N'etl_ontime';
DECLARE @sql    nvarchar(max);

-- 2) Usuario de base de datos ------------------------------------------------
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = @cuenta)
BEGIN
    SET @sql = N'CREATE USER ' + QUOTENAME(@cuenta) + N' FOR LOGIN ' + QUOTENAME(@cuenta) + N';';
    EXEC sys.sp_executesql @sql;
    PRINT 'Usuario creado en AnaliticaOperativa: ' + @cuenta;
END
ELSE
    PRINT 'Usuario ya existía en AnaliticaOperativa: ' + @cuenta;

-- 3) Permisos, acotados a los dos esquemas del pipeline ----------------------
-- Por esquema y no db_owner: la base es compartida con otras aplicaciones.
-- ALTER sobre staging es opcional: habilita el TRUNCATE TABLE de cada corrida. Sin él
-- la carga vacía con DELETE (ver carga.cargar_sql._vaciar_staging).
SET @sql = N'
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::core    TO ' + QUOTENAME(@cuenta) + N';
GRANT SELECT, INSERT, UPDATE, DELETE ON SCHEMA::staging TO ' + QUOTENAME(@cuenta) + N';
GRANT ALTER                          ON SCHEMA::staging TO ' + QUOTENAME(@cuenta) + N';';
EXEC sys.sp_executesql @sql;
PRINT 'Permisos otorgados sobre los esquemas core y staging';
GO

-- 4) Verificación ------------------------------------------------------------
-- Se evalúa suplantando al login del pipeline (EXECUTE AS): consultado como
-- sysadmin, HAS_PERMS_BY_NAME devolvería 1 siempre y no probaría nada.
-- Debe devolver 1 en las cuatro columnas.
EXECUTE AS USER = N'etl_ontime';

SELECT
    puede_leer_core        = HAS_PERMS_BY_NAME('core.HechosOnTime',          'OBJECT', 'SELECT'),
    puede_escribir_core    = HAS_PERMS_BY_NAME('core.HechosOnTime',          'OBJECT', 'INSERT'),
    puede_escribir_staging = HAS_PERMS_BY_NAME('staging.HechosOnTime_Stage', 'OBJECT', 'INSERT'),
    puede_truncar_staging  = HAS_PERMS_BY_NAME('staging.HechosOnTime_Stage', 'OBJECT', 'ALTER');

REVERT;
GO
