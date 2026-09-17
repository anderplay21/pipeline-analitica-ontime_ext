"""Verifica que el SQL versionado corresponda al esquema actual.

`sql/02`, `sql/03` y el MERGE se generan desde `utils.esquema`. Nada impedía que
alguien editara uno a mano, o que se cambiara el esquema sin regenerarlos: el
desajuste no se notaba hasta la corrida, y en el caso del MERGE no se notaba nunca
(una columna ausente del UPDATE SET se inserta en filas nuevas y jamás se actualiza
en las existentes, sin error ni log).
"""

import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / "src"))

from utils import esquema as esq


@pytest.mark.parametrize(
    "nombre, generador",
    esq.ARCHIVOS_GENERADOS,
    ids=[nombre for nombre, _ in esq.ARCHIVOS_GENERADOS],
)
def test_archivo_sql_corresponde_al_esquema(nombre, generador):
    ruta = RAIZ / "sql" / nombre

    assert ruta.read_text(encoding="utf-8") == generador(), (
        f"sql/{nombre} está desincronizado del esquema. "
        f"Regenera con:  cd src && python -m utils.esquema"
    )


def _bloque(texto, inicio, fin):
    desde = texto.index(inicio)
    return texto[desde:texto.index(fin, desde)]


def test_el_merge_actualiza_todas_las_columnas_menos_la_llave():
    """Una columna ausente del UPDATE SET nunca se actualiza en core: el fallo silencioso.

    Red de seguridad por si el generador se reescribe: valida la intención (que ninguna
    columna se quede fuera) y no el texto exacto, que ya cubre el test de arriba.
    """
    merge = esq.sql_merge_hechos_ontime()
    update = _bloque(merge, "UPDATE SET", "WHEN NOT MATCHED")
    condicion = _bloque(merge, "    ON ", "WHEN MATCHED")

    for columna in esq.columnas():
        if columna in esq.LLAVE_NEGOCIO:
            # La llave no se actualiza: por definición ya coincide en ambos lados.
            assert f"destino.[{columna}] = origen.[{columna}]" in condicion
            assert f"destino.[{columna}]" not in update
        else:
            assert f"destino.[{columna}] = origen.[{columna}]" in update, (
                f"[{columna}] no se actualiza en el MERGE: quedaría vieja en core"
            )

    assert "destino.[RowHash] = origen.[RowHash]" in update
    assert "destino.[FechaUltimaActualizacion] = SYSDATETIME()" in update


def test_insert_y_values_del_merge_estan_alineados():
    """El pareo INSERT/VALUES es posicional: un desfase daría datos en la columna equivocada."""
    merge = esq.sql_merge_hechos_ontime()
    bloque_insert = merge[merge.index("INSERT ("):merge.index("VALUES (")]
    bloque_values = merge[merge.index("VALUES ("):merge.index("OUTPUT $action")]

    def columnas_de(bloque):
        return [
            linea.strip().rstrip(",").replace("origen.", "").strip("[]")
            for linea in bloque.splitlines()
            if linea.strip().startswith(("[", "origen."))
        ]

    assert columnas_de(bloque_insert) == columnas_de(bloque_values)
    assert columnas_de(bloque_insert) == list(esq.columnas()) + ["RowHash"]
