"""Conexión reutilizable a SQL Server (pyodbc / sqlalchemy)."""

import os
from pathlib import Path

import pyodbc
import yaml
from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.engine import URL

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
load_dotenv(RAIZ_PROYECTO / "config" / ".env")

# Nombre con el que el pipeline se presenta ante SQL Server (columna program_name).
NOMBRE_APLICACION = "pipeline-analitica-ontime"


def cargar_config():
    with open(RAIZ_PROYECTO / "config" / "config.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _credenciales_sql():
    """Usuario y clave del login SQL, tomados de .env.

    Falla de entrada con un mensaje claro si faltan: sin esto la cadena se arma con
    "UID=None" y SQL Server responde un "Login failed for user 'None'" que manda a
    buscar el problema en el servidor cuando en realidad está en el .env local.
    """
    usuario = os.getenv("SQL_USUARIO")
    password = os.getenv("SQL_PASSWORD")
    if not usuario or not password:
        faltan = " y ".join(
            n for n, v in (("SQL_USUARIO", usuario), ("SQL_PASSWORD", password)) if not v
        )
        raise RuntimeError(
            f"config.yaml pide autenticación SQL (trusted_connection: false) pero falta "
            f"{faltan} en config/.env."
        )
    return usuario, password


def _entre_llaves(valor):
    """Delimita un valor para la cadena ODBC.

    Las claves generadas suelen traer `;`, `=` o espacios, que en una cadena ODBC sin
    delimitar cortan el atributo y producen un "Login failed" imposible de diagnosticar.
    Entre llaves eso deja de importar; el único carácter que sigue siendo especial es la
    llave de cierre, que se escapa duplicándola.
    """
    return "{" + valor.replace("}", "}}") + "}"


def construir_connection_string(config=None):
    config = config or cargar_config()
    sql_cfg = config["sql_server"]

    partes = [
        f"DRIVER={{{sql_cfg['driver']}}}",
        f"SERVER={sql_cfg['servidor']}",
        f"DATABASE={sql_cfg['base_datos']}",
    ]

    if sql_cfg.get("trusted_connection", True):
        partes.append("Trusted_Connection=yes")
    else:
        usuario, password = _credenciales_sql()
        partes.append(f"UID={usuario}")
        partes.append(f"PWD={_entre_llaves(password)}")

    if sql_cfg.get("trust_server_certificate", True):
        partes.append("TrustServerCertificate=yes")

    # El servidor es remoto: sin un timeout explícito una caída de red deja la corrida
    # colgada en vez de fallar y quedar registrada en core.ETL_Control.
    partes.append(f"Connect Timeout={sql_cfg.get('timeout_conexion', 30)}")
    # Identifica al pipeline en sys.dm_exec_sessions.program_name, para que el DBA pueda
    # distinguir sus conexiones de las de las demás aplicaciones del servidor.
    partes.append(f"APP={NOMBRE_APLICACION}")

    return ";".join(partes) + ";"


def obtener_conexion_pyodbc(config=None):
    """Conexión directa pyodbc, útil para ejecutar MERGE/DDL y cargas rápidas."""
    conn_str = construir_connection_string(config)
    return pyodbc.connect(conn_str)


def obtener_engine_sqlalchemy(config=None):
    """Engine de SQLAlchemy, útil para leer/escribir con pandas.to_sql / read_sql."""
    conn_str = construir_connection_string(config)
    url = URL.create("mssql+pyodbc", query={"odbc_connect": conn_str})
    return create_engine(url, fast_executemany=True)
