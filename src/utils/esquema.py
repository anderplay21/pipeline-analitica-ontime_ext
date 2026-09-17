"""Contrato de tipos de la tabla HechosOnTime: única fuente de verdad.

De aquí salen tanto el DDL de SQL Server (`sql/02` y `sql/03`, generables con
`python -m utils.esquema`) como la validación/coerción que corre antes de cada
carga (`transformacion/tipado.py`). Definir el tipo en un solo lugar evita que el
DDL y el código Python se desincronicen, que es la causa típica de los errores de
truncamiento y de tipo al insertar.

Los anchos y tipos provienen de la especificación funcional entregada por negocio
(el CSV "tipo de datos"), validados contra los largos reales de los datos.
"""

from dataclasses import dataclass

# Tipos soportados por la capa de tipado
TEXTO = "nvarchar"
FECHA = "date"
HORA = "time"
ENTERO = "int"
DECIMAL = "decimal"


@dataclass(frozen=True)
class Columna:
    """Una columna del modelo, con su tipo destino en SQL Server."""

    nombre: str
    tipo: str
    largo: int | None = None      # solo para nvarchar
    nulo: bool = True
    precision: int | None = None  # solo para decimal: dígitos totales
    escala: int | None = None     # solo para decimal: dígitos a la derecha del punto

    @property
    def tipo_sql(self) -> str:
        """El tipo tal como se escribe en T-SQL."""
        if self.tipo == TEXTO:
            return f"NVARCHAR({self.largo})"
        if self.tipo == DECIMAL:
            return f"DECIMAL({self.precision},{self.escala})"
        return self.tipo.upper()

    @property
    def restriccion_sql(self) -> str:
        return "NOT NULL" if not self.nulo else "NULL"

    @property
    def sql(self) -> str:
        """Fragmento DDL de la columna, con el nombre siempre entre corchetes."""
        return f"[{self.nombre}] {self.tipo_sql} {self.restriccion_sql}"


# Orden significativo: es el orden físico de las columnas en las tablas.
ESQUEMA_HECHOS_ONTIME: tuple[Columna, ...] = (
    Columna("Cliente", TEXTO, 50, nulo=False),
    Columna("Cl/ Movimiento", TEXTO, 10),
    Columna("Cl Doc FWO", TEXTO, 10),
    Columna("Fecha de Factura", FECHA, nulo=False),
    Columna("Ciudad Origen", TEXTO, 50),
    Columna("Centro Origen", TEXTO, 50),
    Columna("Destinatario", TEXTO, 100),
    Columna("Ciudad Destino", TEXTO, 50),
    Columna("Centro Destino", TEXTO, 50),
    Columna("Factura", TEXTO, 50, nulo=False),
    Columna("Carta Porte", TEXTO, 50),
    Columna("Orden de Compra", TEXTO, 50),
    Columna("Orden de Pedido", TEXTO, 50),
    Columna("Remision", TEXTO, 50),
    Columna("Ultima Ubicación", TEXTO, 50),
    Columna("Fecha Programada De Entrega", FECHA),
    Columna("Hora_Cita", HORA),
    Columna("Tipo Producto", TEXTO, 100),
    Columna("ESTATUS", TEXTO, 100),
    Columna("Novedad / Causal", TEXTO, 100),
    Columna("FECHA ENTREGA", FECHA),
    Columna("FECHA DE CITA", FECHA),
    Columna("DOCUWARE", TEXTO, 50),
    Columna("Lead Time", ENTERO),
    Columna("Fecha Estimada Entrega", FECHA),
    Columna("RESPONSABLE", TEXTO, 50),
    Columna("Origen_Destino", TEXTO, 50),
    Columna("Nombre del mes", TEXTO, 20),
    Columna("ON TIME ACIDO", TEXTO, 50),
    Columna("ON TIME JUSTIFICADO", TEXTO, 50),
    Columna("Dir/ Cliente", TEXTO, 200),
    Columna("Cajas", ENTERO),
    Columna("Kilos", DECIMAL, precision=12, escala=2),
    Columna("source_file", TEXTO, 100),
    Columna("source_path", TEXTO, 300),
    Columna("source_sheet", TEXTO, 100),
)

# Llave de negocio: identifica un registro de forma única (ver plan, Fase 0).
LLAVE_NEGOCIO = ("Cliente", "Fecha de Factura", "Factura")

# Rango de fechas aceptable. Una fecha fuera de aquí es basura del origen (típico:
# la fracción de hora de Excel interpretada como serial, que cae en 1899).
FECHA_MINIMA = "2020-01-01"
ANIOS_FUTURO_MAXIMO = 2


def columnas() -> tuple[str, ...]:
    """Nombres de las columnas de negocio, en orden."""
    return tuple(c.nombre for c in ESQUEMA_HECHOS_ONTIME)


def por_nombre() -> dict[str, Columna]:
    return {c.nombre: c for c in ESQUEMA_HECHOS_ONTIME}


def columnas_de_tipo(tipo: str) -> tuple[str, ...]:
    return tuple(c.nombre for c in ESQUEMA_HECHOS_ONTIME if c.tipo == tipo)


_ANCHO_NOMBRE = max(
    len("[FechaUltimaActualizacion]"),
    max(len(f"[{c.nombre}]") for c in ESQUEMA_HECHOS_ONTIME),
) + 1


def _linea_ddl(nombre: str, tipo_sql: str, resto: str, sangria: str = "        ") -> str:
    return f"{sangria}{f'[{nombre}]'.ljust(_ANCHO_NOMBRE)} {tipo_sql.ljust(14)} {resto},"


def _ddl_columnas(sangria: str = "        ") -> str:
    return "\n".join(
        _linea_ddl(c.nombre, c.tipo_sql, c.restriccion_sql, sangria)
        for c in ESQUEMA_HECHOS_ONTIME
    )


def ddl_tabla_core() -> str:
    """DDL de core.HechosOnTime (histórico completo, con columnas de control)."""
    return f"""-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- Tabla Core: histórico completo del consolidado On Time, nunca se trunca.
-- Llave de negocio: {' + '.join(LLAVE_NEGOCIO)}.

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HechosOnTime' AND schema_id = SCHEMA_ID('core'))
BEGIN
    CREATE TABLE core.HechosOnTime (
{_ddl_columnas()}
        -- columnas de control técnico:
{_linea_ddl("RowHash", "BINARY(32)", "NOT NULL")}
{_linea_ddl("FechaCargaInicial", "DATETIME2", "NOT NULL DEFAULT SYSDATETIME()")}
{_linea_ddl("FechaUltimaActualizacion", "DATETIME2", "NOT NULL DEFAULT SYSDATETIME()")}
        CONSTRAINT PK_HechosOnTime PRIMARY KEY ({', '.join(f'[{c}]' for c in LLAVE_NEGOCIO)})
    );

    CREATE NONCLUSTERED INDEX IX_HechosOnTime_Fecha ON core.HechosOnTime ([Fecha de Factura]);
END
GO
"""


def ddl_tabla_staging() -> str:
    """DDL de staging.HechosOnTime_Stage (desechable, se trunca en cada corrida)."""
    return f"""-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- Tabla Staging: espejo de core.HechosOnTime, desechable (se trunca y recarga en cada corrida).
-- Solo contiene la ventana móvil (mes actual + mes anterior), nunca el histórico completo.

USE AnaliticaOperativa;
GO

IF NOT EXISTS (SELECT 1 FROM sys.tables WHERE name = 'HechosOnTime_Stage' AND schema_id = SCHEMA_ID('staging'))
BEGIN
    CREATE TABLE staging.HechosOnTime_Stage (
{_ddl_columnas()}
{_linea_ddl("RowHash", "BINARY(32)", "NOT NULL").rstrip(",")}
    );
END
GO
"""


def sql_merge_hechos_ontime() -> str:
    """Script del MERGE staging -> core.

    Se genera en vez de escribirse a mano porque enumera cada columna tres veces
    (UPDATE SET, lista del INSERT y lista del VALUES) y el pareo entre las dos
    últimas es posicional. Olvidar una columna en el UPDATE SET no produce ningún
    error: la fila nueva se inserta bien y la existente nunca recibe el valor. Al
    derivar las tres listas del mismo iterable, ese desfase deja de ser posible.
    """
    # El INSERT nombra todas las columnas del esquema más la huella; FechaCargaInicial
    # y FechaUltimaActualizacion las pone SQL Server con su DEFAULT.
    insertables = columnas() + ("RowHash",)
    # La llave de negocio no se actualiza: por definición ya coincide en ambos lados.
    actualizables = tuple(c for c in columnas() if c not in LLAVE_NEGOCIO) + ("RowHash",)

    condicion_llave = "\n".join(
        f"{'    ON ' if i == 0 else '   AND '}destino.[{c}] = origen.[{c}]"
        for i, c in enumerate(LLAVE_NEGOCIO)
    )
    asignaciones = "\n".join(f"        destino.[{c}] = origen.[{c}]," for c in actualizables)
    lista_insert = "\n".join(f"        [{c}]," for c in insertables).rstrip(",")
    lista_values = "\n".join(f"        origen.[{c}]," for c in insertables).rstrip(",")

    return f"""-- GENERADO por src/utils/esquema.py -- no editar a mano.
-- Regenerar con:  python -m utils.esquema
-- MERGE staging -> core para HechosOnTime
-- Llave de negocio: {' + '.join(LLAVE_NEGOCIO)}.
-- IMPORTANTE: no incluye WHEN NOT MATCHED BY SOURCE.
-- Staging solo trae la ventana móvil (mes actual + anterior); si se activara esa rama
-- se borraría/marcaría como eliminado todo el histórico que no está en esos meses.

USE AnaliticaOperativa;
GO

DECLARE @ResultadoMerge TABLE (Accion NVARCHAR(10));

MERGE core.HechosOnTime AS destino
USING staging.HechosOnTime_Stage AS origen
{condicion_llave}
WHEN MATCHED AND destino.RowHash <> origen.RowHash THEN
    UPDATE SET
{asignaciones}
        destino.[FechaUltimaActualizacion] = SYSDATETIME()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (
{lista_insert}
    )
    VALUES (
{lista_values}
    )
OUTPUT $action INTO @ResultadoMerge;

SELECT
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'INSERT') AS FilasInsertadas,
    (SELECT COUNT(*) FROM @ResultadoMerge WHERE Accion = 'UPDATE') AS FilasActualizadas;
GO
"""


# Archivos SQL que se generan desde este módulo. Los consume también la prueba que
# verifica que lo que hay en disco corresponde al esquema actual.
ARCHIVOS_GENERADOS = (
    ("02_crear_tablas_core.sql", ddl_tabla_core),
    ("03_crear_tablas_staging.sql", ddl_tabla_staging),
    ("merges/merge_hechos_ontime.sql", sql_merge_hechos_ontime),
)


if __name__ == "__main__":
    from pathlib import Path

    carpeta_sql = Path(__file__).resolve().parents[2] / "sql"
    for nombre, generador in ARCHIVOS_GENERADOS:
        (carpeta_sql / nombre).write_text(generador(), encoding="utf-8")
        print(f"Regenerado sql/{nombre}")
