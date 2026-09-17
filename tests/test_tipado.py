"""Valida el tipado estricto con los valores sucios que realmente traen los Excel.

Los casos no son inventados: salen de auditar core.HechosOnTime y los consolidados de
SharePoint (fracciones de hora de Excel, horas escritas a mano, rangos, notas
operativas, códigos numéricos que llegan como float, espacios duros).
"""

import sys
from datetime import date, time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transformacion.tipado import a_entero, a_fecha, a_hora, a_texto, aplicar_tipado_estricto
from utils import esquema as esq


# --------------------------------------------------------------------------- texto

def test_texto_recorta_al_ancho_y_avisa():
    # El valor que tumbó la corrida del 2026-09-06: 54 caracteres en un NVARCHAR(50).
    valor = "Lunes a sabado 6:00 am a 11:00 am\xa0 - 1:00 pm a 3:00 pm"
    texto, truncado = a_texto(valor, 50)
    assert truncado is True
    assert len(texto) == 50


def test_texto_normaliza_espacio_duro_y_saltos():
    texto, truncado = a_texto("PS- TURBACO\r\n  REFACTU\xa0", 50)
    assert texto == "PS- TURBACO REFACTU"
    assert truncado is False


def test_texto_codigo_numerico_no_queda_con_punto_cero():
    # Remisión/factura llegan como float desde Excel; "2105035559.0" es un código inválido.
    assert a_texto(2105035559.0, 50)[0] == "2105035559"


def test_texto_basura_se_vuelve_nulo():
    for basura in ["", "   ", "nan", "NaT", "#N/D", "-", "NULL"]:
        assert a_texto(basura, 50)[0] is None, basura


def test_texto_valor_normal_intacto():
    assert a_texto("UNILEVER FOODS SOLUTIONS", 50) == ("UNILEVER FOODS SOLUTIONS", False)


# --------------------------------------------------------------------------- fecha

def test_fecha_desde_serial_excel():
    assert a_fecha(46235) == date(2026, 8, 1)


def test_fecha_desde_texto_dia_primero():
    # Formato colombiano: 2/09/2026 es 2 de septiembre, no 9 de febrero.
    assert a_fecha("2/09/2026") == date(2026, 9, 2)


def test_fecha_fuera_de_rango_se_descarta():
    # 0.2326 es una fracción de hora de Excel: como serial cae en 1899, no es una fecha.
    assert a_fecha(0.2326388888888889) is None
    assert a_fecha(pd.Timestamp("1899-12-30 05:35:00")) is None


def test_fecha_nula_o_basura():
    for valor in [None, pd.NaT, "", "nan", "TURNO"]:
        assert a_fecha(valor) is None, valor


# --------------------------------------------------------------------------- hora

@pytest.mark.parametrize("valor,esperado", [
    (0.375, time(9, 0)),                     # fracción de día de Excel
    (0.2916666666666667, time(7, 0)),
    ("0.375", time(9, 0)),                   # la misma fracción guardada como texto
    ("7:00 AM", time(7, 0)),
    ("7.00am ", time(7, 0)),                 # punto en vez de dos puntos
    ("1:00PM ", time(13, 0)),
    ("11:00pm", time(23, 0)),
    ("18:30", time(18, 30)),
    ("12:00 m", time(12, 0)),                # mediodía en el uso local
    ("8:00 a.m.", time(8, 0)),
    (time(14, 30), time(14, 30)),
])
def test_hora_formatos_validos(valor, esperado):
    assert a_hora(valor) == esperado


@pytest.mark.parametrize("valor", [
    "6:00 - 9:30 AM. Y 10:00 AM. - 2:00 PM.",          # rango, no una hora
    "LLAMAR MEDIA HORA ANTES DE OFRECER 3229476710",   # nota operativa
    "TURNO",
    "vencimiento de OC por despacho tarde de Aldor",
    "25:00",                                            # hora imposible
    "7:75",                                             # minuto imposible
    None,
    "",
])
def test_hora_no_interpretable_queda_nula(valor):
    assert a_hora(valor) is None


def test_hora_medianoche_se_considera_sin_dato():
    # Sale de celdas vacías o de un cero, no de una cita a las 00:00.
    assert a_hora(0) is None
    assert a_hora(time(0, 0)) is None


# --------------------------------------------------------------------------- entero

def test_entero_desde_texto_y_float():
    assert a_entero("4") == 4
    assert a_entero(4.0) == 4
    assert a_entero(" 7 ") == 7


def test_entero_basura_queda_nulo():
    for valor in [None, "", "nan", "sin dato", float("nan")]:
        assert a_entero(valor) is None, valor


def test_entero_fuera_del_rango_de_sql_server():
    assert a_entero(99_999_999_999) is None


# ------------------------------------------------------------------ tipos de numpy

def test_tipos_numpy_se_interpretan_igual_que_los_de_python():
    """pandas entrega numpy.int64 cuando la columna del Excel es homogénea numérica.

    numpy.int64 no es instancia de int, así que sin tratarlo aparte un serial de
    fecha se iría por la rama de texto y se interpretaría mal.
    """
    assert a_fecha(np.int64(46235)) == date(2026, 8, 1)
    assert a_hora(np.float64(0.375)) == time(9, 0)
    assert a_entero(np.int64(4)) == 4
    assert a_texto(np.int64(2105035559), 50)[0] == "2105035559"
    assert a_texto(np.float64(2105035559.0), 50)[0] == "2105035559"


# --------------------------------------------------- tipado completo del DataFrame

def _fila_valida(**cambios):
    fila = {c.nombre: None for c in esq.ESQUEMA_HECHOS_ONTIME}
    fila.update({
        "Cliente": "UNILEVER FOODS SOLUTIONS",
        "Fecha de Factura": 46235,
        "Factura": "PMP1422211",
    })
    fila.update(cambios)
    return fila


def test_tipado_devuelve_las_columnas_del_esquema_en_orden():
    df = pd.DataFrame([_fila_valida()])
    tipado, _ = aplicar_tipado_estricto(df)
    assert list(tipado.columns) == list(esq.columnas())


def test_tipado_convierte_cada_tipo():
    df = pd.DataFrame([_fila_valida(**{"Hora_Cita": 0.375, "Lead Time": "3"})])
    tipado, _ = aplicar_tipado_estricto(df)
    assert tipado["Fecha de Factura"].iloc[0] == date(2026, 8, 1)
    assert tipado["Hora_Cita"].iloc[0] == time(9, 0)
    assert tipado["Lead Time"].iloc[0] == 3
    assert isinstance(tipado["Cliente"].iloc[0], str)


def test_tipado_descarta_fila_sin_llave_de_negocio():
    df = pd.DataFrame([
        _fila_valida(),
        _fila_valida(Factura="   "),            # factura vacía: rompe la llave primaria
        _fila_valida(**{"Fecha de Factura": None}),
    ])
    tipado, reporte = aplicar_tipado_estricto(df)
    assert len(tipado) == 1
    assert reporte.filas_descartadas == 2
    assert reporte.motivos_descarte == {"Factura": 1, "Fecha de Factura": 1}


def test_tipado_reporta_truncados_y_anulados():
    df = pd.DataFrame([
        _fila_valida(**{
            "Destinatario": "X" * 150,     # excede nvarchar(100)
            "Hora_Cita": "TURNO",          # no interpretable como hora
        })
    ])
    tipado, reporte = aplicar_tipado_estricto(df)
    assert reporte.truncados["Destinatario"] == 1
    assert reporte.anulados["Hora_Cita"] == 1
    assert len(tipado["Destinatario"].iloc[0]) == 100
    assert tipado["Hora_Cita"].iloc[0] is None
    assert reporte.hubo_ajustes


def test_tipado_conserva_rowhash_si_ya_viene():
    df = pd.DataFrame([_fila_valida()])
    df["RowHash"] = [b"x" * 32]
    tipado, _ = aplicar_tipado_estricto(df)
    assert tipado["RowHash"].iloc[0] == b"x" * 32


def test_tipado_exige_las_columnas_del_esquema():
    with pytest.raises(ValueError, match="no trae columnas del esquema"):
        aplicar_tipado_estricto(pd.DataFrame([{"Cliente": "ACME"}]))


# ------------------------------------------- coherencia entre config.yaml y el esquema

def _config():
    import yaml
    ruta = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


def test_llave_negocio_igual_en_config_y_esquema():
    tabla = _config()["tablas"]["hechos_ontime"]
    assert tuple(tabla["llave_negocio"]) == esq.LLAVE_NEGOCIO


def test_columnas_hash_son_las_de_negocio_del_esquema():
    """El RowHash debe cubrir todas las columnas de negocio.

    Si se agrega una columna al esquema y se olvida en `columnas_hash`, un cambio en
    ese campo no dispararía el UPDATE del MERGE y el dato quedaría viejo en silencio.
    """
    tabla = _config()["tablas"]["hechos_ontime"]
    trazabilidad = {"source_file", "source_path", "source_sheet"}
    esperadas = [c for c in esq.columnas() if c not in trazabilidad]
    assert sorted(tabla["columnas_hash"]) == sorted(esperadas)


def test_columnas_extraidas_de_sharepoint_existen_en_el_esquema():
    faltantes = [c for c in _config()["columnas_esquema_origen"] if c not in esq.columnas()]
    assert not faltantes, f"columnas extraídas que el esquema no declara: {faltantes}"
