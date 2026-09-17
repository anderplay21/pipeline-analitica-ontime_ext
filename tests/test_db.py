"""Valida la cadena de conexión en los dos modos de autenticación.

Es código que solo se ejerce al conectar contra el servidor, donde un error se ve
como un "Login failed" genérico; probarlo acá deja el diagnóstico del lado local.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.db import _entre_llaves, construir_connection_string


CONFIG_BASE = {
    "sql_server": {
        "servidor": "ICOLDATA01N",
        "base_datos": "AnaliticaOperativa",
        "driver": "ODBC Driver 18 for SQL Server",
        "trust_server_certificate": True,
        "timeout_conexion": 30,
    }
}


def _config(**cambios):
    cfg = {"sql_server": dict(CONFIG_BASE["sql_server"], **cambios)}
    return cfg


# ------------------------------------------------------- autenticación de Windows

def test_windows_no_pide_credenciales(monkeypatch):
    monkeypatch.delenv("SQL_USUARIO", raising=False)
    monkeypatch.delenv("SQL_PASSWORD", raising=False)

    cadena = construir_connection_string(_config(trusted_connection=True))

    assert "Trusted_Connection=yes" in cadena
    assert "UID=" not in cadena and "PWD=" not in cadena


# ------------------------------------------------------------------- login SQL

def test_login_sql_usa_las_credenciales_del_env(monkeypatch):
    monkeypatch.setenv("SQL_USUARIO", "etl_ontime")
    monkeypatch.setenv("SQL_PASSWORD", "clave-simple")

    cadena = construir_connection_string(_config(trusted_connection=False))

    assert "UID=etl_ontime" in cadena
    assert "PWD={clave-simple}" in cadena
    assert "Trusted_Connection" not in cadena


@pytest.mark.parametrize("clave", ["cla;ve", "cla=ve", "cla ve", "cla{ve", "cla}ve"])
def test_clave_con_caracteres_especiales_queda_delimitada(monkeypatch, clave):
    """`;` y `=` sin delimitar cortan el atributo y dan un "Login failed" engañoso."""
    monkeypatch.setenv("SQL_USUARIO", "sa")
    monkeypatch.setenv("SQL_PASSWORD", clave)

    cadena = construir_connection_string(_config(trusted_connection=False))

    # Lo que va después de PWD= es un único atributo delimitado, no varios.
    pwd = cadena.split("PWD=")[1]
    assert pwd.startswith("{")
    assert pwd[: pwd.index("}") + 1].endswith("}")


def test_llave_de_cierre_se_escapa_duplicada():
    assert _entre_llaves("a}b") == "{a}}b}"


@pytest.mark.parametrize("faltante", ["SQL_USUARIO", "SQL_PASSWORD"])
def test_credencial_faltante_falla_con_mensaje_claro(monkeypatch, faltante):
    monkeypatch.setenv("SQL_USUARIO", "sa")
    monkeypatch.setenv("SQL_PASSWORD", "clave")
    monkeypatch.delenv(faltante)

    with pytest.raises(RuntimeError, match=faltante):
        construir_connection_string(_config(trusted_connection=False))
