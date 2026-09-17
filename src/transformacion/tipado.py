"""Tipado estricto antes de cargar a SQL Server.

Todo lo que llega de los Excel de SharePoint es texto libre: una misma columna trae
enteros, flotantes, horas de Excel como fracción de día, fechas seriales y notas
escritas a mano. Esta capa convierte cada columna al tipo que declara
`utils.esquema` y deja el DataFrame listo para insertar sin sorpresas.

Política ante un dato que no cumple (definida con negocio):
  - texto más largo que el ancho declarado -> se recorta y se registra
  - fecha / hora / entero no interpretable  -> queda en NULL y se registra
  - fila sin llave de negocio completa      -> se descarta (SQL la rechazaría) y se registra
La corrida nunca se cae por un dato sucio, pero cada ajuste queda contado y logueado.
"""

import math
import numbers
import re
import unicodedata
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import pandas as pd

from utils import esquema as esq

# Textos que en la práctica significan "sin dato" y no deben viajar como valor.
BASURA_TEXTO = {
    "", "-", "--", "nan", "nat", "none", "null", "n/a", "na", "s/d", "sin dato",
    "#n/d", "#n/a", "#value!", "#ref!", "#div/0!", "#name?", "#nombre?", "#¡valor!",
    "#¡ref!", "#¡div/0!", "#¡nombre?",
}

# Una sola hora, opcionalmente con minutos y meridiano. Deliberadamente estricto:
# un rango ("6:00 - 9:30 AM") o una nota ("LLAMAR ANTES") no matchea y queda en NULL.
PATRON_HORA = re.compile(
    r"^(?P<hora>\d{1,2})(?:[:.h](?P<minuto>\d{1,2}))?\s*(?P<meridiano>am|pm|m)?$"
)

# Número con separador de miles bien formado: grupos de exactamente 3 dígitos
# ("12.500", "1.234.567"). Sirve para distinguirlo de un decimal escrito con punto.
# El primer grupo no puede empezar en 0: "0.125" es un decimal, nadie escribe 125 así.
PATRON_MILES = re.compile(r"^-?[1-9]\d{0,2}([.,]\d{3})+$")


class ReporteTipado:
    """Acumula los ajustes hechos durante el tipado, para log y auditoría."""

    def __init__(self):
        self.truncados: dict[str, int] = {}
        self.anulados: dict[str, int] = {}
        self.filas_descartadas: int = 0
        self.motivos_descarte: dict[str, int] = {}

    @property
    def hubo_ajustes(self) -> bool:
        return bool(self.truncados or self.anulados or self.filas_descartadas)

    def resumen(self) -> str:
        partes = []
        if self.filas_descartadas:
            detalle = ", ".join(f"{k}={v}" for k, v in sorted(self.motivos_descarte.items()))
            partes.append(f"filas descartadas por llave incompleta: {self.filas_descartadas} ({detalle})")
        for col, n in sorted(self.truncados.items()):
            partes.append(f"'{col}': {n} valor(es) recortado(s) al ancho máximo")
        for col, n in sorted(self.anulados.items()):
            partes.append(f"'{col}': {n} valor(es) no interpretable(s) -> NULL")
        return "; ".join(partes) if partes else "sin ajustes"


def _es_vacio(valor) -> bool:
    if valor is None or valor is pd.NA or valor is pd.NaT:
        return True
    if isinstance(valor, float) and math.isnan(valor):
        return True
    return False


def _normalizar_espacios(texto: str) -> str:
    """Quita espacios raros (NBSP y demás), saltos de línea y espacios repetidos."""
    # NFKC convierte el espacio duro (\xa0) y otros separadores unicode en espacio normal.
    texto = unicodedata.normalize("NFKC", texto)
    return re.sub(r"\s+", " ", texto).strip()


def _es_numero(valor) -> bool:
    """True para numéricos de Python y de numpy, excluyendo bool.

    Se usa `numbers` y no `isinstance(valor, (int, float))` porque pandas entrega
    numpy.int64 cuando la columna del Excel es homogénea numérica, y numpy.int64 no
    es instancia de int: con la comprobación ingenua, un serial de fecha se iría por
    la rama de texto y se interpretaría mal.
    """
    return isinstance(valor, numbers.Number) and not isinstance(valor, bool)


def a_texto(valor, largo: int) -> tuple[str | None, bool]:
    """Devuelve (texto_limpio_o_None, fue_truncado)."""
    if _es_vacio(valor):
        return None, False

    if isinstance(valor, bool):
        texto = "1" if valor else "0"
    elif isinstance(valor, numbers.Integral):
        texto = str(int(valor))
    elif _es_numero(valor):
        # Los códigos numéricos (factura, remisión) llegan como float y no deben
        # quedar como "2105035559.0".
        numero = float(valor)
        texto = str(int(numero)) if numero.is_integer() else str(numero)
    elif isinstance(valor, (pd.Timestamp, datetime)):
        texto = valor.strftime("%Y-%m-%d")
    elif isinstance(valor, date):
        texto = valor.isoformat()
    elif isinstance(valor, time):
        texto = valor.strftime("%H:%M:%S")
    else:
        texto = str(valor)

    texto = _normalizar_espacios(texto)
    if texto.lower() in BASURA_TEXTO:
        return None, False

    if len(texto) > largo:
        return texto[:largo], True
    return texto, False


def a_fecha(valor) -> date | None:
    """Convierte a `date`, aceptando serial de Excel, datetime o texto.

    Descarta fechas fuera del rango razonable del negocio: una fracción de hora de
    Excel interpretada como serial cae en 1899 y no es una fecha real.
    """
    if _es_vacio(valor):
        return None

    convertido = None
    try:
        if isinstance(valor, pd.Timestamp):
            convertido = valor.to_pydatetime().date()
        elif isinstance(valor, datetime):
            convertido = valor.date()
        elif isinstance(valor, date):
            convertido = valor
        elif isinstance(valor, bool):
            return None
        elif _es_numero(valor):
            numero = float(valor)
            if not math.isfinite(numero) or numero <= 0:
                return None
            convertido = (pd.Timestamp("1899-12-30") + pd.Timedelta(days=numero)).date()
        else:
            texto = _normalizar_espacios(str(valor))
            if not texto or texto.lower() in BASURA_TEXTO:
                return None
            # dayfirst: el origen es Colombia (d/m/aaaa).
            convertido = pd.to_datetime(texto, errors="coerce", dayfirst=True)
            if pd.isna(convertido):
                return None
            convertido = convertido.date()
    except (ValueError, OverflowError, pd.errors.OutOfBoundsDatetime):
        return None

    minima = date.fromisoformat(esq.FECHA_MINIMA)
    maxima = date.today() + timedelta(days=365 * esq.ANIOS_FUTURO_MAXIMO)
    if not (minima <= convertido <= maxima):
        return None
    return convertido


def _hora_desde_fraccion(fraccion: float) -> time | None:
    """Convierte la fracción de día de Excel (0.375 = 09:00) en `time`."""
    if not math.isfinite(fraccion):
        return None
    fraccion = fraccion % 1
    if fraccion == 0:
        # Serial entero (una fecha sin hora) o cero: no hay información de hora.
        return None
    total_segundos = int(round(fraccion * 24 * 3600))
    total_segundos %= 24 * 3600
    return time(total_segundos // 3600, (total_segundos % 3600) // 60, total_segundos % 60)


def a_hora(valor) -> time | None:
    """Convierte a `time`. Devuelve None si el valor no es una hora única interpretable.

    Cubre los formatos que realmente aparecen en los consolidados: horas de Excel
    como fracción de día (0.375), horas de texto ('7:00 AM', '7.00am', '18:30') y
    celdas ya tipadas como hora. El texto libre (rangos, notas, 'TURNO') queda en
    NULL: se acordó con negocio priorizar una columna `time` limpia y utilizable.
    """
    if _es_vacio(valor):
        return None

    if isinstance(valor, time):
        return None if (valor.hour == 0 and valor.minute == 0 and valor.second == 0) else valor
    if isinstance(valor, (pd.Timestamp, datetime)):
        hora = valor.time()
        return None if (hora.hour == 0 and hora.minute == 0 and hora.second == 0) else hora
    if isinstance(valor, bool):
        return None
    if _es_numero(valor):
        return _hora_desde_fraccion(float(valor))

    texto = _normalizar_espacios(str(valor)).lower()
    if not texto or texto in BASURA_TEXTO:
        return None

    # "0.375" guardado como texto.
    try:
        return _hora_desde_fraccion(float(texto.replace(",", ".")))
    except ValueError:
        pass

    # Normaliza el meridiano: "a.m.", "a m", "p.m." -> "am" / "pm".
    texto = re.sub(r"\ba\.?\s?m\.?", "am", texto)
    texto = re.sub(r"\bp\.?\s?m\.?", "pm", texto)
    texto = texto.replace(" ", "").rstrip(".")

    coincidencia = PATRON_HORA.match(texto)
    if not coincidencia:
        return None

    hora = int(coincidencia.group("hora"))
    minuto = int(coincidencia.group("minuto") or 0)
    meridiano = coincidencia.group("meridiano")

    if minuto > 59:
        return None
    if meridiano in ("am", "pm"):
        if not 1 <= hora <= 12:
            return None
        if meridiano == "pm" and hora != 12:
            hora += 12
        elif meridiano == "am" and hora == 12:
            hora = 0
    elif meridiano == "m":
        # "12:00 m" es mediodía en el uso local; con otra hora es ambiguo.
        if hora != 12:
            return None
    if hora > 23:
        return None
    if hora == 0 and minuto == 0:
        return None
    return time(hora, minuto)


def _texto_a_float(valor) -> float | None:
    """Interpreta como número el texto libre que traen las celdas numéricas.

    Los Excel llegan con formato colombiano y con restos de edición manual: separador
    de miles, coma decimal, símbolo de moneda. Reglas, en orden:

    1. Si aparecen los dos separadores, el decimal es el que va de último
       ("1.234,56" -> 1234.56 y "1,234.56" -> 1234.56).
    2. Si la agrupación de miles está bien formada, es separador de miles:
       "12.500" son doce mil quinientos, no doce con medio, y "1.234.567" es un millón
       largo. Esto lo confirmó negocio: en estos archivos el punto siempre agrupa miles.
    3. Si un separador aparece repetido, también es de miles, aunque la agrupación esté
       mal escrita ("1.23.456").
    4. Una coma sola es el separador decimal ("12,5" -> 12.5).
    5. Un punto solo que no agrupa miles se respeta como punto decimal ("12.5" -> 12.5):
       en formato local eso se habría escrito con coma, así que viene de un archivo en
       formato inglés.
    """
    texto = _normalizar_espacios(str(valor))
    if not texto or texto.lower() in BASURA_TEXTO:
        return None

    texto = texto.replace("$", "").replace(" ", "").replace(" ", "")
    comas, puntos = texto.count(","), texto.count(".")
    if comas and puntos:
        if texto.rfind(",") > texto.rfind("."):
            texto = texto.replace(".", "").replace(",", ".")
        else:
            texto = texto.replace(",", "")
    elif PATRON_MILES.match(texto):
        texto = texto.replace(".", "").replace(",", "")
    elif puntos > 1:
        texto = texto.replace(".", "")
    elif comas > 1:
        texto = texto.replace(",", "")
    elif comas == 1:
        texto = texto.replace(",", ".")

    try:
        numero = float(texto)
    except (ValueError, OverflowError):
        return None
    return numero if math.isfinite(numero) else None


def a_entero(valor) -> int | None:
    if _es_vacio(valor):
        return None
    if isinstance(valor, bool):
        return int(valor)
    try:
        if _es_numero(valor):
            if not math.isfinite(float(valor)):
                return None
            numero = int(round(float(valor)))
        else:
            numero = _texto_a_float(valor)
            if numero is None:
                return None
            numero = int(round(numero))
    except (ValueError, OverflowError):
        return None
    # Rango de INT en SQL Server.
    if not -2_147_483_648 <= numero <= 2_147_483_647:
        return None
    return numero


def a_decimal(valor, precision: int, escala: int) -> Decimal | None:
    """Convierte a Decimal cuantizado a la escala declarada.

    Devuelve Decimal y no float por dos razones. Una: el RowHash se calcula sobre
    `str(valor)`, así que la representación tiene que ser estable entre corridas —
    cuantizado, 12.5 y 12.50 dan siempre "12.50" y el MERGE no actualiza filas que no
    cambiaron. Dos: un float no representa exactamente los decimales y SQL Server
    recibiría un valor con ruido en el último dígito.

    Un valor que no quepa en la precisión declarada queda en NULL, igual que una fecha
    ilegible: es preferible el hueco contado en el log a un dato truncado en silencio.
    """
    if _es_vacio(valor) or isinstance(valor, bool):
        return None

    if isinstance(valor, Decimal):
        numero = valor
    else:
        crudo = float(valor) if _es_numero(valor) else _texto_a_float(valor)
        if crudo is None or not math.isfinite(crudo):
            return None
        # Vía str: Decimal(0.1) arrastra el error binario del float, Decimal("0.1") no.
        numero = Decimal(str(crudo))

    try:
        cuantizado = numero.quantize(Decimal(1).scaleb(-escala), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None

    # DECIMAL(precision, escala) admite (precision - escala) dígitos a la izquierda.
    if abs(cuantizado) >= Decimal(10) ** (precision - escala):
        return None
    return cuantizado


def aplicar_tipado_estricto(df, logger=None):
    """Convierte cada columna al tipo declarado en `utils.esquema`.

    Devuelve (df_tipado, reporte). El DataFrame resultante trae exactamente las
    columnas del esquema, en orden, con tipos nativos de Python (str/date/time/int/None),
    de modo que pyodbc no tenga que adivinar nada al insertar.
    """
    reporte = ReporteTipado()
    faltantes = [c for c in esq.columnas() if c not in df.columns]
    if faltantes:
        raise ValueError(f"El DataFrame no trae columnas del esquema: {faltantes}")

    salida = {}
    for columna in esq.ESQUEMA_HECHOS_ONTIME:
        serie = df[columna.nombre]
        nombre = columna.nombre

        if columna.tipo == esq.TEXTO:
            resultados = [a_texto(v, columna.largo) for v in serie]
            valores = [r[0] for r in resultados]
            truncados = sum(1 for r in resultados if r[1])
            if truncados:
                reporte.truncados[nombre] = truncados
        elif columna.tipo == esq.FECHA:
            valores = [a_fecha(v) for v in serie]
        elif columna.tipo == esq.HORA:
            valores = [a_hora(v) for v in serie]
        elif columna.tipo == esq.ENTERO:
            valores = [a_entero(v) for v in serie]
        elif columna.tipo == esq.DECIMAL:
            valores = [a_decimal(v, columna.precision, columna.escala) for v in serie]
        else:
            raise ValueError(f"Tipo no soportado en el esquema: {columna.tipo}")

        if columna.tipo != esq.TEXTO:
            # Un valor que venía con dato y quedó en None es un dato que no se pudo
            # interpretar: hay que reportarlo, no silenciarlo.
            anulados = sum(
                1 for original, nuevo in zip(serie, valores)
                if nuevo is None and not _es_vacio(original)
            )
            if anulados:
                reporte.anulados[nombre] = anulados

        salida[nombre] = valores

    tipado = pd.DataFrame(salida, index=df.index)

    # Las columnas NOT NULL son la llave de negocio: sin ellas SQL Server rechaza la
    # fila y el MERGE no tiene con qué emparejar. Se descartan explícitamente.
    obligatorias = [c.nombre for c in esq.ESQUEMA_HECHOS_ONTIME if not c.nulo]
    mascara_valida = pd.Series(True, index=tipado.index)
    for nombre in obligatorias:
        invalidas = tipado[nombre].isna()
        if invalidas.any():
            reporte.motivos_descarte[nombre] = int(invalidas.sum())
        mascara_valida &= ~invalidas

    reporte.filas_descartadas = int((~mascara_valida).sum())
    tipado = tipado[mascara_valida]

    if "RowHash" in df.columns:
        tipado["RowHash"] = df.loc[mascara_valida, "RowHash"]

    if logger is not None and reporte.hubo_ajustes:
        logger.warning("Tipado estricto: %s", reporte.resumen())

    return tipado, reporte
