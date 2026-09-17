"""Carga a staging (truncate + insert) y ejecución del MERGE staging -> core."""

from pathlib import Path

import pandas as pd
import pyodbc

from utils import esquema as esq

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]
CARPETA_MERGES = RAIZ_PROYECTO / "sql" / "merges"

# Orden de inserción: las columnas del esquema más la huella técnica.
COLUMNAS_CARGA = esq.columnas() + ("RowHash",)


def _valores_para_pyodbc(df):
    """Pasa el DataFrame a listas de valores nativos.

    `transformacion.tipado` ya dejó cada columna en su tipo destino (str/date/time/
    int/None), así que aquí no hay conversiones de negocio: solo la red de seguridad
    de traducir cualquier NaN/NaT residual de pandas a None, porque pyodbc los
    enviaría como valor en vez de NULL.
    """
    return [
        [None if (isinstance(v, float) and pd.isna(v)) or v is pd.NaT or v is pd.NA else v
         for v in fila]
        for fila in df.values.tolist()
    ]


def _vaciar_staging(cursor, tabla_staging):
    """Deja vacía la tabla de staging antes de recargarla.

    Lo natural es TRUNCATE (mínimo log), pero exige permiso ALTER sobre la tabla, que los
    roles estándar `db_datareader`/`db_datawriter` no incluyen: una cuenta de servicio
    creada con esos roles falla con el error 1088, cuyo texto ("no existe o no tienes
    permisos") hace pensar que falta la tabla. Cuando no hay ALTER se usa DELETE, que solo
    necesita el permiso que `db_datawriter` sí trae. A la escala de esta tabla (~40k filas)
    la diferencia de tiempo es despreciable.
    """
    puede_truncar = cursor.execute(
        "SELECT HAS_PERMS_BY_NAME(?, 'OBJECT', 'ALTER')", tabla_staging
    ).fetchone()[0]

    if puede_truncar:
        cursor.execute(f"TRUNCATE TABLE {tabla_staging}")
    else:
        cursor.execute(f"DELETE FROM {tabla_staging}")


def cargar_staging(df, tabla_staging, conexion):
    """Trunca la tabla de staging y carga el DataFrame completo.

    Exige que el DataFrame traiga exactamente las columnas del esquema (en su orden)
    para que la lista del INSERT y la tabla no puedan desalinearse. Las columnas se
    escriben entre corchetes porque varias traen espacios o "/" (ej. "Cl/ Movimiento",
    "Novedad / Causal"), sintaxis inválida sin delimitar en T-SQL.
    """
    faltantes = [c for c in COLUMNAS_CARGA if c not in df.columns]
    if faltantes:
        raise ValueError(f"Faltan columnas requeridas para la carga: {faltantes}")

    df = df[list(COLUMNAS_CARGA)]

    cursor = conexion.cursor()
    _vaciar_staging(cursor, tabla_staging)
    cursor.fast_executemany = True

    columnas_sql = ",".join(f"[{c}]" for c in COLUMNAS_CARGA)
    placeholders = ",".join(["?"] * len(COLUMNAS_CARGA))
    sql_insert = f"INSERT INTO {tabla_staging} ({columnas_sql}) VALUES ({placeholders})"

    cursor.executemany(sql_insert, _valores_para_pyodbc(df))
    conexion.commit()
    return len(df)


def ejecutar_merge(nombre_archivo_merge, conexion):
    """Ejecuta el script SQL de MERGE correspondiente y devuelve (insertadas, actualizadas)."""
    ruta = CARPETA_MERGES / nombre_archivo_merge
    script = ruta.read_text(encoding="utf-8")

    cursor = conexion.cursor()
    # Los scripts usan GO como separador de lotes; pyodbc no lo entiende, se separa aquí.
    lotes = [lote.strip() for lote in script.split("\nGO") if lote.strip()]

    filas_insertadas, filas_actualizadas = 0, 0
    for lote in lotes:
        cursor.execute(lote)
        # Un lote con MERGE + SELECT genera varios "result sets" (mensajes de filas
        # afectadas antes del SELECT real); hay que recorrerlos con nextset() hasta
        # encontrar uno con filas, si no cursor.fetchone() puede devolver None de largada.
        while True:
            try:
                fila = cursor.fetchone()
            except pyodbc.ProgrammingError:
                fila = None
            if fila is not None:
                filas_insertadas, filas_actualizadas = fila[0], fila[1]
                break
            if not cursor.nextset():
                break

    conexion.commit()
    return filas_insertadas, filas_actualizadas
