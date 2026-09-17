-- Tabla de control (watermark): registra cada corrida del pipeline
-- Da trazabilidad auditable de qué ventana se procesó, cuántas filas se tocaron y el resultado

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'ETL_Control' AND schema_id = SCHEMA_ID('core'))
BEGIN
    CREATE TABLE core.ETL_Control (
        ProcesoID         INT IDENTITY(1,1) PRIMARY KEY,
        NombreProceso     NVARCHAR(100) NOT NULL,
        UltimaEjecucion   DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
        FechaDesde        DATE          NOT NULL,
        FechaHasta        DATE          NOT NULL,
        FilasStaging      INT           NULL,
        FilasInsertadas   INT           NULL,
        FilasActualizadas INT           NULL,
        Estado            NVARCHAR(20)  NOT NULL,
        Detalle           NVARCHAR(MAX) NULL
    );
END
GO
