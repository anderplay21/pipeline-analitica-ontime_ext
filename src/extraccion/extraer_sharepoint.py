"""Extracción desde SharePoint: recorre la biblioteca de documentos, descarga cada libro
Excel, toma la hoja "Consolidado" de cada uno y arma el consolidado alineado al esquema fijo.

Lógica portada de extraer_desde_sharepoint_script1.py (autenticación con MFA vía device
code, tolerancia a nombres de hoja variables, cache de descargas por tamaño).

Después de consolidar, se filtra por la ventana móvil (mes actual + N meses atrás) usando
la columna de fecha de corte definida en config.yaml.
"""

import hashlib
import re
import sys
import unicodedata
from datetime import date
from pathlib import Path

import pandas as pd

RAIZ_PROYECTO = Path(__file__).resolve().parents[2]


def normalizar(texto):
    """Forma canónica de un nombre de hoja/columna: sin acentos, minúsculas,
    espacios colapsados y sin espacios alrededor de "/" o "_".
    """
    sin_acentos = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(c for c in sin_acentos if not unicodedata.combining(c))
    limpio = re.sub(r"\s+", " ", sin_acentos).strip().lower()
    return re.sub(r"\s*([/_])\s*", r"\1", limpio)


def calcular_ventana_fechas(meses_atras=1, hoy=None):
    """Ventana móvil: desde el primer día de hace N meses hasta hoy."""
    hoy = hoy or date.today()
    primer_dia_mes_actual = hoy.replace(day=1)

    mes = primer_dia_mes_actual.month - meses_atras
    anio = primer_dia_mes_actual.year
    while mes <= 0:
        mes += 12
        anio -= 1

    fecha_desde = primer_dia_mes_actual.replace(year=anio, month=mes)
    fecha_hasta = hoy
    return fecha_desde, fecha_hasta


def _token_device_flow(tenant, client_id, recurso, ruta_cache_token):
    """Obtiene un token por device code, reutilizando el token cacheado si sigue vigente.

    El tenant exige MFA, así que usuario/clave no basta: se aprueba una sola vez en el
    navegador y el refresh token cacheado cubre las corridas siguientes.
    """
    import msal

    cache = msal.SerializableTokenCache()
    if ruta_cache_token.exists():
        cache.deserialize(ruta_cache_token.read_text(encoding="utf-8"))

    app = msal.PublicClientApplication(
        client_id,
        authority=f"https://login.microsoftonline.com/{tenant}",
        token_cache=cache,
    )
    scopes = [f"{recurso}/.default"]

    resultado = None
    cuentas = app.get_accounts()
    if cuentas:
        resultado = app.acquire_token_silent(scopes, account=cuentas[0])

    if not resultado:
        flujo = app.initiate_device_flow(scopes=scopes)
        if "user_code" not in flujo:
            raise RuntimeError(f"No se pudo iniciar device flow: {flujo}")
        print("\n" + "=" * 70)
        print("AUTENTICACION REQUERIDA (una sola vez; el token queda cacheado)")
        print("=" * 70)
        print(flujo["message"])
        print("=" * 70 + "\n", flush=True)
        resultado = app.acquire_token_by_device_flow(flujo)

    if "access_token" not in resultado:
        raise RuntimeError(resultado.get("error_description", str(resultado)))

    if cache.has_state_changed:
        ruta_cache_token.parent.mkdir(parents=True, exist_ok=True)
        ruta_cache_token.write_text(cache.serialize(), encoding="utf-8")

    return resultado


def conectar(site_url, usuario, password, config):
    """Autentica contra SharePoint, en orden de preferencia:
    1. app registration (si SP_CLIENT_SECRET está en .env) — ideal para desatendido.
    2. usuario/clave ROPC — falla si la cuenta exige MFA.
    3. device code — fallback ante MFA; token cacheado en disco.
    """
    import os

    from office365.runtime.auth.token_response import TokenResponse
    from office365.sharepoint.client_context import ClientContext

    sp_cfg = config["sharepoint"]
    ruta_cache_token = RAIZ_PROYECTO / sp_cfg["ruta_cache_token"]

    tenant = os.getenv("SP_TENANT") or f"{usuario.split('@')[-1].split('.')[0]}.onmicrosoft.com"
    client_id = os.getenv("SP_CLIENT_ID") or sp_cfg["client_id_publico"]
    client_secret = os.getenv("SP_CLIENT_SECRET")
    recurso = "/".join(site_url.split("/")[:3])

    intentos = []
    if client_secret:
        def _app_registration():
            from office365.runtime.auth.client_credential import ClientCredential
            return ClientContext(site_url).with_credentials(
                ClientCredential(client_id, client_secret))
        intentos.append((f"app registration (client_id={client_id[:8]}...)", _app_registration))
    else:
        def _ropc():
            return ClientContext(site_url).with_username_and_password(
                tenant, client_id, usuario, password)

        def _device():
            token = _token_device_flow(tenant, client_id, recurso, ruta_cache_token)
            return ClientContext(site_url).with_access_token(lambda: TokenResponse(**token))

        intentos.append((f"usuario/clave ROPC (tenant={tenant})", _ropc))
        intentos.append((f"device code (tenant={tenant})", _device))

    ultimo_error = None
    for metodo, construir in intentos:
        try:
            ctx = construir()
            web = ctx.web.get().execute_query()
            print(f"Conectado a: {web.properties.get('Title', site_url)}  [{metodo}]")
            return ctx
        except Exception as exc:
            ultimo_error = exc
            mensaje = str(exc)
            if "AADSTS50076" in mensaje or "AADSTS50079" in mensaje:
                print(f"  {metodo}: la cuenta exige MFA, pasando a device code ...")
            elif not metodo.startswith("device"):
                print(f"  {metodo} falló: {type(exc).__name__}")

    raise RuntimeError(f"No se pudo autenticar contra {site_url}: {ultimo_error}")


def listar_libros(ctx, biblioteca, extensiones):
    """Recorre recursivamente la biblioteca y devuelve los archivos Excel encontrados."""
    site_path = "/".join(ctx.base_url.split("/")[3:])
    raiz = f"/{site_path}/{biblioteca}".replace("//", "/")

    encontrados = []
    pendientes = [raiz]
    visitados = set()

    while pendientes:
        ruta = pendientes.pop()
        if ruta in visitados:
            continue
        visitados.add(ruta)

        try:
            carpeta = ctx.web.get_folder_by_server_relative_url(ruta)
            archivos = carpeta.files.get().execute_query()
            subcarpetas = carpeta.folders.get().execute_query()
        except Exception as exc:
            print(f"  AVISO: no se pudo leer la carpeta {ruta}: {exc}")
            continue

        for archivo in archivos:
            nombre = archivo.properties.get("Name", "")
            if nombre.startswith("~$"):
                continue
            if Path(nombre).suffix.lower() not in extensiones:
                continue
            encontrados.append({
                "url": archivo.properties.get("ServerRelativeUrl"),
                "nombre": nombre,
                "tamano": archivo.properties.get("Length"),
            })

        for sub in subcarpetas:
            nombre_sub = sub.properties.get("Name", "")
            if nombre_sub.startswith("Forms"):
                continue
            pendientes.append(sub.properties.get("ServerRelativeUrl"))

    return sorted(encontrados, key=lambda item: item["url"])


def descargar(ctx, libro, carpeta_cache):
    """Baja el libro al cache local. Si ya está y el tamaño coincide, no lo re-descarga."""
    carpeta_cache.mkdir(parents=True, exist_ok=True)
    prefijo = hashlib.md5(libro["url"].encode("utf-8")).hexdigest()[:8]
    destino = carpeta_cache / f"{prefijo}_{libro['nombre']}"

    tamano_remoto = libro.get("tamano")
    if destino.exists() and tamano_remoto and destino.stat().st_size == int(tamano_remoto):
        return destino

    with open(destino, "wb") as salida:
        ctx.web.get_file_by_server_relative_url(libro["url"]).download(salida).execute_query()
    return destino


def elegir_hoja(ruta, nombre_buscar):
    """Devuelve (hoja_elegida, todas_las_hojas) buscando la hoja objetivo (ej. "Consolidado").

    Cascada de coincidencia: nombre exacto -> empieza por -> contiene.
    """
    engine = "pyxlsb" if ruta.suffix.lower() == ".xlsb" else "openpyxl"
    with pd.ExcelFile(ruta, engine=engine) as libro:
        hojas = list(libro.sheet_names)

    candidatas = [
        hoja for hoja in hojas
        if "_filterdatabase" not in str(hoja).lower()
        and not str(hoja).startswith("_xlnm.")
        and "$" not in str(hoja)
    ]

    exactas = [h for h in candidatas if normalizar(h) == nombre_buscar]
    if exactas:
        return exactas[0], hojas

    prefijo = [h for h in candidatas if normalizar(h).startswith(nombre_buscar)]
    if prefijo:
        return prefijo[0], hojas

    contiene = [h for h in candidatas if nombre_buscar in normalizar(h)]
    if contiene:
        return contiene[0], hojas

    return None, hojas


def alinear_esquema(df, columnas_esquema):
    """Deja el DataFrame con exactamente columnas_esquema, en ese orden.

    El match es por nombre normalizado exacto, nunca por substring, para no confundir
    columnas parecidas (ej. "FECHA ENTREGA" vs "Fecha Programada De Entrega").
    """
    esquema_por_clave = {normalizar(col): col for col in columnas_esquema}
    renombres = {}
    descartadas = []
    for col in df.columns:
        canonica = esquema_por_clave.get(normalizar(col))
        if canonica and canonica not in renombres.values():
            renombres[col] = canonica
        else:
            descartadas.append(str(col))

    alineado = df[list(renombres.keys())].rename(columns=renombres)

    faltantes = [col for col in columnas_esquema if col not in alineado.columns]
    for col in faltantes:
        alineado[col] = pd.NA

    return alineado[columnas_esquema], faltantes, descartadas


def procesar_libro(ctx, libro, site_path, carpeta_cache, nombre_hoja, columnas_esquema):
    """Descarga, lee y alinea un libro. Devuelve (df_o_None, fila_de_reporte)."""
    ruta_relativa = libro["url"].replace(f"/{site_path}/", "")
    reporte = {
        "archivo": libro["nombre"], "ruta": ruta_relativa, "hoja_elegida": "",
        "filas": 0, "columnas_faltantes": "", "columnas_descartadas": "", "estado": "",
    }

    try:
        local = descargar(ctx, libro, carpeta_cache)
        hoja, hojas = elegir_hoja(local, nombre_hoja)

        if hoja is None:
            reporte["estado"] = f"OMITIDO: sin hoja '{nombre_hoja}'"
            reporte["columnas_descartadas"] = f"hojas disponibles: {' | '.join(map(str, hojas))}"
            return None, reporte

        reporte["hoja_elegida"] = str(hoja)
        engine = "pyxlsb" if local.suffix.lower() == ".xlsb" else "openpyxl"
        df = pd.read_excel(local, sheet_name=hoja, engine=engine)
        df.columns = df.columns.astype(str).str.strip()

        df, faltantes, descartadas = alinear_esquema(df, columnas_esquema)
        df["source_file"] = libro["nombre"]
        df["source_path"] = ruta_relativa
        df["source_sheet"] = str(hoja)

        reporte["filas"] = len(df)
        reporte["columnas_faltantes"] = " | ".join(faltantes)
        reporte["columnas_descartadas"] = " | ".join(descartadas)
        reporte["estado"] = "OK"
        return df, reporte

    except Exception as exc:
        reporte["estado"] = f"ERROR: {type(exc).__name__}: {exc}"
        return None, reporte


def consolidar_sharepoint(config):
    """Conecta a SharePoint, recorre la biblioteca completa y devuelve (df_consolidado, reportes)."""
    import os

    sp_cfg = config["sharepoint"]
    site_url = os.getenv("SP_SITE_URL")
    usuario = os.getenv("SP_USERNAME")
    password = os.getenv("SP_PASSWORD")
    if not all([site_url, usuario, password]):
        raise RuntimeError("Faltan SP_SITE_URL / SP_USERNAME / SP_PASSWORD en config/.env")

    ctx = conectar(site_url, usuario, password, config)
    site_path = "/".join(ctx.base_url.split("/")[3:])
    carpeta_cache = RAIZ_PROYECTO / sp_cfg["carpeta_cache"]
    extensiones = set(sp_cfg["extensiones_permitidas"])
    nombre_hoja = normalizar(sp_cfg["hoja_buscar"])
    columnas_esquema = config["columnas_esquema_origen"]

    print(f"Buscando libros en '{sp_cfg['biblioteca']}' ...")
    libros = listar_libros(ctx, sp_cfg["biblioteca"], extensiones)
    print(f"Libros Excel encontrados: {len(libros)}")

    frames, reportes = [], []
    for indice, libro in enumerate(libros, start=1):
        df, reporte = procesar_libro(ctx, libro, site_path, carpeta_cache, nombre_hoja, columnas_esquema)
        reportes.append(reporte)
        if df is not None:
            frames.append(df)
        print(f"[{indice}/{len(libros)}] {libro['nombre']}: {reporte['estado']} "
              f"({reporte['filas']} filas, hoja '{reporte['hoja_elegida']}')")

    if not frames:
        raise RuntimeError("Ningún libro se pudo consolidar. Revisar el reporte de procesamiento.")

    return pd.concat(frames, ignore_index=True), reportes


def convertir_fecha_excel(valor):
    """Convierte un serial de Excel (días desde 1899-12-30) a datetime; tolera datetime/str."""
    if pd.isna(valor):
        return None
    try:
        if isinstance(valor, pd.Timestamp):
            return valor
        if isinstance(valor, (int, float)) and valor > 0:
            return pd.to_datetime(valor, unit="D", origin="1899-12-30")
        if isinstance(valor, str) and valor.strip():
            return pd.to_datetime(valor, errors="coerce")
        return None
    except Exception:
        return None


def filtrar_ventana(df, columna_fecha, fecha_desde, fecha_hasta):
    """Filtra el DataFrame consolidado dejando solo las filas dentro de la ventana móvil."""
    df = df.copy()
    df[columna_fecha] = df[columna_fecha].apply(convertir_fecha_excel)
    mascara = (df[columna_fecha] >= pd.Timestamp(fecha_desde)) & (
        df[columna_fecha] <= pd.Timestamp(fecha_hasta)
    )
    return df[mascara]


def extraer(config, nombre_tabla="hechos_ontime", logger=None):
    """Orquesta la extracción completa: consolida SharePoint y filtra por la ventana móvil."""
    ventana_cfg = config.get("ventana_movil", {})
    fecha_desde, fecha_hasta = calcular_ventana_fechas(
        meses_atras=ventana_cfg.get("meses_atras", 1)
    )

    df_consolidado, reportes = consolidar_sharepoint(config)

    # Un libro que no se pudo leer (corrupto, protegido, sin hoja "Consolidado") significa
    # que ese cliente o regional simplemente no entra en la carga. Si solo queda en el
    # print de consola nadie se entera: tiene que verse en el log de la corrida.
    omitidos = [r for r in reportes if r["estado"] != "OK"]
    if omitidos and logger is not None:
        logger.warning(
            "Libros omitidos en la extracción: %s de %s", len(omitidos), len(reportes)
        )
        for reporte in omitidos:
            logger.warning("  omitido '%s': %s", reporte["archivo"], reporte["estado"])

    tabla_cfg = config["tablas"][nombre_tabla]
    df_ventana = filtrar_ventana(df_consolidado, tabla_cfg["columna_fecha"], fecha_desde, fecha_hasta)

    return df_ventana, fecha_desde, fecha_hasta
