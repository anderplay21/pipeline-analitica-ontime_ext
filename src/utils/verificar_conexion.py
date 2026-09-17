"""Diagnóstico de la conexión a SQL Server: alcance, permisos y objetos del pipeline.

Contra el servidor corporativo la corrida ya no falla solo por datos: puede fallar
porque la cuenta de Windows que ejecuta el proceso no tiene permisos sobre la base,
o porque los objetos (esquemas y tablas) todavía no existen allí. Este script separa
esos tres problemas y dice cuál es, en vez de dejar un "Login failed" sin contexto.

Uso:
    cd src && python -m utils.verificar_conexion
"""

import os
import sys

import pyodbc

from utils import esquema as esq
from utils.db import cargar_config, construir_connection_string

# Objetos que el pipeline necesita para correr (ver sql/01..04).
TABLAS_REQUERIDAS = (
    ("core", "HechosOnTime"),
    ("staging", "HechosOnTime_Stage"),
    ("core", "ETL_Control"),
)

# Permisos mínimos sobre cada objeto: sin alguno de estos la corrida se cae.
PERMISOS_REQUERIDOS = (
    ("core.HechosOnTime", ("SELECT", "INSERT", "UPDATE")),
    ("staging.HechosOnTime_Stage", ("SELECT", "INSERT", "DELETE")),
    ("core.ETL_Control", ("INSERT",)),
)


def _conectar(config, base_datos=None):
    """Conexión con la cadena del pipeline, opcionalmente redirigida a otra base."""
    cadena = construir_connection_string(config)
    if base_datos:
        cadena = cadena.replace(
            f"DATABASE={config['sql_server']['base_datos']}", f"DATABASE={base_datos}"
        )
    return pyodbc.connect(cadena)


def _revisar_servidor(config):
    """Confirma que el servidor responde y que el login de Windows es válido.

    Se conecta a master porque un login puede ser válido a nivel de servidor y aun así
    no tener acceso a la base destino; distinguir los dos casos es justamente el punto.
    """
    with _conectar(config, base_datos="master") as cn:
        login, servidor, version, es_sysadmin = cn.cursor().execute(
            """
            SELECT SUSER_SNAME(),
                   CAST(@@SERVERNAME AS nvarchar(128)),
                   CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(50)),
                   IS_SRVROLEMEMBER('sysadmin')
            """
        ).fetchone()
    print(f"[OK] Servidor {servidor} (SQL Server {version}) responde")
    print(f"[OK] Autenticado como {login}")
    if es_sysadmin:
        # No impide correr, pero conviene verlo en cada verificación: es un servidor
        # compartido y la clave del login vive en texto plano en config/.env.
        print(f"[AVISO] {login} es sysadmin: controla las demás bases del servidor.")
        print("        Para acotarlo, crear el login dedicado con "
              "sql/07_crear_login_pipeline.sql y apuntar .env a él.")


def _revisar_columnas(cursor):
    """Confirma que las tablas tengan las columnas que el esquema declara.

    Es el desajuste más fácil de producir: agregar una columna en `utils.esquema` y
    olvidar el ALTER TABLE contra el servidor. Los DDL generados usan IF NOT EXISTS y
    no alteran una tabla que ya existe, así que regenerarlos no basta. Sin este chequeo
    el error aparece recién en la carga, como un "Invalid column name" a las 8 a.m.
    """
    faltantes = []
    for esquema_sql, tabla in (("core", "HechosOnTime"), ("staging", "HechosOnTime_Stage")):
        existentes = {
            fila[0]
            for fila in cursor.execute(
                "SELECT c.name FROM sys.columns c JOIN sys.tables t ON t.object_id = c.object_id"
                " JOIN sys.schemas s ON s.schema_id = t.schema_id WHERE s.name = ? AND t.name = ?",
                esquema_sql, tabla,
            )
        }
        faltantes += [
            f"{esquema_sql}.{tabla}.[{c}]" for c in esq.columnas() if c not in existentes
        ]

    if faltantes:
        print(f"[FALTA] Columnas que el esquema declara y la base no tiene: {', '.join(faltantes)}")
        print("        La carga fallará con 'Invalid column name'. Ejecutar el script de")
        print("        migración correspondiente en sql/ con una cuenta que pueda hacer DDL.")
        return False

    print(f"[OK] Las tablas tienen las {len(esq.columnas())} columnas del esquema")
    return True


def _revisar_base(config):
    base = config["sql_server"]["base_datos"]
    with _conectar(config) as cn:
        cursor = cn.cursor()
        print(f"[OK] Acceso a la base {base} como usuario "
              f"{cursor.execute('SELECT USER_NAME()').fetchone()[0]}")

        faltantes = [
            f"{esquema}.{tabla}"
            for esquema, tabla in TABLAS_REQUERIDAS
            if not cursor.execute(
                "SELECT COUNT(*) FROM sys.tables t JOIN sys.schemas s"
                " ON s.schema_id = t.schema_id WHERE s.name = ? AND t.name = ?",
                esquema, tabla,
            ).fetchone()[0]
        ]
        if faltantes:
            print(f"[FALTA] Tablas que no existen todavía: {', '.join(faltantes)}")
            print("        Ejecutar sql/01_crear_esquemas.sql .. sql/04_crear_control.sql")
            return False

        print(f"[OK] Existen las {len(TABLAS_REQUERIDAS)} tablas del pipeline")

        if not _revisar_columnas(cursor):
            return False

        sin_permiso = [
            f"{objeto}:{permiso}"
            for objeto, permisos in PERMISOS_REQUERIDOS
            for permiso in permisos
            if not cursor.execute(
                "SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', ?)", objeto, permiso
            ).fetchone()[0]
        ]
        if sin_permiso:
            print(f"[FALTA] Permisos ausentes: {', '.join(sin_permiso)}")
            print("        Pedir al DBA que ejecute sql/06_permisos_pipeline.sql")
            return False

        print("[OK] La cuenta tiene los permisos de lectura/escritura que usa el pipeline")

        # ALTER no es obligatorio: sin él la carga vacía staging con DELETE en vez de
        # TRUNCATE (ver carga.cargar_sql._vaciar_staging). Se informa porque explica por
        # qué el vaciado es más lento, y porque es lo que distingue a una cuenta con
        # db_datawriter de una con permisos sobre el esquema.
        if not cursor.execute(
            "SELECT HAS_PERMS_BY_NAME('staging.HechosOnTime_Stage', 'OBJECT', 'ALTER')"
        ).fetchone()[0]:
            print("[NOTA] Sin permiso ALTER en staging: se vaciará con DELETE, no TRUNCATE.")
            print("       Funciona igual. Para usar TRUNCATE: "
                  "GRANT ALTER ON SCHEMA::staging TO <cuenta>;")
        return True


def main():
    config = cargar_config()
    sql_cfg = config["sql_server"]
    print(f"Destino configurado: {sql_cfg['servidor']} / {sql_cfg['base_datos']} "
          f"({'autenticación de Windows' if sql_cfg.get('trusted_connection', True) else 'usuario SQL'})")

    try:
        _revisar_servidor(config)
    except pyodbc.Error as e:
        print(f"[ERROR] No se pudo conectar al servidor: {e}")
        # La causa probable depende del modo de autenticación, y son arreglos distintos:
        # con login SQL casi siempre es la clave del .env; con Windows, el login del dominio.
        if sql_cfg.get("trusted_connection", True):
            print("        Revisar nombre del servidor, VPN/red y que el login de dominio "
                  "exista en SQL Server.")
        else:
            print(f"        Revisar SQL_USUARIO / SQL_PASSWORD en config/.env: el login "
                  f"'{os.getenv('SQL_USUARIO')}' debe existir en {sql_cfg['servidor']} y la "
                  f"clave debe ser la de ESE servidor (la del SQL local no sirve).")
            print("        Si el servidor responde pero el login no, también revisar red/VPN.")
        return 1

    try:
        todo_listo = _revisar_base(config)
    except pyodbc.Error as e:
        print(f"[ERROR] El servidor responde pero la base no es accesible: {e}")
        print("        Pedir al DBA que ejecute sql/06_permisos_pipeline.sql para dar "
              "acceso a la cuenta de dominio.")
        return 1

    print("\nConexión lista para correr el pipeline" if todo_listo
          else "\nFaltan pasos antes de correr el pipeline (ver arriba)")
    return 0 if todo_listo else 1


if __name__ == "__main__":
    sys.exit(main())
