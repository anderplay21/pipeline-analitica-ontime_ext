"""Arma el elemento de la lista de ejecuciones sin tocar SharePoint.

Lo delicado son las fechas: SharePoint guarda UTC y muestra en la zona del sitio, así
que una fecha mal convertida aparece corrida un día en la lista.
"""

import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.reporte_sharepoint import construir_registro, registrar_ejecucion


def _utc(dt_local):
    """Lo que se espera para una hora local, sea cual sea la zona del equipo de pruebas."""
    return dt_local.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_corrida_exitosa_completa():
    inicio = datetime(2026, 11, 24, 15, 26, 2)
    fin = datetime(2026, 11, 24, 15, 49, 46)
    r = construir_registro(
        inicio, fin, True, date(2026, 10, 1), date(2026, 11, 24),
        libros_leidos=76, filas_extraidas=59090, filas_staging=54412,
        filas_insertadas=17631, filas_actualizadas=36607,
    )
    assert r["Title"] == "2026-11-24 15:26"
    assert r["Estado"] == "Exitosa"
    assert r["Duracion"] == 23.7
    assert r["FilasDescartadas"] == 59090 - 54412
    assert r["Inicio"] == _utc(inicio)
    assert "Error" not in r


def test_ventana_es_medianoche_local_no_utc():
    # Mandar "2026-10-01T00:00:00Z" haría que un sitio en Bogotá muestre 30-sep.
    r = construir_registro(datetime(2026, 11, 24, 8), datetime(2026, 11, 24, 9), True,
                           date(2026, 10, 1), date(2026, 11, 24))
    assert r["VentanaDesde"] == _utc(datetime(2026, 10, 1))
    assert r["VentanaHasta"] == _utc(datetime(2026, 11, 24))


def test_corrida_fallida_parcial_omite_cifras_desconocidas():
    r = construir_registro(datetime(2026, 11, 24, 8), datetime(2026, 11, 24, 8, 5), False,
                           filas_extraidas=100, error="Traceback ...\nRuntimeError: x")
    assert r["Estado"] == "Fallida"
    assert r["Error"].endswith("RuntimeError: x")
    # Sin filas en staging no se puede saber cuántas se descartaron: queda vacío, no 100.
    for campo in ("FilasDescartadas", "FilasInsertadas", "LibrosLeidos", "VentanaDesde"):
        assert campo not in r


def test_error_largo_conserva_el_final():
    r = construir_registro(datetime(2026, 1, 1), datetime(2026, 1, 1), False,
                           error="x" * 50000 + "CAUSA")
    assert len(r["Error"]) == 20000 and r["Error"].endswith("CAUSA")


def test_deshabilitado_no_intenta_conectar():
    assert registrar_ejecucion({"reporte_ejecuciones": {"habilitado": False}}, {}) is False
    assert registrar_ejecucion({}, {}) is False


def test_falla_de_sharepoint_no_lanza(monkeypatch):
    # Sin credenciales en el entorno falla al pedir el token: debe quedar en WARNING.
    for var in ("SP_CLIENT_ID", "SP_TENANT", "SP_CERT_THUMBPRINT", "SP_CERT_PATH"):
        monkeypatch.delenv(var, raising=False)
    avisos = []

    class Logger:
        def warning(self, msg, *args):
            avisos.append(msg % args)

    cfg = {"reporte_ejecuciones": {"habilitado": True,
                                   "sitio_url": "https://x.sharepoint.com/sites/y",
                                   "ruta_lista": "/sites/y/Lists/z"}}
    assert registrar_ejecucion(cfg, {"Title": "t"}, logger=Logger()) is False
    assert avisos and "No se pudo registrar" in avisos[0]
