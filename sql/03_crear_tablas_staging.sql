-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- Tabla Staging: espejo de core.HechosOnTime, desechable (se trunca y recarga en cada corrida).
-- Solo contiene la ventana móvil (mes actual + mes anterior), nunca el histórico completo.

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HechosOnTime_Stage' AND schema_id = SCHEMA_ID('staging'))
BEGIN
    CREATE TABLE staging.HechosOnTime_Stage (
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
        [RowHash]                      BINARY(32)     NOT NULL
    );
END
GO
