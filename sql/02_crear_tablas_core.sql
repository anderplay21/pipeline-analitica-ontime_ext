-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- Tabla Core: histórico completo del consolidado On Time, nunca se trunca.
-- Llave de negocio: Cliente + Fecha de Factura + Factura.

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HechosOnTime' AND schema_id = SCHEMA_ID('core'))
BEGIN
    CREATE TABLE core.HechosOnTime (
        [Cliente]                      NVARCHAR(50)   NOT NULL,
        [Cl/ Movimiento]               NVARCHAR(10)   NULL,
        [Cl Doc FWO]                   NVARCHAR(10)   NULL,
        [Fecha de Factura]             DATE           NOT NULL,
        [Ciudad Origen]                NVARCHAR(50)   NULL,
        [Centro Origen]                NVARCHAR(50)   NULL,
        [Destinatario]                 NVARCHAR(100)  NULL,
        [Ciudad Destino]               NVARCHAR(50)   NULL,
        [Centro Destino]               NVARCHAR(50)   NULL,
        [Factura]                      NVARCHAR(50)   NOT NULL,
        [Carta Porte]                  NVARCHAR(50)   NULL,
        [Orden de Compra]              NVARCHAR(50)   NULL,
        [Orden de Pedido]              NVARCHAR(50)   NULL,
        [Remision]                     NVARCHAR(50)   NULL,
        [Ultima Ubicación]             NVARCHAR(50)   NULL,
        [Fecha Programada De Entrega]  DATE           NULL,
        [Hora_Cita]                    TIME           NULL,
        [Tipo Producto]                NVARCHAR(100)  NULL,
        [ESTATUS]                      NVARCHAR(100)  NULL,
        [Novedad / Causal]             NVARCHAR(100)  NULL,
        [FECHA ENTREGA]                DATE           NULL,
        [FECHA DE CITA]                DATE           NULL,
        [DOCUWARE]                     NVARCHAR(50)   NULL,
        [Lead Time]                    INT            NULL,
        [Fecha Estimada Entrega]       DATE           NULL,
        [RESPONSABLE]                  NVARCHAR(50)   NULL,
        [Origen_Destino]               NVARCHAR(50)   NULL,
        [Nombre del mes]               NVARCHAR(20)   NULL,
        [ON TIME ACIDO]                NVARCHAR(50)   NULL,
        [ON TIME JUSTIFICADO]          NVARCHAR(50)   NULL,
        [Dir/ Cliente]                 NVARCHAR(200)  NULL,
        [Cajas]                        INT            NULL,
        [Kilos]                        DECIMAL(12,2)  NULL,
        [source_file]                  NVARCHAR(100)  NULL,
        [source_path]                  NVARCHAR(300)  NULL,
        [source_sheet]                 NVARCHAR(100)  NULL,
        -- columnas de control técnico:
        [RowHash]                      BINARY(32)     NOT NULL,
        [FechaCargaInicial]            DATETIME2      NOT NULL DEFAULT SYSDATETIME(),
        [FechaUltimaActualizacion]     DATETIME2      NOT NULL DEFAULT SYSDATETIME(),
        CONSTRAINT PK_HechosOnTime PRIMARY KEY ([Cliente], [Fecha de Factura], [Factura])
    );

    CREATE NONCLUSTERED INDEX IX_HechosOnTime_Fecha ON core.HechosOnTime ([Fecha de Factura]);
END
GO
