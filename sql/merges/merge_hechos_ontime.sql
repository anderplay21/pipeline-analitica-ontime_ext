-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- MERGE staging -> core para HechosOnTime
-- Llave de negocio: Cliente + Fecha de Factura + Factura.
-- IMPORTANTE: no incluye WHEN NOT MATCHED BY SOURCE.
-- Staging solo trae la ventana móvil (mes actual + anterior); si se activara esa rama
-- se borraría/marcaría como eliminado todo el histórico que no está en esos meses.

USE AnaliticaOperativa;
GO

DECLARE @ResultadoMerge TABLE (Accion NVARCHAR(10));

MERGE core.HechosOnTime AS destino
USING staging.HechosOnTime_Stage AS origen
    ON destino.[Cliente] = origen.[Cliente]
   AND destino.[Fecha de Factura] = origen.[Fecha de Factura]
   AND destino.[Factura] = origen.[Factura]
WHEN MATCHED AND destino.RowHash <> origen.RowHash THEN
    UPDATE SET
        destino.[Cl/ Movimiento] = origen.[Cl/ Movimiento],
        destino.[Cl Doc FWO] = origen.[Cl Doc FWO],
        destino.[Ciudad Origen] = origen.[Ciudad Origen],
        destino.[Centro Origen] = origen.[Centro Origen],
        destino.[Destinatario] = origen.[Destinatario],
        destino.[Ciudad Destino] = origen.[Ciudad Destino],
        destino.[Centro Destino] = origen.[Centro Destino],
        destino.[Carta Porte] = origen.[Carta Porte],
        destino.[Orden de Compra] = origen.[Orden de Compra],
        destino.[Orden de Pedido] = origen.[Orden de Pedido],
        destino.[Remision] = origen.[Remision],
        destino.[Ultima Ubicación] = origen.[Ultima Ubicación],
        destino.[Fecha Programada De Entrega] = origen.[Fecha Programada De Entrega],
        destino.[Hora_Cita] = origen.[Hora_Cita],
        destino.[Tipo Producto] = origen.[Tipo Producto],
        destino.[ESTATUS] = origen.[ESTATUS],
        destino.[Novedad / Causal] = origen.[Novedad / Causal],
        destino.[FECHA ENTREGA] = origen.[FECHA ENTREGA],
        destino.[FECHA DE CITA] = origen.[FECHA DE CITA],
        destino.[DOCUWARE] = origen.[DOCUWARE],
        destino.[Lead Time] = origen.[Lead Time],
        destino.[Fecha Estimada Entrega] = origen.[Fecha Estimada Entrega],
        destino.[RESPONSABLE] = origen.[RESPONSABLE],
        destino.[Origen_Destino] = origen.[Origen_Destino],
        destino.[Nombre del mes] = origen.[Nombre del mes],
        destino.[ON TIME ACIDO] = origen.[ON TIME ACIDO],
        destino.[ON TIME JUSTIFICADO] = origen.[ON TIME JUSTIFICADO],
        destino.[Dir/ Cliente] = origen.[Dir/ Cliente],
        destino.[Cajas] = origen.[Cajas],
        destino.[Kilos] = origen.[Kilos],
        destino.[source_file] = origen.[source_file],
        destino.[source_path] = origen.[source_path],
        destino.[source_sheet] = origen.[source_sheet],
        destino.[RowHash] = origen.[RowHash],
        destino.[FechaUltimaActualizacion] = SYSDATETIME()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (
        [Cliente],
        [Cl/ Movimiento],
        [Cl Doc FWO],
        [Fecha de Factura],
        [Ciudad Origen],
        [Centro Origen],
        [Destinatario],
        [Ciudad Destino],
        [Centro Destino],
        [Factura],
        [Carta Porte],
        [Orden de Compra],
        [Orden de Pedido],
        [Remision],
        [Ultima Ubicación],
        [Fecha Programada De Entrega],
        [Hora_Cita],
        [Tipo Producto],
        [ESTATUS],
        [Novedad / Causal],
        [FECHA ENTREGA],
        [FECHA DE CITA],
        [DOCUWARE],
        [Lead Time],
        [Fecha Estimada Entrega],
        [RESPONSABLE],
        [Origen_Destino],
        [Nombre del mes],
        [ON TIME ACIDO],
        [ON TIME JUSTIFICADO],
        [Dir/ Cliente],
        [Cajas],
        [Kilos],
        [source_file],
        [source_path],
        [source_sheet],
        [RowHash]
    )
    VALUES (
        origen.[Cliente],
        origen.[Cl/ Movimiento],
        origen.[Cl Doc FWO],
        origen.[Fecha de Factura],
        origen.[Ciudad Origen],
        origen.[Centro Origen],
        origen.[Destinatario],
        origen.[Ciudad Destino],
        origen.[Centro Destino],
        origen.[Factura],
        origen.[Carta Porte],
        origen.[Orden de Compra],
        origen.[Orden de Pedido],
        origen.[Remision],
        origen.[Ultima Ubicación],
        origen.[Fecha Programada De Entrega],
        origen.[Hora_Cita],
        origen.[Tipo Producto],
        origen.[ESTATUS],
        origen.[Novedad / Causal],
        origen.[FECHA ENTREGA],
        origen.[FECHA DE CITA],
        origen.[DOCUWARE],
        origen.[Lead Time],
        origen.[Fecha Estimada Entrega],
        origen.[RESPONSABLE],
        origen.[Origen_Destino],
        origen.[Nombre del mes],
        origen.[ON TIME ACIDO],
        origen.[ON TIME JUSTIFICADO],
        origen.[Dir/ Cliente],
        origen.[Cajas],
        origen.[Kilos],
        origen.[source_file],
        origen.[source_path],
        origen.[source_sheet],
        origen.[RowHash]
    )
OUTPUT $action INTO @ResultadoMerge;

SELECT
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'INSERT') AS FilasInsertadas,
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'UPDATE') AS FilasActualizadas;
GO
