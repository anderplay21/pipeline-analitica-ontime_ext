"""Registro de cada corrida en una lista de SharePoint (bitácora visible para el negocio).

Complementa core.ETL_Control: la tabla SQL es la bitácora técnica, la lista es la que
consulta la gente sin entrar a SQL Server. Usa la misma app de Entra con certificado
que la extracción (SP_TENANT, SP_CLIENT_ID, SP_CERT_THUMBPRINT, SP_CERT_PATH), que debe
tener permiso de escritura (Sites.Selected, rol write) sobre el sitio de la lista.

Registrar es "mejor esfuerzo": si SharePoint no responde, se deja un WARNING en el log y
la corrida sigue su curso. Una falla de la bitácora no debe tumbar ni ocultar la carga.
"""

import os
from datetime import date, datetime, timezone
from pathlib import Path

import requests

# Columna "Error" es de varias líneas (límite de SharePoint ~63.999 caracteres). Un
# traceback cabe de sobra; el tope solo protege de un mensaje anómalo gigante.
MAX_ERROR = 20000


def _a_utc_iso(valor):
    """Fecha/hora local -> ISO UTC, que es lo que espera SharePoint.

    Una fecha sola se toma como medianoche LOCAL: si se mandara "2026-10-01T00:00:00Z",
    el sitio (zona Bogotá) la mostraría como 30-sep 19:00.
    """
    if valor is None:
        return None
    if isinstance(valor, date) and not isinstance(valor, datetime):
        valor = datetime(valor.year, valor.month, valor.day)
    # astimezone() sobre una hora naive la interpreta como hora local del servidor.
    return valor.astimezone().astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def construir_registro(inicio, fin, exitosa, fecha_desde=None, fecha_hasta=None,
                       libros_leidos=None, filas_extraidas=None, filas_staging=None,
                       filas_insertadas=None, filas_actualizadas=None, error=None):
    """Arma el cuerpo del elemento con los nombres internos de las columnas de la lista.

    FilasDescartadas = extraídas - cargadas a staging: incluye las filas sin llave de
    negocio y los duplicados de llave, es decir, todo lo que salió de SharePoint y no
    llegó a la base. Solo se calcula si ambas cifras se conocen.
    """
    descartadas = None
    if filas_extraidas is not None and filas_staging is not None:
        descartadas = filas_extraidas - filas_staging

    registro = {
        "Title": inicio.strftime("%Y-%m-%d %H:%M"),
        "Estado": "Exitosa" if exitosa else "Fallida",
        "Inicio": _a_utc_iso(inicio),
        "Fin": _a_utc_iso(fin),
        "Duracion": round((fin - inicio).total_seconds() / 60, 1),
        "VentanaDesde": _a_utc_iso(fecha_desde),
        "VentanaHasta": _a_utc_iso(fecha_hasta),
        "LibrosLeidos": libros_leidos,
        "FilasExtraidas": filas_extraidas,
        "FilasDescartadas": descartadas,
        "FilasInsertadas": filas_insertadas,
        "FilasActualizadas": filas_actualizadas,
        "Error": error[-MAX_ERROR:] if error else None,
    }
    # Los campos sin dato no se mandan: quedan vacíos en la lista en vez de 0.
    return {k: v for k, v in registro.items() if v is not None}


def _token(host):
    import msal

    app = msal.ConfidentialClientApplication(
        os.environ["SP_CLIENT_ID"],
        authority=f"https://login.microsoftonline.com/{os.environ['SP_TENANT']}",
        client_credential={
            "thumbprint": os.environ["SP_CERT_THUMBPRINT"].strip(),
            "private_key": Path(os.environ["SP_CERT_PATH"].strip().strip('"')).read_text(),
        },
    )
    resultado = app.acquire_token_for_client(scopes=[f"https://{host}/.default"])
    if "access_token" not in resultado:
        raise RuntimeError(resultado.get("error_description", str(resultado)))
    return resultado["access_token"]


def registrar_ejecucion(config, registro, logger=None):
    """Crea un elemento en la lista configurada. Nunca lanza excepción."""
    cfg = config.get("reporte_ejecuciones", {})
    if not cfg.get("habilitado", False):
        return False

    try:
        sitio = cfg["sitio_url"].rstrip("/")
        host = sitio.split("/")[2]
        ruta_lista = cfg["ruta_lista"]  # ruta relativa al servidor: estable aunque se renombre la lista
        respuesta = requests.post(
            f"{sitio}/_api/web/GetList('{ruta_lista}')/items",
            headers={
                "Authorization": f"Bearer {_token(host)}",
                "Accept": "application/json;odata=nometadata",
                "Content-Type": "application/json;odata=nometadata",
            },
            json=registro,
            timeout=60,
        )
        if not respuesta.ok:
            raise RuntimeError(f"HTTP {respuesta.status_code}: {respuesta.text[:500]}")
        if logger is not None:
            logger.info("Corrida registrada en SharePoint (elemento %s)", respuesta.json().get("Id"))
        return True
    except Exception as exc:
        if logger is not None:
            logger.warning("No se pudo registrar la corrida en SharePoint: %s", exc)
        return False
