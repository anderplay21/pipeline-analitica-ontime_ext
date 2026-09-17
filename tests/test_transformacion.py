"""Valida las reglas críticas de transformación: clasificación On Time y RowHash."""

import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from transformacion.transformar import (
    calcular_hash_fila,
    calcular_ontime_acido,
    calcular_ontime_justificado,
    calcular_rowhash,
    transformar_y_calcular_hash,
)


def test_ontime_acido_a_tiempo():
    fila = {"FECHA ENTREGA": pd.Timestamp("2026-08-05"), "Fecha Estimada Entrega": pd.Timestamp("2026-08-06")}
    assert calcular_ontime_acido(fila) == "On Time"


def test_ontime_acido_tarde():
    fila = {"FECHA ENTREGA": pd.Timestamp("2026-08-10"), "Fecha Estimada Entrega": pd.Timestamp("2026-08-06")}
    assert calcular_ontime_acido(fila) == "Off Time"


def test_ontime_acido_sin_fecha_entrega():
    fila = {"FECHA ENTREGA": pd.NaT, "Fecha Estimada Entrega": pd.Timestamp("2026-08-06")}
    assert calcular_ontime_acido(fila) == "No Hay Fecha De Entrega"


def test_ontime_justificado_por_causal():
    causales = pd.DataFrame({
        "Descripcion": ["Entrega Perfecta (A Tiempo Y Sin Novedad)"],
        "On Time Justificado": ["JUSTIFICADO"],
    })
    fila = {
        "FECHA ENTREGA": pd.Timestamp("2026-08-10"),
        "Fecha Estimada Entrega": pd.Timestamp("2026-08-06"),
        "Novedad / Causal": "Entrega Perfecta (A Tiempo Y Sin Novedad)",
    }
    assert calcular_ontime_justificado(fila, causales) == "JUSTIFICADO"


def test_ontime_justificado_penalizado_sin_causal():
    causales = pd.DataFrame({"Descripcion": [], "On Time Justificado": []})
    fila = {
        "FECHA ENTREGA": pd.Timestamp("2026-08-10"),
        "Fecha Estimada Entrega": pd.Timestamp("2026-08-06"),
        "Novedad / Causal": None,
    }
    assert calcular_ontime_justificado(fila, causales) == "PENALIZADO"


def test_hash_es_determinista():
    fila = {"Cliente": "Acme", "Factura": "123", "Fecha de Factura": "2026-08-01"}
    columnas = ["Cliente", "Factura", "Fecha de Factura"]
    assert calcular_hash_fila(fila, columnas) == calcular_hash_fila(fila, columnas)


def test_hash_cambia_si_cambia_un_campo():
    columnas = ["Cliente", "Factura", "ESTATUS"]
    original = {"Cliente": "Acme", "Factura": "123", "ESTATUS": "ENTREGADO"}
    modificado = {"Cliente": "Acme", "Factura": "123", "ESTATUS": "EN RUTA"}
    assert calcular_hash_fila(original, columnas) != calcular_hash_fila(modificado, columnas)


def test_calcular_rowhash_agrega_columna():
    df = pd.DataFrame({
        "Cliente": ["Acme", "Beta"],
        "Factura": ["1", "2"],
        "ESTATUS": ["ENTREGADO", "EN RUTA"],
    })
    resultado = calcular_rowhash(df, ["Cliente", "Factura", "ESTATUS"])
    assert "RowHash" in resultado.columns
    assert resultado["RowHash"].iloc[0] != resultado["RowHash"].iloc[1]


# ------------------------------------------- flujo completo de transformación

def _config():
    ruta = Path(__file__).resolve().parents[1] / "config" / "config.yaml"
    return yaml.safe_load(ruta.read_text(encoding="utf-8"))


def _crudo(config, **cambios):
    """Fila con la forma que entrega la extracción (columnas del esquema de origen)."""
    fila = {col: None for col in config["columnas_esquema_origen"]}
    fila.update({
        "Cliente": "NESTLE DE COLOMBIA S.A",
        "Fecha de Factura": pd.Timestamp("2026-08-03"),
        "Factura": "3100746344",
        "Ciudad Origen": "COTA",
        "Ciudad Destino": "FUNZA",
    })
    fila.update(cambios)
    fila.update({"source_file": "x.xlsb", "source_path": "x/x.xlsb", "source_sheet": "Consolidado"})
    return fila


def test_fecha_entrega_invalida_se_reporta_y_no_clasifica_on_time():
    """Una fracción de hora de Excel en FECHA ENTREGA no es una fecha de entrega.

    Como serial cae en 1899: antes alcanzaba a clasificar la fila como "Off Time" y
    después quedaba en NULL, dejando el KPI sin respaldo. Ahora se descarta antes de
    clasificar y queda contada en el reporte, no anulada en silencio.
    """
    config = _config()
    crudo = pd.DataFrame([
        _crudo(config, **{"FECHA ENTREGA": 46237}),                  # entrega válida
        _crudo(config, Factura="OTRA-1", **{"FECHA ENTREGA": 0.2326388888888889}),
    ])

    listo, reporte = transformar_y_calcular_hash(crudo, config)

    assert reporte.anulados.get("FECHA ENTREGA") == 1
    invalida = listo[listo["Factura"] == "OTRA-1"].iloc[0]
    assert invalida["FECHA ENTREGA"] is None
    assert invalida["ON TIME ACIDO"] == "No Hay Fecha De Entrega"
    assert invalida["ON TIME JUSTIFICADO"] == "Sin Fecha De Entrega"


def test_flujo_completo_deja_columnas_tipadas_y_rowhash():
    from datetime import date

    config = _config()
    crudo = pd.DataFrame([_crudo(config, **{"Hora_Cita": 0.375})])
    listo, _ = transformar_y_calcular_hash(crudo, config)

    fila = listo.iloc[0]
    assert fila["Fecha de Factura"] == date(2026, 8, 3)
    assert str(fila["Hora_Cita"]) == "09:00:00"
    assert isinstance(fila["RowHash"], bytes)
    assert fila["Origen_Destino"] == "COTA_FUNZA"
