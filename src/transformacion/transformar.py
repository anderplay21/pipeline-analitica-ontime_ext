"""Transformación de negocio: réplica de la lógica del script Power Query
(procesar_consolidado_sharepoint_script2.py) — cálculo de Lead Time, fecha estimada de
entrega por días hábiles, clasificación On Time ácido/justificado — más el cálculo de
RowHash para la detección de cambios en el MERGE.
"""

import hashlib
from datetime import timedelta
from pathlib import Path

import pandas as pd

from extraccion.extraer_sharepoint import convertir_fecha_excel
from transformacion.tipado import a_fecha, aplicar_tipado_estricto

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]


def cargar_datos_referencia(config):
    """Carga LeadTime, Causales, Festivos y Restricciones desde data_referencia/."""
    ref_cfg = config["datos_referencia"]
    carpeta = RAIZ_PROYECTO / ref_cfg["carpeta"]

    leadtime = pd.read_csv(carpeta / ref_cfg["leadtime"], sep=";")
    causales = pd.read_csv(carpeta / ref_cfg["causales"], sep=";")
    festivos = pd.read_csv(carpeta / ref_cfg["festivos"], sep=";")
    festivos["Festivos"] = pd.to_datetime(festivos["Festivos"], format="%d/%m/%Y")
    restricciones = pd.read_csv(carpeta / ref_cfg["restricciones"], sep=";")

    return {
        "leadtime": leadtime,
        "causales": causales,
        "festivos": festivos,
        "restricciones": restricciones,
    }


def calcular_dias_habiles(fecha_inicio, dias_a_sumar, festivos, cliente, restricciones):
    """Suma días hábiles (sin fines de semana ni festivos) a partir de fecha_inicio,
    respetando los días de trabajo configurados por cliente en Restricciones.
    """
    if pd.isna(fecha_inicio) or pd.isna(dias_a_sumar):
        return None

    try:
        fecha_inicio = convertir_fecha_excel(fecha_inicio)
        if fecha_inicio is None:
            return None

        dias_a_sumar = int(dias_a_sumar)

        cliente_restricciones = restricciones[
            restricciones["Clientes"].str.contains(cliente, case=False, na=False)
        ]

        if cliente_restricciones.empty:
            dias_trabaja = [0, 1, 2, 3, 4, 5]  # por defecto: lunes a sábado
        else:
            codigo_dia = cliente_restricciones.iloc[0]["Codigo De Dia"]
            dias_trabaja = [0, 1, 2, 3, 4] if codigo_dia == 1 else [0, 1, 2, 3, 4, 5]

        fechas_festivas = set(festivos["Festivos"].dt.date)

        fecha_actual = fecha_inicio
        dias_sumados = 0
        while dias_sumados < dias_a_sumar:
            fecha_actual += timedelta(days=1)
            es_dia_trabajo = fecha_actual.weekday() in dias_trabaja
            es_festivo = fecha_actual.date() in fechas_festivas
            if es_dia_trabajo and not es_festivo:
                dias_sumados += 1

        return fecha_actual
    except Exception:
        return None


def calcular_ontime_acido(row):
    fecha_entrega = row["FECHA ENTREGA"]
    fecha_estimada = row["Fecha Estimada Entrega"]

    if pd.isna(fecha_entrega) or pd.isna(fecha_estimada):
        return "No Hay Fecha De Entrega"

    fecha_entrega = pd.Timestamp(fecha_entrega)
    fecha_estimada = pd.Timestamp(fecha_estimada)
    return "On Time" if fecha_entrega.date() <= fecha_estimada.date() else "Off Time"


def calcular_ontime_justificado(row, causales):
    fecha_entrega = row["FECHA ENTREGA"]
    fecha_estimada = row["Fecha Estimada Entrega"]
    novedad = row.get("Novedad / Causal", None)

    if pd.isna(fecha_entrega):
        return "Sin Fecha De Entrega"

    if pd.notna(novedad) and novedad != "":
        causal_match = causales[causales["Descripcion"].str.strip() == str(novedad).strip()]
        if not causal_match.empty and causal_match.iloc[0]["On Time Justificado"] == "JUSTIFICADO":
            return "JUSTIFICADO"

    if pd.notna(novedad) and novedad != "":
        causal_match = causales[causales["Descripcion"].str.strip() == str(novedad).strip()]
        if not causal_match.empty and causal_match.iloc[0]["On Time Justificado"] == "ON TIME":
            return "ON TIME"

    if pd.notna(fecha_estimada):
        if pd.Timestamp(fecha_entrega).date() <= pd.Timestamp(fecha_estimada).date():
            return "On Time"

    return "PENALIZADO"


def transformar(df, config):
    """Aplica todas las reglas de negocio y devuelve el DataFrame listo para staging."""
    datos_ref = cargar_datos_referencia(config)
    df = df.copy()

    df["Ciudad Origen"] = df["Ciudad Origen"].astype(str).str.upper()
    df["Ciudad Destino"] = df["Ciudad Destino"].astype(str).str.upper()
    df["Origen_Destino"] = df["Ciudad Origen"] + "_" + df["Ciudad Destino"]

    # LeadTime.csv trae mas de un lead time para el mismo par Origen_Destino cuando la
    # ruta puede servirse desde distintos centros/regionales. Sin deduplicar, el merge
    # multiplica filas (una por cada lead time posible) y luego el MERGE a SQL Server
    # falla porque la llave de negocio Cliente+Fecha+Factura queda repetida en staging.
    # TODO: confirmar con negocio el criterio correcto (¿el de menor lead time? ¿el del
    # centro que realmente despacha esa ruta?); por ahora se toma la primera coincidencia.
    leadtime_unico = datos_ref["leadtime"].drop_duplicates(subset="Origen_Destino", keep="first")
    df = df.merge(
        leadtime_unico[["Origen_Destino", "LEAD TIME DIAS"]],
        on="Origen_Destino", how="left",
    )
    df.rename(columns={"LEAD TIME DIAS": "Lead Time"}, inplace=True)

    df["Fecha Estimada Entrega"] = df.apply(
        lambda row: calcular_dias_habiles(
            row["Fecha de Factura"], row["Lead Time"],
            datos_ref["festivos"], row["Cliente"], datos_ref["restricciones"],
        ),
        axis=1,
    )

    # Se usa `a_fecha` (el mismo conversor del tipado estricto) y no una conversión
    # aparte: así la fecha con la que se clasifica On Time es exactamente la que se
    # graba. Con dos conversores distintos, una fecha basura (ej. una fracción de hora
    # de Excel, que como serial cae en 1899) alcanzaba a clasificar la fila como
    # "Off Time" y después el tipado la dejaba en NULL, dejando el KPI sin respaldo.
    #
    # Las fechas que se descartan aquí se cuentan y se devuelven: si no, quedarían
    # anuladas en silencio, porque al llegar al tipado estricto ya vienen en NULL y
    # su contador no las vería.
    fechas_invalidas = {}
    for col in ["Fecha de Factura", "Fecha Programada De Entrega", "FECHA ENTREGA", "FECHA DE CITA"]:
        if col in df.columns:
            antes = df[col].notna().sum()
            df[col] = df[col].apply(a_fecha)
            descartadas = int(antes - df[col].notna().sum())
            if descartadas:
                fechas_invalidas[col] = descartadas

    df["Nombre del mes"] = df["Fecha de Factura"].apply(
        lambda x: pd.Timestamp(x).strftime("%B") if pd.notna(x) else None
    )

    df["ON TIME ACIDO"] = df.apply(calcular_ontime_acido, axis=1)
    df["ON TIME JUSTIFICADO"] = df.apply(
        lambda row: calcular_ontime_justificado(row, datos_ref["causales"]), axis=1
    )

    return df, fechas_invalidas


def resolver_duplicados_llave_negocio(df, llave_negocio):
    """Cuando la misma llave de negocio (Cliente + Fecha de Factura + Factura) aparece más
    de una vez -mismo archivo cargado dos veces, o un archivo "Copia de..." que quedó
    desactualizado frente al original-, se prioriza la fila que ya tiene FECHA ENTREGA
    (el estado más avanzado). Si ambas o ninguna la tienen, se descarta el resto y se
    conserva una sola.
    """
    df = df.copy()
    df["_tiene_entrega"] = df["FECHA ENTREGA"].notna()
    df = df.sort_values("_tiene_entrega", ascending=False, kind="mergesort")
    df = df.drop_duplicates(subset=llave_negocio, keep="first")
    return df.drop(columns="_tiene_entrega")


def calcular_hash_fila(fila, columnas):
    """Hash SHA-256 de una fila, usado para detectar si un registro cambió sin comparar
    columna por columna.
    """
    texto = "|".join(str(fila[c]) for c in columnas)
    return hashlib.sha256(texto.encode("utf-8")).digest()


def calcular_rowhash(df, columnas_hash):
    df = df.copy()
    df["RowHash"] = df.apply(lambda fila: calcular_hash_fila(fila, columnas_hash), axis=1)
    return df


def transformar_y_calcular_hash(df_crudo, config, nombre_tabla="hechos_ontime", logger=None):
    """Pipeline completo: reglas de negocio + tipado estricto + RowHash.

    El orden no es casual:
      1. reglas de negocio (Lead Time, fechas estimadas, clasificación On Time)
      2. tipado estricto -> deja cada columna en su tipo destino y normaliza la llave
         de negocio (una factura que llegó como 2105035559.0 y otra como "2105035559"
         pasan a ser el mismo valor, así el paso 3 sí las reconoce como duplicadas)
      3. deduplicación por llave de negocio
      4. RowHash sobre los valores ya tipados, para que la detección de cambios sea
         estable entre corridas

    Devuelve (df_listo_para_staging, reporte_de_tipado).
    """
    tabla_cfg = config["tablas"][nombre_tabla]
    df, fechas_invalidas = transformar(df_crudo, config)

    # Sin logger acá a propósito: el reporte todavía está incompleto. Se loguea abajo,
    # una sola vez, ya con las fechas descartadas en la transformación incluidas.
    df, reporte = aplicar_tipado_estricto(df)

    # Esas fechas son el mismo tipo de problema que las del tipado, así que van al
    # mismo reporte y terminan en el log y en ETL_Control.
    for columna, cantidad in fechas_invalidas.items():
        reporte.anulados[columna] = reporte.anulados.get(columna, 0) + cantidad

    if logger is not None and reporte.hubo_ajustes:
        logger.warning("Datos ajustados por tipado: %s", reporte.resumen())

    df = resolver_duplicados_llave_negocio(df, tabla_cfg["llave_negocio"])
    df = calcular_rowhash(df, tabla_cfg["columnas_hash"])
    return df, reporte
