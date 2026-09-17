# pipeline-analitica-ontime

Consolida a diario los archivos de estatus On Time que las regionales mantienen en SharePoint
y los deja en una sola tabla de SQL Server lista para el dashboard.

La extracción y la transformación corren en el **equipo local**; la carga va al servidor
corporativo **ICOLDATA01N** (SQL Server 2019 Standard), base **AnaliticaOperativa**:

```
SharePoint (54 libros .xlsb/.xlsx)  →  transformación local  →  staging  →  MERGE  →  core
     biblioteca "Documentos compartidos"                  ICOLDATA01N.AnaliticaOperativa
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
                   contrato de esquema
  main_pipeline.py orquestador
sql/               DDL (esquemas, HechosOnTime core/staging, control), migraciones,
                   scripts de permisos y el MERGE
tests/             pruebas de las reglas On Time, del tipado estricto, del RowHash y de la
                   cadena de conexión
run_pipeline.bat   punto de entrada de la tarea programada (usa el intérprete de env-pipe)
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

## Operación

La ejecución diaria la dispara la tarea de Windows **`pipeline_ontime`**, que corre como
`Aguzman` y ejecuta el `run_pipeline.bat` de esta carpeta. Tiene dos disparadores: **diario a
las 8:00 a.m.** y **al iniciar sesión**, para cubrir los días en que el equipo estaba apagado
a esa hora.

Modificar la tarea exige un **PowerShell como administrador**: su definición en
`C:\Windows\System32\Tasks` solo la puede escribir `BUILTIN\Administradores`. Por ejemplo,
para repuntarla a otra carpeta:

```bash
$r="C:\Users\aguzman\pipeline-analitica-ontime_ext"; Set-ScheduledTask -TaskName pipeline_ontime -Action (New-ScheduledTaskAction -Execute "$r\run_pipeline.bat" -WorkingDirectory $r)
```

Correrlo a mano, sin esperar la tarea:

```bash
env-pipe\Scripts\python.exe src\main_pipeline.py
```

Una corrida completa tarda unos **9 minutos**, casi todo descargando los libros de SharePoint.

### Dónde mirar cuando algo falla

| Fuente | Qué tiene |
|---|---|
| `logs/pipeline.log` | Log del pipeline, rotado a medianoche y conservado 30 días (`logging.rotacion_dias`) |
| `logs/bat_run.log` | Salida cruda del `.bat`: es la única pista cuando el proceso ni arranca (ej. venv roto) y no llega a escribir en el log del pipeline |
| `core.ETL_Control` | Una fila por corrida: ventana procesada, filas de staging, insertadas, actualizadas, estado y el detalle de los ajustes de tipado |

La consulta de rutina después de una corrida:

```sql
SELECT TOP 10 ProcesoID, UltimaEjecucion, Estado, FilasStaging,
       FilasInsertadas, FilasActualizadas, Detalle
FROM core.ETL_Control
ORDER BY ProcesoID DESC;
```

Las alertas por correo y Teams están **desactivadas** (`notificaciones.email_habilitado` y
`teams_webhook_habilitado` en `false`), así que un fallo no avisa solo: hoy se detecta
mirando `ETL_Control` o el log.

### Fallas conocidas

| Síntoma | Causa | Solución |
|---|---|---|
| `logs/bat_run.log` dice `did not find executable at ...python.exe` y el `.bat` sale con código 103 | El venv `env-pipe` apunta a una ruta de Python que ya no existe (pasa al reinstalar o mover Python) | Corregir `home` y `executable` en `env-pipe/pyvenv.cfg`; no hace falta recrear el entorno |
| Uno o varios libros fallan con `401 Unauthorized` a mitad de corrida | El token de SharePoint se venció durante la ejecución | Volver a correr; si se repite, borrar `data_raw_backup/_cache_sharepoint/.msal_token_cache.json` y aprobar el device code de nuevo |
| `AADSTS50076 ... must use multi-factor authentication` en la salida | Es el intento con usuario/clave fallando antes de pasar al device code | Ruido esperado, no un fallo: la autenticación tiene cadena de respaldo (app registration → usuario/clave → device code) |
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
2. Copiar `config/.env` (no se versiona). Debe traer las credenciales de SharePoint
   (`SP_SITE_URL`, `SP_USERNAME`, `SP_PASSWORD`, `SP_TENANT`) y las del login SQL
   (`SQL_USUARIO`, `SQL_PASSWORD`). Como el tenant exige MFA, la primera corrida pide
   aprobar un device code en el navegador; el token queda cacheado en
   `data_raw_backup/_cache_sharepoint/.msal_token_cache.json` para las siguientes. Con una
   app registration en Azure AD (`SP_CLIENT_ID` / `SP_CLIENT_SECRET`) se vuelve 100%
   desatendido, sin device code.
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
5. Correr el pipeline a mano una vez y revisar `core.ETL_Control`.
6. Crear la tarea programada apuntando a `run_pipeline.bat` (ver [Operación](#operación)).

## Pruebas

```bash
env-pipe\Scripts\python.exe -m pytest tests/ -q
```

66 pruebas: reglas de On Time ácido y justificado, tipado estricto contra los valores sucios
reales de los Excel, cálculo del RowHash, armado de la cadena de conexión y el vaciado de
staging según los permisos de la cuenta.

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
