"""Orquestador del pipeline: extracción -> transformación -> carga a staging -> MERGE a core.

Registra cada corrida en core.ETL_Control y en la lista de SharePoint de ejecuciones,
y envía alertas si algo falla.
"""

import sys
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from carga.cargar_sql import cargar_staging, ejecutar_merge
from extraccion.extraer_sharepoint import calcular_ventana_fechas, extraer
from transformacion.transformar import transformar_y_calcular_hash
from utils.db import cargar_config, obtener_conexion_pyodbc
from utils.logging_config import configurar_logging
from utils.notificaciones import notificar_error
from utils.reporte_sharepoint import construir_registro, registrar_ejecucion

NOMBRE_TABLA = "hechos_ontime"  # clave dentro de config["tablas"]; hoy es la única tabla del pipeline


def registrar_control(conexion, nombre_proceso, fecha_desde, fecha_hasta,
                       filas_staging=None, filas_insertadas=None,
                       filas_actualizadas=None, estado="OK", detalle=None):
    cursor = conexion.cursor()
    cursor.execute(
        """
        INSERT INTO core.ETL_Control
            (NombreProceso, FechaDesde, FechaHasta, FilasStaging,
             FilasInsertadas, FilasActualizadas, Estado, Detalle)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        nombre_proceso, fecha_desde, fecha_hasta, filas_staging,
        filas_insertadas, filas_actualizadas, estado, detalle,
    )
    conexion.commit()


def _registrar_en_sharepoint(config, logger, inicio, exitosa, fecha_desde, fecha_hasta,
                             cifras, error=None):
    """Deja la corrida en la lista de SharePoint. No lanza: ver utils.reporte_sharepoint."""
    registro = construir_registro(
        inicio, datetime.now(), exitosa, fecha_desde, fecha_hasta, error=error, **cifras
    )
    registrar_ejecucion(config, registro, logger=logger)


def main():
    config = cargar_config()
    logger = configurar_logging(
        nivel=config["logging"]["nivel"],
        rotacion_dias=config["logging"]["rotacion_dias"],
    )
    tabla_cfg = config["tablas"][NOMBRE_TABLA]

    inicio = datetime.now()
    logger.info("Inicio de corrida del pipeline (%s)", NOMBRE_TABLA)
    fecha_desde, fecha_hasta = calcular_ventana_fechas(
        meses_atras=config.get("ventana_movil", {}).get("meses_atras", 1)
    )
    # Cifras de la corrida para la lista de SharePoint; se llenan a medida que avanza,
    # así una corrida fallida registra hasta dónde llegó.
    cifras = {}

    try:
        conexion = obtener_conexion_pyodbc(config)
    except Exception:
        # Sin conexión no hay core.ETL_Control donde dejarlo: la lista es el único rastro.
        _registrar_en_sharepoint(config, logger, inicio, False, fecha_desde, fecha_hasta,
                                 cifras, traceback.format_exc())
        raise

    try:
        estadisticas_extraccion = {}
        df_crudo, fecha_desde, fecha_hasta = extraer(
            config, NOMBRE_TABLA, logger=logger, estadisticas=estadisticas_extraccion
        )
        cifras["libros_leidos"] = estadisticas_extraccion.get("libros_leidos")
        cifras["filas_extraidas"] = len(df_crudo)
        logger.info("Extracción: %s filas en ventana %s -> %s", len(df_crudo), fecha_desde, fecha_hasta)

        # El detalle de los ajustes por datos sucios lo loguea transformar_y_calcular_hash
        # (como WARNING, y solo si hubo algo que ajustar); acá no se repite.
        df_transformado, reporte_tipado = transformar_y_calcular_hash(
            df_crudo, config, NOMBRE_TABLA, logger=logger
        )

        filas_staging = cargar_staging(df_transformado, tabla_cfg["tabla_staging"], conexion)
        cifras["filas_staging"] = filas_staging
        logger.info("Carga a staging: %s filas", filas_staging)

        filas_insertadas, filas_actualizadas = ejecutar_merge("merge_hechos_ontime.sql", conexion)
        cifras["filas_insertadas"] = filas_insertadas
        cifras["filas_actualizadas"] = filas_actualizadas
        logger.info("MERGE: %s insertadas, %s actualizadas", filas_insertadas, filas_actualizadas)

        registrar_control(
            conexion, NOMBRE_TABLA, fecha_desde, fecha_hasta,
            filas_staging, filas_insertadas, filas_actualizadas, estado="OK",
            # Queda en la bitácora qué se ajustó por datos sucios, aunque la corrida
            # haya sido exitosa: es la señal para ir a corregir el archivo de origen.
            detalle=f"Tipado: {reporte_tipado.resumen()}" if reporte_tipado.hubo_ajustes else None,
        )
        logger.info("Corrida finalizada correctamente")
        _registrar_en_sharepoint(config, logger, inicio, True, fecha_desde, fecha_hasta, cifras)

    except Exception as e:
        detalle = traceback.format_exc()
        logger.error("Error en la corrida: %s", e)
        try:
            registrar_control(
                conexion, NOMBRE_TABLA, fecha_desde, fecha_hasta,
                estado="ERROR", detalle=detalle,
            )
        except Exception:
            logger.error("No se pudo registrar el error en core.ETL_Control")
        _registrar_en_sharepoint(config, logger, inicio, False, fecha_desde, fecha_hasta,
                                 cifras, detalle)
        notificar_error(f"Pipeline {NOMBRE_TABLA} falló", detalle, config)
        raise
    finally:
        conexion.close()


if __name__ == "__main__":
    main()
