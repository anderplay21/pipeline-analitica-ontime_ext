# pipeline-analitica-ontime

Consolida cada 30 minutos los archivos de estatus On Time que las regionales mantienen en
SharePoint y los deja en una sola tabla de SQL Server lista para el dashboard.

La extracción y la transformación corren en el **equipo local**; la carga va al servidor
corporativo **ICOLDATA01N** (SQL Server 2019 Standard), base **AnaliticaOperativa**. Cada
corrida queda registrada en una lista de SharePoint:

```
SharePoint (76 libros .xlsb/.xlsx)  →  transformación local  →  staging  →  MERGE  →  core
  sitio Consolidadodeestatus-SAC                         ICOLDATA01N.AnaliticaOperativa
  biblioteca "Documentos compartidos"
                                          bitácora  →  lista "Ejecuciones Pipeline OnTime"
                                                       sitio SitioAutomatizaciones
```

Una sola tabla de negocio: **`core.HechosOnTime`**, con llave de negocio
`Cliente + Fecha de Factura + Factura`. Cada corrida reprocesa solo la **ventana móvil**
(mes actual + mes anterior, `ventana_movil.meses_atras` en `config.yaml`) y la aplica con un
`MERGE`, así que todo lo anterior a esa ventana queda congelado y nunca se vuelve a descargar.
El `RowHash` de cada fila hace que el `MERGE` solo actualice lo que de verdad cambió.

Ver [plan_ecosistema_datos_sharepoint_sqlserver.md](plan_ecosistema_datos_sharepoint_sqlserver.md)
para el diseño completo y las decisiones de fondo.

## Estructura

```
config/            config.yaml (conexión, esquema de columnas, ventana móvil) + .env (credenciales)
data_referencia/   LeadTime.csv, Causales.csv, Festivos_Colombia.csv, Restricciones.csv
src/
  extraccion/      conexión a SharePoint, recorrido de la biblioteca, consolidación por hoja
                   "Consolidado" y filtro por ventana móvil
  transformacion/  Lead Time, fecha estimada de entrega (días hábiles), On Time
                   ácido/justificado, tipado estricto y cálculo de RowHash
  carga/           carga a staging + ejecución del MERGE
  utils/           conexión a BD, verificación de conexión, logging, notificaciones,
                   registro de corridas en SharePoint, contrato de esquema
  main_pipeline.py orquestador
sql/               DDL (esquemas, HechosOnTime core/staging, control), migraciones,
                   scripts de permisos y el MERGE. sql/02, sql/03 y el MERGE se GENERAN
                   desde utils/esquema.py: no se editan a mano
tests/             pruebas de las reglas On Time, del tipado estricto, del RowHash, de la
                   cadena de conexión y de que el SQL generado siga el esquema
run_pipeline.bat   punto de entrada de la tarea programada (usa el intérprete de env-pipe)
crear_tarea_programada.ps1  crea/reemplaza la tarea de Windows (como administrador)
env-pipe/          entorno virtual del proyecto (no se versiona)
logs/              log del pipeline y del .bat (no se versiona)
data_raw_backup/   cache de libros descargados y del token de SharePoint (no se versiona)
```

## Conexión al servidor

El destino se define en el bloque `sql_server` de `config/config.yaml`:

| Clave | Valor | Nota |
|---|---|---|
| `servidor` | `ICOLDATA01N` | Instancia por defecto, puerto 1433 |
| `base_datos` | `AnaliticaOperativa` | Base compartida del servidor corporativo |
| `driver` | `ODBC Driver 18 for SQL Server` | Debe estar instalado en el equipo que corre el pipeline |
| `trusted_connection` | `false` | Login SQL, con `SQL_USUARIO` / `SQL_PASSWORD` de `config/.env` |
| `timeout_conexion` | `30` | Segundos para establecer la conexión antes de fallar |

El servidor está en modo mixto, así que acepta los dos esquemas de autenticación y se cambia
de uno a otro con `trusted_connection`:

- **Login SQL** (`trusted_connection: false`, el modo actual). Usuario y clave salen de
  `config/.env`, que no se versiona. Cualquier cuenta de Windows puede ejecutar el pipeline,
  porque las credenciales viajan en la cadena de conexión y no dependen de quién corre el
  proceso. El costo es que la clave queda en texto plano en el `.env` de esta estación.
- **Autenticación de Windows** (`trusted_connection: true`). No hay clave que guardar: el
  pipeline entra con la cuenta de dominio que ejecuta el proceso (`ICOLTRANS\aguzman`, la de
  la tarea programada), pero esa cuenta necesita que el DBA le dé acceso a la base con
  `sql/06_permisos_pipeline.sql`. Hoy no lo tiene (error 4060).

### Permisos que necesita la cuenta

Hoy el pipeline se conecta con la cuenta de servicio **`automatizacion`**, que tiene los roles
`db_datareader` y `db_datawriter` sobre `AnaliticaOperativa`. Con eso alcanza:

| Objeto | Permisos | Para qué |
|---|---|---|
| `core.HechosOnTime` | `SELECT`, `INSERT`, `UPDATE` | el MERGE |
| `staging.HechosOnTime_Stage` | `SELECT`, `INSERT`, `DELETE` | vaciar y recargar en cada corrida |
| `core.ETL_Control` | `INSERT` | bitácora de la corrida |

**`ALTER` sobre staging es opcional.** Vaciar la tabla con `TRUNCATE TABLE` lo exige, y
`db_datawriter` no lo incluye; cuando falta, la carga vacía con `DELETE`, que sí está
cubierto. A esta escala (~40k filas) la diferencia de tiempo es despreciable, así que no vale
la pena pedir permisos extra solo por eso. Si se quiere `TRUNCATE`, alguien con permisos
sobre el esquema ejecuta:

```sql
GRANT ALTER ON SCHEMA::staging TO automatizacion;
```

Si en algún momento el pipeline vuelve a correr como `sa`, `verificar_conexion` lo avisa:
`sa` es sysadmin y controla las ~79 bases del servidor compartido, mucho más de lo que el
pipeline necesita. `sql/07_crear_login_pipeline.sql` crea un login dedicado de mínimo
privilegio como alternativa.

### Verificar la conexión

```bash
cd src && python -m utils.verificar_conexion
```

Separa los problemas que desde afuera se ven igual (un "Login failed" genérico) pero se
arreglan distinto:

| Salida | Qué significa | Qué hacer |
|---|---|---|
| `[ERROR] No se pudo conectar al servidor` | Con login SQL: usuario o clave equivocados (error 18456). Con Windows: el login de dominio no existe en el servidor. También red/VPN o servidor mal escrito | Corregir `SQL_USUARIO`/`SQL_PASSWORD` en `.env`, o pedir el login al DBA |
| `[ERROR] El servidor responde pero la base no es accesible` (error 4060) | El login existe pero no tiene usuario en `AnaliticaOperativa` | Ejecutar `sql/06` (cuenta de Windows) o `sql/07` (login SQL dedicado) |
| `[FALTA] Tablas que no existen todavía` | Hay acceso pero los objetos del pipeline no están creados | Ejecutar `sql/01`..`sql/04` |
| `[AVISO] ... es sysadmin` | El pipeline corre con `sa` o similar: más permiso del necesario | Opcional: pasar al login dedicado con `sql/07` |
| `[NOTA] Sin permiso ALTER en staging` | Informativo: la tabla se vaciará con `DELETE` en vez de `TRUNCATE` | Nada; opcionalmente otorgar `ALTER` sobre el esquema |

## Conexión a SharePoint

El pipeline entra a SharePoint como una **app de Entra ID con certificado**: sin usuario,
sin clave y sin MFA, que es lo que permite correrlo desatendido.

| Variable en `config/.env` | Valor |
|---|---|
| `SP_TENANT` | Id del tenant de Icoltrans |
| `SP_CLIENT_ID` | `4ef3bf31-85cd-45f0-919d-1b56f7c5bef7` (la app) |
| `SP_CERT_THUMBPRINT` | Huella SHA-1 del certificado subido a la app |
| `SP_CERT_PATH` | Ruta a la clave privada `sp_app_key.pem` |
| `SP_SITE_URL` | Sitio de donde se leen los libros |

**La clave privada no está en el repo.** Vive en
`..\certificados_sharepoint\sp_app_key.pem` (fuera de esta carpeta), con permisos NTFS solo
para la cuenta que corre la tarea. El `.cer` público es el que está cargado en la app de
Entra. **El certificado vence el 2028-11-24**: antes de esa fecha hay que generar uno nuevo,
subir su `.cer` a la app y actualizar `SP_CERT_THUMBPRINT` y `SP_CERT_PATH`.

La app tiene el permiso **`Sites.Selected`**, que no abre ningún sitio por sí solo: un
administrador la autoriza sitio por sitio. Hoy:

| Sitio | Rol | Para qué |
|---|---|---|
| `Consolidadodeestatus-SAC` | `read` | Leer los libros de estatus |
| `SitioAutomatizaciones` | `write` | Escribir la bitácora de corridas |

Para dar acceso a otro sitio, un administrador ejecuta (Graph, permiso `Sites.FullControl.All`):

```powershell
$sitio = Get-MgSite -SiteId "icoltranssas.sharepoint.com:/sites/<sitio>"
New-MgSitePermission -SiteId $sitio.Id -BodyParameter @{ roles = @("read")   # o "write"
  grantedToIdentities = @(@{ application = @{ id = "4ef3bf31-85cd-45f0-919d-1b56f7c5bef7"; displayName = "pipeline-ontime" } }) }
```

Si las variables de certificado no están, el código cae a los métodos anteriores, en este
orden: secreto de la app (`SP_CLIENT_SECRET`), usuario/clave (`SP_USERNAME`/`SP_PASSWORD`) y
device code. Quedan como respaldo; con el certificado configurado no se usan.

## Operación

La ejecución la dispara la tarea de Windows **`pipeline_ontime`** (carpeta
`\Automatizaciones\1) Pipeline OnTime\` del Programador de tareas), que ejecuta el
`run_pipeline.bat` de esta carpeta **cada 30 minutos desde el 2026-11-25 a las 10:00**, como
`icoltrans\automatizacion`. Corre con o sin sesión iniciada (inicio de sesión S4U, que no
guarda la clave de Windows ni la necesita: SQL entra con login SQL y SharePoint con el
certificado).

- Si una corrida sigue en curso cuando toca la siguiente, la nueva **se salta**
  (`MultipleInstances IgnoreNew`): nunca hay dos corridas a la vez.
- Si el servidor estaba apagado a la hora, corre apenas pueda (`StartWhenAvailable`).
- Una corrida que pase de **2 horas** se cancela.

La tarea se crea o se reemplaza con `crear_tarea_programada.ps1`, que exige **PowerShell
como administrador** (registrar una tarea S4U es exclusivo de administradores):

```powershell
powershell -ExecutionPolicy Bypass -File .\crear_tarea_programada.ps1
# otro horario:
powershell -ExecutionPolicy Bypass -File .\crear_tarea_programada.ps1 -Inicio "2026-12-01 06:00" -Minutos 60
```

Correrlo a mano, sin esperar la tarea:

```bash
env-pipe\Scripts\python.exe src\main_pipeline.py
```

La primera corrida en un equipo tarda unos **23 minutos**, casi todo descargando los ~460 MB
de libros de SharePoint. Las siguientes reusan la cache local
(`data_raw_backup/_cache_sharepoint`) y solo bajan los libros que cambiaron.

### Bitácora de corridas en SharePoint

Cada corrida, exitosa o fallida, agrega un elemento a la lista **Ejecuciones Pipeline
OnTime** de `SitioAutomatizaciones`. Es la forma de ver el estado del pipeline sin entrar a
SQL Server; con una corrida cada 30 minutos son unos 48 elementos por día.

| Columna | Contenido |
|---|---|
| `Estado` | `Exitosa` o `Fallida` |
| `Inicio`, `Fin`, `Duracion` | Horario de la corrida y duración en minutos |
| `VentanaDesde`, `VentanaHasta` | Rango de fechas de factura procesado |
| `LibrosLeidos` | Libros de SharePoint leídos sin error |
| `FilasExtraidas` | Filas en la ventana antes de transformar |
| `FilasDescartadas` | Extraídas que no llegaron a staging: sin llave de negocio completa o duplicadas por llave |
| `FilasInsertadas`, `FilasActualizadas` | Resultado del MERGE |
| `Error` | Traceback, solo en corridas fallidas |

En una corrida fallida las cifras quedan hasta donde llegó. Si el fallo es al conectar a SQL
Server, la lista es el **único** rastro: `core.ETL_Control` no se pudo escribir.

Registrar es de mejor esfuerzo: si SharePoint no responde, la corrida deja un `WARNING` en
el log y sigue. Se configura en el bloque `reporte_ejecuciones` de `config.yaml`
(`habilitado: false` lo apaga). La lista se ubica por su ruta (`ruta_lista`), no por su
título, así que se puede renombrar sin tocar nada.

Las fechas se guardan en UTC y SharePoint las muestra en la zona del sitio, que debe ser
**(UTC-05:00) Bogotá** (Configuración del sitio → Configuración regional). Con la zona por
defecto del Pacífico se ven corridas 3 horas.

### Dónde mirar cuando algo falla

| Fuente | Qué tiene |
|---|---|
| `logs/pipeline.log` | Log del pipeline, rotado a medianoche y conservado 30 días (`logging.rotacion_dias`) |
| `logs/bat_run.log` | Salida cruda del `.bat`: es la única pista cuando el proceso ni arranca (ej. venv roto) y no llega a escribir en el log del pipeline |
| `core.ETL_Control` | Una fila por corrida: ventana procesada, filas de staging, insertadas, actualizadas, estado y el detalle de los ajustes de tipado |
| Lista **Ejecuciones Pipeline OnTime** | Lo mismo, visible en SharePoint, y además las corridas que fallaron antes de conectar a SQL |
| Historial de la tarea `pipeline_ontime` | En el Programador de tareas: si la tarea se disparó y con qué código salió |

La consulta de rutina después de una corrida:

```sql
SELECT TOP 10 ProcesoID, UltimaEjecucion, Estado, FilasStaging,
       FilasInsertadas, FilasActualizadas, Detalle
FROM core.ETL_Control
ORDER BY ProcesoID DESC;
```

Las alertas por correo y Teams están **desactivadas** (`notificaciones.email_habilitado` y
`teams_webhook_habilitado` en `false`), así que un fallo no avisa solo: hoy se detecta
mirando la lista de SharePoint, `ETL_Control` o el log.

`logs/bat_run.log` **no rota**: crece con cada corrida (48 por día). Conviene vaciarlo o
archivarlo de vez en cuando.

### Fallas conocidas

| Síntoma | Causa | Solución |
|---|---|---|
| `logs/bat_run.log` dice `did not find executable at ...python.exe` y el `.bat` sale con código 103 | El venv `env-pipe` apunta a una ruta de Python que ya no existe (pasa al reinstalar o mover Python) | Corregir `home` y `executable` en `env-pipe/pyvenv.cfg`; no hace falta recrear el entorno |
| `No existe la clave privada del certificado` | `SP_CERT_PATH` apunta a un `.pem` que no está, o la cuenta que corre no puede leerlo | Revisar la ruta y los permisos NTFS de `certificados_sharepoint` para la cuenta de la tarea |
| `AADSTS700027` / `AADSTS700024` al conectar a SharePoint | El certificado venció, o el `.cer` cargado en la app no corresponde al `.pem` local | Comparar `SP_CERT_THUMBPRINT` con la huella que muestra la app en Entra; renovar si venció |
| `403 Forbidden` / `Access denied` al leer el sitio o escribir en la lista | La app no tiene permiso `Sites.Selected` sobre ese sitio, o tiene `read` donde necesita `write` | Pedir al administrador el permiso (ver [Conexión a SharePoint](#conexión-a-sharepoint)) |
| `No se pudo registrar la corrida en SharePoint` en el log | Falló la bitácora, no la carga: los datos sí quedaron en SQL | Ver el detalle del WARNING; normalmente es permiso o la lista renombrada de ruta |
| La tarea queda en "No se ha ejecutado" o falla con `0x80070569` | La cuenta no tiene el derecho "Iniciar sesión como proceso por lotes", necesario para S4U | Un administrador lo otorga en `secpol.msc` → Asignación de derechos de usuario |
| `AADSTS50076 ... must use multi-factor authentication` en la salida | Solo aparece si se quitó el certificado: es el respaldo usuario/clave fallando antes de pasar al device code | Restablecer las variables `SP_CERT_*` en `.env` |
| `Login failed for user '...'` (18456) | La clave del `.env` no es la de este servidor | Corregir `SQL_PASSWORD` y verificar con `python -m utils.verificar_conexion` |
| `Cannot find the object "HechosOnTime_Stage" because it does not exist or you do not have permissions` (1088) | Casi siempre es permiso, no tabla faltante: la cuenta no tiene `ALTER` y la carga intentó `TRUNCATE`. Ya no debería ocurrir — el código cae a `DELETE` solo | Correr `verificar_conexion`, que distingue "no existe" de "sin permiso" |

## Tipado de datos

Los Excel de SharePoint son texto libre: una misma columna trae enteros, flotantes, horas
como fracción de día de Excel, fechas seriales y notas escritas a mano. Para que eso no
tumbe la carga hay un contrato de tipos único y una capa que lo hace cumplir.

- **[src/utils/esquema.py](src/utils/esquema.py)** es la única fuente de verdad: declara
  tipo, ancho y obligatoriedad de cada columna. De ahí se generan los DDL:
  ```bash
  cd src && python -m utils.esquema      # regenera sql/02 y sql/03
  ```
  Nunca editar `sql/02_crear_tablas_core.sql` ni `sql/03_crear_tablas_staging.sql` a mano:
  se sobreescriben. Cambiar un tipo se hace en `esquema.py` y luego se regenera.

- **[src/transformacion/tipado.py](src/transformacion/tipado.py)** convierte cada columna a
  su tipo destino antes de insertar. Política acordada con negocio ante un dato que no
  cumple:

  | Situación | Qué hace | Queda registrado |
  |---|---|---|
  | Texto más largo que el ancho declarado | lo recorta | log + `ETL_Control.Detalle` |
  | Fecha / hora / entero no interpretable | lo deja en `NULL` | log + `ETL_Control.Detalle` |
  | Fila sin llave de negocio completa | descarta la fila (SQL la rechazaría) | log + `ETL_Control.Detalle` |

  La corrida nunca se cae por un dato sucio, pero cada ajuste queda contado: si el log
  reporta recortes o anulaciones, hay que ir a corregir el archivo de origen. En una corrida
  típica se anulan unos 4.700 valores de `Hora_Cita` y unos 1.000 de
  `Fecha Programada De Entrega`: es lo esperado con el origen actual, pero si esas columnas
  se van a usar en el dashboard vale la pena atacar el origen.

- `Hora_Cita` es de tipo `time`. Se interpretan las fracciones de Excel (`0.375` → 09:00) y
  las horas de texto (`7:00 AM`, `7.00am`, `18:30`). El texto libre que aparece en esa
  columna (rangos como `6:00 - 9:30 AM`, notas como `TURNO`) queda en `NULL` por decisión
  de negocio; la migración dejó una copia en `core.HechosOnTime_HoraCita_Respaldo`.

### Migración de una base ya existente

Los DDL usan `IF NOT EXISTS`, así que **no** alteran una tabla que ya existe. Para llevar
una base creada con el esquema anterior al tipado estricto:

```
sql/05_migrar_tipado_estricto.sql     -- ajusta anchos y convierte Hora_Cita a TIME
sql/03_crear_tablas_staging.sql       -- recrea staging (el paso 6 de la migración la elimina)
```

Es idempotente y verifica antes de actuar: si algún dato no cabe en los anchos nuevos,
aborta sin tocar nada y muestra qué columna es.

## Instalación desde cero

Solo hace falta al montar el pipeline en otro equipo o al reconstruir el entorno: en esta
estación ya está instalado y corriendo.

1. Crear el entorno virtual e instalar dependencias:
   ```bash
   python -m venv env-pipe && env-pipe\Scripts\python.exe -m pip install -r requirements.txt
   ```
2. Copiar `config/.env` (no se versiona) con el login SQL (`SQL_USUARIO`, `SQL_PASSWORD`) y
   las variables de SharePoint (`SP_SITE_URL`, `SP_TENANT`, `SP_CLIENT_ID`,
   `SP_CERT_THUMBPRINT`, `SP_CERT_PATH`), y copiar `sp_app_key.pem` a una carpeta fuera del
   repo con permisos solo para la cuenta de la tarea. Ver
   [Conexión a SharePoint](#conexión-a-sharepoint).
3. Crear los objetos en la base, si no existen (requiere permisos elevados sobre
   `AnaliticaOperativa`):
   ```
   sql/01_crear_esquemas.sql
   sql/02_crear_tablas_core.sql
   sql/03_crear_tablas_staging.sql
   sql/04_crear_control.sql
   ```
   Y, según el modo de autenticación, uno de estos dos:
   ```
   sql/06_permisos_pipeline.sql     -- da acceso a la cuenta de Windows ICOLTRANS\aguzman
   sql/07_crear_login_pipeline.sql  -- crea el login SQL dedicado etl_ontime
   ```
4. Verificar la conexión:
   ```bash
   cd src && python -m utils.verificar_conexion
   ```
5. Crear la carpeta `logs` (el `.bat` escribe ahí antes de que Python la cree).
6. Correr el pipeline a mano una vez y revisar `core.ETL_Control` y la lista de SharePoint.
7. Crear la tarea con `crear_tarea_programada.ps1` como administrador (ver [Operación](#operación)).

## Agregar una columna nueva

El listado de columnas está enumerado en varios lugares y no todos fallan si se olvidan.
El orden importa: **la base primero**, porque las columnas nuevas son nulables y el código
viejo sigue funcionando con ellas ya creadas — así la corrida diaria no se rompe si el
cambio de código queda a medias.

1. **Migrar la base.** Escribir un `sql/NN_*.sql` con `ALTER TABLE ... ADD` sobre
   `core.HechosOnTime` **y** `staging.HechosOnTime_Stage`, con guarda idempotente
   (`IF COL_LENGTH(...) IS NULL`). Ver `sql/08_agregar_columnas_dir_cajas_kilos.sql` como
   modelo. Ejecutarlo con una cuenta que pueda hacer DDL: `automatizacion` no puede.
   Las columnas nuevas van **`NULL`**: una `NOT NULL` haría que el tipado descarte toda
   fila que no la traiga.
   Regenerar `sql/02` y `sql/03` no sirve para esto — usan `IF NOT EXISTS` y no alteran
   una tabla existente.
2. **Declararla** en `ESQUEMA_HECHOS_ONTIME` ([src/utils/esquema.py](src/utils/esquema.py)),
   con su tipo y ancho. La posición en la tupla es el orden físico de las columnas.
3. **Agregarla en `config/config.yaml`**: en `columnas_esquema_origen` si viene del Excel, y
   **siempre** en `columnas_hash` — si no, un cambio en ese campo no dispara el UPDATE.
4. **Regenerar el SQL**: `cd src && python -m utils.esquema`. Esto reescribe `sql/02`,
   `sql/03` y el MERGE. Ninguno de los tres se edita a mano.
5. **Verificar**: `pytest tests/ -q` y `cd src && python -m utils.verificar_conexion`, que
   compara las columnas declaradas contra las que la base realmente tiene.

Si la columna es **derivada** (calculada, no viene del Excel), el cálculo va en
[transformar.py](src/transformacion/transformar.py) y no se declara en
`columnas_esquema_origen`.

**Tipos disponibles:** `TEXTO` (con ancho), `FECHA`, `HORA`, `ENTERO` y `DECIMAL` (con
precisión y escala). Agregar un tipo nuevo exige tocar `esquema.py` (el `tipo_sql`) y
`tipado.py` (la función de conversión y su rama en el despacho).

**Qué esperar en la primera corrida:** al agregar columnas a `columnas_hash` cambia el
RowHash de todas las filas, así que el MERGE reportará como actualizada casi toda la
ventana móvil. Es esperado, y es justamente lo que rellena las columnas nuevas en las filas
ya existentes. En la segunda corrida el número debe volver a la normalidad; si sigue alto,
algún valor no tiene representación estable en `str()` y el hash cambia en cada corrida.

El histórico anterior a la ventana móvil queda en `NULL`: la ventana solo reprocesa mes
actual y anterior.

## Pruebas

```bash
env-pipe\Scripts\python.exe -m pytest tests/ -q
```

84 pruebas: reglas de On Time ácido y justificado, tipado estricto contra los valores sucios
reales de los Excel (incluido el `DECIMAL` y su estabilidad para el hash), cálculo del
RowHash, armado de la cadena de conexión, vaciado de staging según los permisos de la cuenta,
correspondencia entre el SQL versionado y el esquema, y el armado del registro para la
lista de SharePoint (conversión de fechas a UTC y tolerancia a fallas de SharePoint).

## Notas

- `columnas_esquema_origen` en `config.yaml` es el esquema fijo que se extrae de cada libro
  de SharePoint; las columnas de un libro que no calcen se descartan y se reportan.
- El `MERGE` **no** incluye `WHEN NOT MATCHED BY SOURCE`, y es a propósito: staging solo trae
  la ventana móvil, así que esa rama borraría todo el histórico fuera de esos dos meses.
- No hay registro de backups de `AnaliticaOperativa` en `msdb` y la base está en modo de
  recuperación `SIMPLE`. El servidor tiene SQL Server Agent activo, así que los respaldos son
  tarea del área de TI: vale confirmar con ellos que esta base quedó incluida.
- `consolidado_sharepoint.xlsx` y `reporte_consolidacion.csv` en la raíz son insumos de los
  scripts originales (ya eliminados del repo) que sirvieron de referencia en el primer
  sprint. Se pueden archivar.
