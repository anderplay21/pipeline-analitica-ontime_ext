"""Configuración centralizada de logging: archivo rotativo diario + consola."""

import logging
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
CARPETA_LOGS = RAIZ_PROYECTO / "logs"


def configurar_logging(nombre_logger="pipeline_ontime", nivel="INFO", rotacion_dias=30):
    CARPETA_LOGS.mkdir(exist_ok=True)

    logger = logging.getLogger(nombre_logger)
    logger.setLevel(getattr(logging, nivel.upper(), logging.INFO))

    if logger.handlers:
        return logger

    formato = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    manejador_archivo = TimedRotatingFileHandler(
        CARPETA_LOGS / "pipeline.log",
        when="midnight",
        backupCount=rotacion_dias,
        encoding="utf-8",
    )
    manejador_archivo.setFormatter(formato)
    logger.addHandler(manejador_archivo)

    manejador_consola = logging.StreamHandler()
    manejador_consola.setFormatter(formato)
    logger.addHandler(manejador_consola)

    return logger
