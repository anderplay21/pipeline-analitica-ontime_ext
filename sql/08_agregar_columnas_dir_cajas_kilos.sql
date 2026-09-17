-- ============================================================================
-- Agrega tres columnas nuevas a la tabla de hechos On Time
-- ----------------------------------------------------------------------------
-- Ejecutar contra ICOLDATA01N, base AnaliticaOperativa, con una cuenta que pueda
-- hacer DDL (sa o el DBA). La cuenta del pipeline (`automatizacion`) solo tiene
-- db_datareader/db_datawriter y no puede alterar tablas.
--
-- Qué agrega, a core.HechosOnTime y a staging.HechosOnTime_Stage:
--
--   [Dir/ Cliente]  NVARCHAR(200) NULL
--   [Cajas]         INT           NULL
--   [Kilos]         DECIMAL(12,2) NULL
--
-- Las tres son NULL a propósito. Declararlas NOT NULL fallaría sobre las filas ya
-- cargadas, y haría que el pipeline descartara toda fila sin esos datos (la capa de
-- tipado descarta las filas a las que les falte una columna obligatoria).
--
-- ORDEN DE EJECUCIÓN: este script va PRIMERO, antes de desplegar el código nuevo.
-- Las columnas nuevas son nulables y tanto el INSERT a staging como el MERGE nombran
-- sus columnas una por una, así que el código actual sigue funcionando sin cambios con
-- las columnas ya agregadas. Eso deja una ventana segura: si el despliegue del código
-- queda a medias, la corrida de las 8:00 a.m. no se rompe.
--
-- Es idempotente: cada columna se verifica antes de agregarse, así que volver a
-- ejecutarlo no falla ni duplica nada. No borra ni modifica filas existentes: las
-- columnas nuevas quedan en NULL en el histórico ya cargado, por decisión de negocio
-- (la ventana móvil solo reprocesa mes actual + anterior, así que lo anterior no se
-- rellena).
-- ============================================================================

USE AnaliticaOperativa;
GO

SET XACT_ABORT ON;
GO

-- ---------------------------------------------------------------- core.HechosOnTime

IF COL_LENGTH('core.HechosOnTime', 'Dir/ Cliente') IS NULL
BEGIN
    ALTER TABLE core.HechosOnTime ADD [Dir/ Cliente] NVARCHAR(200) NULL;
    PRINT 'Anderson agregro core.HechosOnTime: agregada [Dir/ Cliente]';
END
ELSE
    PRINT 'core.HechosOnTime: [Dir/ Cliente] ya existia';
GO

IF COL_LENGTH('core.HechosOnTime', 'Cajas') IS NULL
BEGIN
    ALTER TABLE core.HechosOnTime ADD [Cajas] INT NULL;
    PRINT 'core.HechosOnTime: agregada [Cajas]';
END
ELSE
    PRINT 'core.HechosOnTime: [Cajas] ya existia';
GO

IF COL_LENGTH('core.HechosOnTime', 'Kilos') IS NULL
BEGIN
    ALTER TABLE core.HechosOnTime ADD [Kilos] DECIMAL(12,2) NULL;
    PRINT 'core.HechosOnTime: agregada [Kilos]';
END
ELSE
    PRINT 'core.HechosOnTime: [Kilos] ya existia';
GO

-- --------------------------------------------------- staging.HechosOnTime_Stage
-- Staging es desechable (se vacía en cada corrida), pero se altera en vez de
-- recrearse para que este script no dependa de ejecutar además sql/03.

IF COL_LENGTH('staging.HechosOnTime_Stage', 'Dir/ Cliente') IS NULL
BEGIN
    ALTER TABLE staging.HechosOnTime_Stage ADD [Dir/ Cliente] NVARCHAR(200) NULL;
    PRINT 'staging.HechosOnTime_Stage: agregada [Dir/ Cliente]';
END
ELSE
    PRINT 'staging.HechosOnTime_Stage: [Dir/ Cliente] ya existia';
GO

IF COL_LENGTH('staging.HechosOnTime_Stage', 'Cajas') IS NULL
BEGIN
    ALTER TABLE staging.HechosOnTime_Stage ADD [Cajas] INT NULL;
    PRINT 'staging.HechosOnTime_Stage: agregada [Cajas]';
END
ELSE
    PRINT 'staging.HechosOnTime_Stage: [Cajas] ya existia';
GO

IF COL_LENGTH('staging.HechosOnTime_Stage', 'Kilos') IS NULL
BEGIN
    ALTER TABLE staging.HechosOnTime_Stage ADD [Kilos] DECIMAL(12,2) NULL;
    PRINT 'staging.HechosOnTime_Stage: agregada [Kilos]';
END
ELSE
    PRINT 'staging.HechosOnTime_Stage: [Kilos] ya existia';
GO

-- ------------------------------------------------------------------ verificación
-- Debe devolver 6 filas (3 columnas x 2 tablas), todas es_nulable = 1.
-- Para NVARCHAR, max_length viene en bytes: 400 = NVARCHAR(200).

SELECT
    tabla        = SCHEMA_NAME(t.schema_id) + '.' + t.name,
    columna      = c.name,
    tipo         = TYPE_NAME(c.user_type_id),
    max_length   = c.max_length,
    [precision]  = c.precision,
    escala       = c.scale,
    es_nulable   = c.is_nullable
FROM sys.columns c
JOIN sys.tables  t ON t.object_id = c.object_id
WHERE t.object_id IN (OBJECT_ID('core.HechosOnTime'), OBJECT_ID('staging.HechosOnTime_Stage'))
  AND c.name IN ('Dir/ Cliente', 'Cajas', 'Kilos')
ORDER BY tabla, columna;
GO
