"""Valida el vaciado de la tabla de staging según los permisos de la cuenta.

Es la bifurcación que tumbó la corrida del 2026-09-10: la cuenta de servicio
`automatizacion` tiene db_datareader/db_datawriter, que no incluyen ALTER, y el
TRUNCATE falló con un error 1088 que parecía tabla faltante.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from carga.cargar_sql import _vaciar_staging

TABLA = "staging.HechosOnTime_Stage"


class CursorFalso:
    """Cursor mínimo: responde al chequeo de permisos y guarda lo que se ejecutó."""

    def __init__(self, tiene_alter):
        self._tiene_alter = tiene_alter
        self.ejecutado = []

    def execute(self, sql, *parametros):
        self.ejecutado.append(sql)
        self._ultimo = sql
        return self

    def fetchone(self):
        assert "HAS_PERMS_BY_NAME" in self._ultimo
        return (1 if self._tiene_alter else 0,)


def test_con_alter_usa_truncate():
    cursor = CursorFalso(tiene_alter=True)

    _vaciar_staging(cursor, TABLA)

    assert f"TRUNCATE TABLE {TABLA}" in cursor.ejecutado
    assert not any(s.startswith("DELETE") for s in cursor.ejecutado)


def test_sin_alter_cae_a_delete():
    cursor = CursorFalso(tiene_alter=False)

    _vaciar_staging(cursor, TABLA)

    assert f"DELETE FROM {TABLA}" in cursor.ejecutado
    assert not any(s.startswith("TRUNCATE") for s in cursor.ejecutado)


@pytest.mark.parametrize("tiene_alter", [True, False])
def test_la_tabla_se_consulta_como_parametro(tiene_alter):
    """El nombre de la tabla va como parámetro, no interpolado en el SQL del chequeo."""
    cursor = CursorFalso(tiene_alter=tiene_alter)

    _vaciar_staging(cursor, TABLA)

    chequeo = cursor.ejecutado[0]
    assert "HAS_PERMS_BY_NAME(?" in chequeo
    assert TABLA not in chequeo
