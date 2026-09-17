-- Migración al tipado estricto definido en src/utils/esquema.py
--
-- Contexto: la corrida del 2026-09-06 falló con
--   ('String data, right truncation: length 108 buffer 100', 'HY000')
-- porque una celda de Hora_Cita traía 54 caracteres de texto libre
-- ('Lunes a sabado 6:00 am a 11:00 am - 1:00 pm a 3:00 pm') en una columna
-- NVARCHAR(50). Con fast_executemany un solo valor pasado de largo aborta el lote
-- completo, así que una celda bloqueó 34.323 filas.
--
-- Esta migración alinea la base con el contrato de tipos acordado con negocio:
-- anchos definitivos por columna y Hora_Cita convertida a TIME.
--
-- Cambios que aplica:
--   [Cliente]: nvarchar(200) -> nvarchar(50)
--   [Cl/ Movimiento]: nvarchar(50) -> nvarchar(10)
--   [Cl Doc FWO]: nvarchar(50) -> nvarchar(10)
--   [Ciudad Origen]: nvarchar(300) -> nvarchar(50)
--   [Centro Origen]: nvarchar(150) -> nvarchar(50)
--   [Destinatario]: nvarchar(300) -> nvarchar(100)
--   [Ciudad Destino]: nvarchar(300) -> nvarchar(50)
--   [Centro Destino]: nvarchar(150) -> nvarchar(50)
--   [Ultima Ubicación]: nvarchar(200) -> nvarchar(50)
--   [Hora_Cita]: nvarchar -> time  (conversión especial)
--   [Tipo Producto]: nvarchar(300) -> nvarchar(100)
--   [ESTATUS]: nvarchar(300) -> nvarchar(100)
--   [Novedad / Causal]: nvarchar(300) -> nvarchar(100)
--   [DOCUWARE]: nvarchar(300) -> nvarchar(50)
--   [RESPONSABLE]: nvarchar(200) -> nvarchar(50)
--   [Origen_Destino]: nvarchar(200) -> nvarchar(50)
--   [source_file]: nvarchar(300) -> nvarchar(100)
--   [source_path]: nvarchar(500) -> nvarchar(300)
--   [source_sheet]: nvarchar(300) -> nvarchar(100)
--
-- Es idempotente: cada paso verifica el estado actual antes de actuar, así que se
-- puede volver a ejecutar sin riesgo si queda a medias.
-- No borra filas: el histórico de core.HechosOnTime se preserva completo.

USE AnaliticaOperativa;
GO

SET XACT_ABORT ON;
GO

/* ------------------------------------------------------------------------
   PASO 0 - Verificación previa
   Aborta si algún dato actual no cabe en el ancho nuevo. Preferimos no
   ejecutar nada antes que truncar histórico en silencio.
   ------------------------------------------------------------------------ */
IF EXISTS (
    SELECT 1 FROM (
    SELECT 'Cliente' AS columna, 50 AS ancho_nuevo, MAX(LEN([Cliente])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cliente])) > 50
    UNION ALL
    SELECT 'Cl/ Movimiento' AS columna, 10 AS ancho_nuevo, MAX(LEN([Cl/ Movimiento])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cl/ Movimiento])) > 10
    UNION ALL
    SELECT 'Cl Doc FWO' AS columna, 10 AS ancho_nuevo, MAX(LEN([Cl Doc FWO])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cl Doc FWO])) > 10
    UNION ALL
    SELECT 'Ciudad Origen' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ciudad Origen])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ciudad Origen])) > 50
    UNION ALL
    SELECT 'Centro Origen' AS columna, 50 AS ancho_nuevo, MAX(LEN([Centro Origen])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Centro Origen])) > 50
    UNION ALL
    SELECT 'Destinatario' AS columna, 100 AS ancho_nuevo, MAX(LEN([Destinatario])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Destinatario])) > 100
    UNION ALL
    SELECT 'Ciudad Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ciudad Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ciudad Destino])) > 50
    UNION ALL
    SELECT 'Centro Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Centro Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Centro Destino])) > 50
    UNION ALL
    SELECT 'Ultima Ubicación' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ultima Ubicación])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ultima Ubicación])) > 50
    UNION ALL
    SELECT 'Tipo Producto' AS columna, 100 AS ancho_nuevo, MAX(LEN([Tipo Producto])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Tipo Producto])) > 100
    UNION ALL
    SELECT 'ESTATUS' AS columna, 100 AS ancho_nuevo, MAX(LEN([ESTATUS])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([ESTATUS])) > 100
    UNION ALL
    SELECT 'Novedad / Causal' AS columna, 100 AS ancho_nuevo, MAX(LEN([Novedad / Causal])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Novedad / Causal])) > 100
    UNION ALL
    SELECT 'DOCUWARE' AS columna, 50 AS ancho_nuevo, MAX(LEN([DOCUWARE])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([DOCUWARE])) > 50
    UNION ALL
    SELECT 'RESPONSABLE' AS columna, 50 AS ancho_nuevo, MAX(LEN([RESPONSABLE])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([RESPONSABLE])) > 50
    UNION ALL
    SELECT 'Origen_Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Origen_Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Origen_Destino])) > 50
    UNION ALL
    SELECT 'source_file' AS columna, 100 AS ancho_nuevo, MAX(LEN([source_file])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_file])) > 100
    UNION ALL
    SELECT 'source_path' AS columna, 300 AS ancho_nuevo, MAX(LEN([source_path])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_path])) > 300
    UNION ALL
    SELECT 'source_sheet' AS columna, 100 AS ancho_nuevo, MAX(LEN([source_sheet])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_sheet])) > 100
    ) AS excedidos
)
BEGIN
    SELECT * FROM (
    SELECT 'Cliente' AS columna, 50 AS ancho_nuevo, MAX(LEN([Cliente])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cliente])) > 50
    UNION ALL
    SELECT 'Cl/ Movimiento' AS columna, 10 AS ancho_nuevo, MAX(LEN([Cl/ Movimiento])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cl/ Movimiento])) > 10
    UNION ALL
    SELECT 'Cl Doc FWO' AS columna, 10 AS ancho_nuevo, MAX(LEN([Cl Doc FWO])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Cl Doc FWO])) > 10
    UNION ALL
    SELECT 'Ciudad Origen' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ciudad Origen])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ciudad Origen])) > 50
    UNION ALL
    SELECT 'Centro Origen' AS columna, 50 AS ancho_nuevo, MAX(LEN([Centro Origen])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Centro Origen])) > 50
    UNION ALL
    SELECT 'Destinatario' AS columna, 100 AS ancho_nuevo, MAX(LEN([Destinatario])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Destinatario])) > 100
    UNION ALL
    SELECT 'Ciudad Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ciudad Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ciudad Destino])) > 50
    UNION ALL
    SELECT 'Centro Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Centro Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Centro Destino])) > 50
    UNION ALL
    SELECT 'Ultima Ubicación' AS columna, 50 AS ancho_nuevo, MAX(LEN([Ultima Ubicación])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Ultima Ubicación])) > 50
    UNION ALL
    SELECT 'Tipo Producto' AS columna, 100 AS ancho_nuevo, MAX(LEN([Tipo Producto])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Tipo Producto])) > 100
    UNION ALL
    SELECT 'ESTATUS' AS columna, 100 AS ancho_nuevo, MAX(LEN([ESTATUS])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([ESTATUS])) > 100
    UNION ALL
    SELECT 'Novedad / Causal' AS columna, 100 AS ancho_nuevo, MAX(LEN([Novedad / Causal])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Novedad / Causal])) > 100
    UNION ALL
    SELECT 'DOCUWARE' AS columna, 50 AS ancho_nuevo, MAX(LEN([DOCUWARE])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([DOCUWARE])) > 50
    UNION ALL
    SELECT 'RESPONSABLE' AS columna, 50 AS ancho_nuevo, MAX(LEN([RESPONSABLE])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([RESPONSABLE])) > 50
    UNION ALL
    SELECT 'Origen_Destino' AS columna, 50 AS ancho_nuevo, MAX(LEN([Origen_Destino])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([Origen_Destino])) > 50
    UNION ALL
    SELECT 'source_file' AS columna, 100 AS ancho_nuevo, MAX(LEN([source_file])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_file])) > 100
    UNION ALL
    SELECT 'source_path' AS columna, 300 AS ancho_nuevo, MAX(LEN([source_path])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_path])) > 300
    UNION ALL
    SELECT 'source_sheet' AS columna, 100 AS ancho_nuevo, MAX(LEN([source_sheet])) AS largo_real FROM core.HechosOnTime HAVING MAX(LEN([source_sheet])) > 100
    ) AS excedidos;
    RAISERROR('MIGRACION ABORTADA: hay datos que exceden los anchos nuevos (ver resultado).', 16, 1);
END
GO

/* ------------------------------------------------------------------------
   PASO 1 - Respaldo de Hora_Cita antes de convertirla a TIME
   Se acordó con negocio que el texto libre (rangos, notas como 'TURNO' o
   'LLAMAR MEDIA HORA ANTES') no se conserva en el modelo. Se guarda igual
   una copia por si hay que auditar qué decía antes.
   ------------------------------------------------------------------------ */
IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HechosOnTime_HoraCita_Respaldo'
               AND schema_id = SCHEMA_ID('core'))
BEGIN
    SELECT [Cliente], [Fecha de Factura], [Factura], [Hora_Cita] AS Hora_Cita_Original,
           SYSDATETIME() AS FechaRespaldo
    INTO core.HechosOnTime_HoraCita_Respaldo
    FROM core.HechosOnTime
    WHERE [Hora_Cita] IS NOT NULL;
END
GO

/* ------------------------------------------------------------------------
   PASO 2 - Quitar PK e índice
   Necesario porque [Cliente] es parte de la llave primaria y cambia de ancho.
   ------------------------------------------------------------------------ */
IF EXISTS (SELECT 1 FROM sys.indexes
           WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'IX_HechosOnTime_Fecha')
    DROP INDEX IX_HechosOnTime_Fecha ON core.HechosOnTime;
GO

IF EXISTS (SELECT 1 FROM sys.key_constraints
           WHERE parent_object_id = OBJECT_ID('core.HechosOnTime') AND name = 'PK_HechosOnTime')
    ALTER TABLE core.HechosOnTime DROP CONSTRAINT PK_HechosOnTime;
GO

/* ------------------------------------------------------------------------
   PASO 3 - Ajustar anchos de las columnas de texto
   ------------------------------------------------------------------------ */
IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Cliente')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Cliente] NVARCHAR(50) NOT NULL;

IF 10 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Cl/ Movimiento')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Cl/ Movimiento] NVARCHAR(10) NULL;

IF 10 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Cl Doc FWO')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Cl Doc FWO] NVARCHAR(10) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Ciudad Origen')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Ciudad Origen] NVARCHAR(50) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Centro Origen')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Centro Origen] NVARCHAR(50) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Destinatario')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Destinatario] NVARCHAR(100) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Ciudad Destino')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Ciudad Destino] NVARCHAR(50) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Centro Destino')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Centro Destino] NVARCHAR(50) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Ultima Ubicación')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Ultima Ubicación] NVARCHAR(50) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Tipo Producto')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Tipo Producto] NVARCHAR(100) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'ESTATUS')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [ESTATUS] NVARCHAR(100) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Novedad / Causal')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Novedad / Causal] NVARCHAR(100) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'DOCUWARE')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [DOCUWARE] NVARCHAR(50) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'RESPONSABLE')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [RESPONSABLE] NVARCHAR(50) NULL;

IF 50 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'Origen_Destino')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [Origen_Destino] NVARCHAR(50) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'source_file')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [source_file] NVARCHAR(100) NULL;

IF 300 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'source_path')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [source_path] NVARCHAR(300) NULL;

IF 100 <> (SELECT max_length/2 FROM sys.columns
                 WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'source_sheet')
    ALTER TABLE core.HechosOnTime ALTER COLUMN [source_sheet] NVARCHAR(100) NULL;
GO

/* ------------------------------------------------------------------------
   PASO 4 - Hora_Cita: NVARCHAR -> TIME
   No se puede con un ALTER directo porque la mayoría de los valores no son
   horas válidas para SQL Server. Se convierte a una columna nueva:
     - '7:00 AM', '18:30'  -> conversión directa
     - 0.375 (fracción de día de Excel) -> se multiplica por 24h
     - texto libre -> NULL
   ------------------------------------------------------------------------ */
IF EXISTS (SELECT 1 FROM sys.columns c JOIN sys.types t ON t.user_type_id = c.user_type_id
           WHERE c.object_id = OBJECT_ID('core.HechosOnTime')
             AND c.name = 'Hora_Cita' AND t.name = 'nvarchar')
BEGIN
    ALTER TABLE core.HechosOnTime ADD [Hora_Cita_Nueva] TIME NULL;
END
GO

-- Va en SQL dinámico a propósito: SQL Server compila el lote entero antes de evaluar
-- el IF, así que si estas sentencias fueran estáticas, al re-ejecutar el script (con
-- Hora_Cita_Nueva ya renombrada) fallaría la compilación por columna inexistente,
-- aunque la guarda las fuera a saltar. Dentro de sp_executesql solo se compila si corre.
IF COL_LENGTH('core.HechosOnTime', 'Hora_Cita_Nueva') IS NOT NULL
BEGIN
    -- NULLIF descarta la medianoche exacta: sale de celdas vacías o de un cero, no de
    -- una cita real a las 00:00. Es la misma regla de transformacion/tipado.py, para
    -- que la base y el pipeline no discrepen.
    EXEC sp_executesql N'
        UPDATE core.HechosOnTime
        SET [Hora_Cita_Nueva] = NULLIF(CASE
            WHEN TRY_CONVERT(TIME, [Hora_Cita]) IS NOT NULL
                THEN TRY_CONVERT(TIME, [Hora_Cita])
            WHEN TRY_CONVERT(FLOAT, [Hora_Cita]) IS NOT NULL
                 AND TRY_CONVERT(FLOAT, [Hora_Cita]) > 0
                 AND TRY_CONVERT(FLOAT, [Hora_Cita]) < 1
                THEN DATEADD(
                    SECOND,
                    CAST(ROUND(TRY_CONVERT(FLOAT, [Hora_Cita]) * 86400, 0) AS INT) % 86400,
                    CAST(''00:00:00'' AS TIME))
            ELSE NULL
        END, CAST(''00:00:00'' AS TIME))
        WHERE [Hora_Cita] IS NOT NULL;';

    EXEC sp_executesql N'ALTER TABLE core.HechosOnTime DROP COLUMN [Hora_Cita];';
    EXEC sp_rename 'core.HechosOnTime.Hora_Cita_Nueva', 'Hora_Cita', 'COLUMN';
END
GO

/* ------------------------------------------------------------------------
   PASO 5 - Restituir PK e índice
   ------------------------------------------------------------------------ */
IF NOT EXISTS (SELECT 1 FROM sys.key_constraints
               WHERE parent_object_id = OBJECT_ID('core.HechosOnTime') AND name = 'PK_HechosOnTime')
    ALTER TABLE core.HechosOnTime
        ADD CONSTRAINT PK_HechosOnTime PRIMARY KEY CLUSTERED
            ([Cliente], [Fecha de Factura], [Factura]);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes
               WHERE object_id = OBJECT_ID('core.HechosOnTime') AND name = 'IX_HechosOnTime_Fecha')
    CREATE NONCLUSTERED INDEX IX_HechosOnTime_Fecha ON core.HechosOnTime ([Fecha de Factura]);
GO

/* ------------------------------------------------------------------------
   PASO 6 - Staging se recrea desde cero
   Es una tabla desechable (se trunca en cada corrida), así que no hay dato
   que preservar: se elimina y se vuelve a crear con el esquema nuevo
   ejecutando sql/03_crear_tablas_staging.sql.
   ------------------------------------------------------------------------ */
DROP TABLE IF EXISTS staging.HechosOnTime_Stage;
GO
PRINT 'Migracion completada. Ahora ejecutar sql/03_crear_tablas_staging.sql para recrear staging.';
GO
