# Plan de implementación: ecosistema de datos (SharePoint → SQL Server → Dashboard)

> **Estado: implementado.** Este es el documento de diseño con el que se construyó el
> pipeline, y se conserva porque explica **por qué** está armado así (ventana móvil, staging
> desechable, RowHash, MERGE sin `NOT MATCHED BY SOURCE`). Para operarlo, el documento
> vigente es el [README](README.md).
>
> Dos cosas cambiaron respecto de lo planeado aquí:
>
> - **El destino ya no es local.** El plan asumía SQL Server en el mismo equipo; hoy la base
>   `AnaliticaOperativa` vive en el servidor corporativo `ICOLDATA01N` (SQL Server 2019
>   Standard) y la conexión usa un login SQL, no autenticación de Windows. La extracción y
>   la transformación siguen corriendo en el equipo local.
> - **Una sola tabla de negocio, no un modelo dimensional.** El plan hablaba de "Fact +
>   Dimensiones"; el alcance real quedó en `core.HechosOnTime`. Los ejemplos de este
>   documento que dicen `core.HechosVentas` son genéricos del plan original.
>
> Sigue pendiente lo de la Fase 8: reproceso completo periódico y backups.

## 0. Resumen de la arquitectura objetivo

```
SharePoint (origen)
   │  extracción incremental (solo mes actual + mes anterior)
   ▼
Staging en SQL Server  (tablas temporales, se truncan y recargan en cada corrida)
   │  MERGE / UPSERT por llave de negocio
   ▼
Tablas Core en SQL Server  (Fact + Dimensiones — el histórico completo vive aquí)
   │  lectura directa (Import u Incremental Refresh si es Power BI)
   ▼
Dashboard
```

Ideas clave que sostienen todo el diseño:

- **Ventana móvil**: solo el mes actual y el mes anterior se "tocan" en cada corrida. Todo lo anterior se considera congelado y no se vuelve a descargar ni reprocesar, salvo un reproceso completo programado (ver Fase 8).
- **Staging desechable**: cada corrida escribe en tablas de staging que se truncan y recargan sin miedo — nunca se acumulan ahí.
- **Tablas Core persistentes**: son las que acumulan el histórico completo. Nunca se truncan. Solo reciben INSERT (registros nuevos) o UPDATE (registros que cambiaron) vía una operación de upsert.
- **Detección de cambios por huella (hash)**: en vez de comparar campo por campo para saber si un registro cambió, se calcula un hash de la fila y se compara contra el hash guardado. Es más rápido y más simple de mantener.
- **Orquestación local, datos en el servidor** (ajustado durante la implementación): Python y el Programador de tareas de Windows corren en el equipo, porque la extracción de SharePoint necesita la sesión del usuario; la base vive en el servidor corporativo. El plan original asumía también SQL Server local.

---

## Fase 0 — Preparación y diagnóstico

Antes de tocar código, define esto (te ahorra rediseños después):

1. **Inventario de tablas/reportes**: ¿cuántas "tablas" distintas salen del Excel transformado? ¿Todas tienen una fecha que permite ubicar el registro en un mes? Si alguna tabla no tiene fecha de referencia (ej. un catálogo de clientes), esa tabla probablemente no necesita ventana móvil — se puede tratar aparte como una dimensión de carga completa (usualmente son pocas filas).
2. **Llave de negocio por tabla**: para cada tabla, identifica qué columna (o combinación de columnas) identifica de forma única un registro. Ejemplo: `NumeroFactura + LineaDetalle`, o `ID_Transaccion`. Esto es indispensable para el UPSERT — sin llave de negocio no hay forma de saber "este registro ya existía y cambió" vs "este registro es nuevo".
3. **Campo de fecha de corte**: identifica qué columna determina a qué mes pertenece un registro (fecha de transacción, fecha de creación, etc.). Esa es la columna sobre la que vas a filtrar la ventana móvil.
4. **¿SharePoint es una lista o una biblioteca de documentos (Excel)?** Esto cambia cómo filtras por fecha:
   - Si es una **lista de SharePoint**, puedes filtrar server-side por fecha de modificación usando la API (Microsoft Graph o SharePoint REST), lo cual es ideal — no descargas lo que no necesitas.
   - Si es un **archivo Excel dentro de una biblioteca de documentos**, normalmente tienes que descargar el archivo completo y filtrar localmente con pandas. En ese caso el ahorro no está en la descarga sino en el procesamiento y en la carga a SQL Server.
5. **Instalar prerrequisitos**:
   - SQL Server — confirma la edición, porque Express no trae SQL Server Agent. *(Resuelto: se usa `ICOLDATA01N`, SQL Server 2019 Standard, que sí lo trae; aun así la orquestación quedó en Task Scheduler — ver Fase 5.)*
   - Driver ODBC 17 o 18 para SQL Server (`ODBC Driver 18 for SQL Server`).
   - Librerías Python: `pyodbc` o `sqlalchemy`, `pandas`, `openpyxl`.

---

## Fase 1 — Diseño de la base de datos en SQL Server

### 1.1 Esquemas

Organiza la base en al menos dos esquemas (son como "carpetas" dentro de la BD):

```sql
CREATE DATABASE AnaliticaOperativa;
GO
USE AnaliticaOperativa;
GO
CREATE SCHEMA staging;  -- datos temporales de cada corrida
GO
CREATE SCHEMA core;     -- datos finales, histórico completo
GO
```

### 1.2 Tabla de control (watermark)

Esta tabla es el corazón de la incrementalidad: guarda cuándo fue la última extracción exitosa y qué ventana de fechas se procesó.

```sql
CREATE TABLE core.ETL_Control (
    ProcesoID       INT IDENTITY(1,1) PRIMARY KEY,
    NombreProceso   NVARCHAR(100) NOT NULL,       -- ej. 'ExtraccionVentas'
    UltimaEjecucion DATETIME2     NOT NULL DEFAULT SYSDATETIME(),
    FechaDesde      DATE          NOT NULL,       -- inicio de la ventana procesada
    FechaHasta      DATE          NOT NULL,       -- fin de la ventana procesada
    FilasStaging    INT           NULL,
    FilasInsertadas INT           NULL,
    FilasActualizadas INT         NULL,
    Estado          NVARCHAR(20)  NOT NULL,       -- 'OK', 'ERROR'
    Detalle         NVARCHAR(MAX) NULL
);
```

Cada corrida del pipeline escribe un registro aquí. Esto te da automáticamente:
- Un log auditable de todas las corridas.
- La respuesta a "¿cuál es la ventana que debo pedirle a SharePoint hoy?" (mes actual + mes anterior, calculado en el momento de la corrida, no depende de la última corrida — ver Fase 2).

### 1.3 Tablas Core (ejemplo con una tabla de hechos)

```sql
CREATE TABLE core.HechosVentas (
    LlaveNegocio     NVARCHAR(100) NOT NULL PRIMARY KEY,  -- tu llave única de negocio
    Fecha            DATE          NOT NULL,
    Cliente          NVARCHAR(200) NULL,
    Producto         NVARCHAR(200) NULL,
    Cantidad         DECIMAL(18,2) NULL,
    ValorTotal       DECIMAL(18,2) NULL,
    -- columnas de control técnico:
    RowHash          BINARY(32)    NOT NULL,   -- huella de la fila, para detectar cambios
    FechaCargaInicial DATETIME2    NOT NULL DEFAULT SYSDATETIME(),
    FechaUltimaActualizacion DATETIME2 NOT NULL DEFAULT SYSDATETIME()
);

CREATE NONCLUSTERED INDEX IX_HechosVentas_Fecha ON core.HechosVentas (Fecha);
```

**¿Por qué `RowHash`?** En vez de comparar 15 columnas una por una para saber si algo cambió, calculas un solo valor (hash) que resume toda la fila. Si el hash es igual al que ya está guardado, el registro no cambió y no hay nada que actualizar. Esto lo calculas en el script de transformación con algo como:

```python
import hashlib

def calcular_hash(fila, columnas):
    texto = "|".join(str(fila[c]) for c in columnas)
    return hashlib.sha256(texto.encode("utf-8")).digest()
```

o directamente en SQL con `HASHBYTES('SHA2_256', columna1 + '|' + columna2 + ...)` dentro del MERGE.

### 1.4 Tabla de staging (espejo de la tabla core, sin llave primaria estricta)

```sql
CREATE TABLE staging.HechosVentas_Stage (
    LlaveNegocio  NVARCHAR(100) NOT NULL,
    Fecha         DATE          NOT NULL,
    Cliente       NVARCHAR(200) NULL,
    Producto      NVARCHAR(200) NULL,
    Cantidad      DECIMAL(18,2) NULL,
    ValorTotal    DECIMAL(18,2) NULL,
    RowHash       BINARY(32)    NOT NULL
);
```

Esta tabla se trunca y se vuelve a llenar en cada corrida — solo contiene los datos de la ventana móvil (mes actual + anterior), nunca el histórico completo.

---

## Fase 2 — Extracción incremental desde SharePoint

### 2.1 Calcular la ventana de fechas en cada corrida

```python
from datetime import date

hoy = date.today()
primer_dia_mes_actual = hoy.replace(day=1)
if primer_dia_mes_actual.month == 1:
    primer_dia_mes_anterior = primer_dia_mes_actual.replace(year=primer_dia_mes_actual.year - 1, month=12)
else:
    primer_dia_mes_anterior = primer_dia_mes_actual.replace(month=primer_dia_mes_actual.month - 1)

fecha_desde = primer_dia_mes_anterior   # ej. 2026-07-01
fecha_hasta = hoy                       # ej. 2026-08-13
```

Esto se recalcula solo, sin depender de checkpoints frágiles: cada día, la ventana es "desde el primer día del mes pasado hasta hoy".

### 2.2 Filtrar en el origen, no después de descargar

- **Si SharePoint es una lista** y usas Microsoft Graph API, puedes filtrar directamente:
  `GET /sites/{site-id}/lists/{list-id}/items?$filter=fields/Fecha ge '2026-07-01'`
  Esto es lo ideal porque el ahorro de procesamiento empieza desde la descarga.
- **Si SharePoint entrega un archivo Excel completo** (biblioteca de documentos), no puedes evitar descargar el archivo, pero sí puedes:
  1. Descargar el archivo (como ya haces).
  2. Inmediatamente después, con pandas, filtrar solo las filas de la ventana:
     ```python
     df = pd.read_excel(ruta_archivo)
     df["Fecha"] = pd.to_datetime(df["Fecha"])
     df_ventana = df[(df["Fecha"] >= pd.Timestamp(fecha_desde)) & (df["Fecha"] <= pd.Timestamp(fecha_hasta))]
     ```
  3. A partir de aquí, todo el pipeline (transformación, carga) trabaja solo con `df_ventana`, no con `df` completo.

  Nota: si el archivo fuente en SharePoint es grande y tarda en descargarse completo cada vez, esa parte del costo no se puede evitar sin cambiar de método de extracción (ej. migrar a Graph API si el dato realmente vive en una lista, o pedir que el reporte fuente en SharePoint ya venga filtrado). Vale la pena confirmarlo en la Fase 0.

### 2.3 Qué hacer con el archivo Excel intermedio

Tienes dos opciones razonables:

- **Opción A (recomendada para empezar)**: mantén el Excel crudo como archivo de auditoría/respaldo (por si necesitas volver a un estado anterior), pero el pipeline ya no depende de él como fuente para el dashboard — solo como bitácora.
- **Opción B (más limpia a mediano plazo)**: elimina los archivos Excel intermedios y que el script de extracción escriba directamente a `staging` en SQL Server. Esto evita problemas de archivos bloqueados, rutas rotas, etc. Puedes migrar a esta opción una vez que el flujo con SQL Server esté validado y estable.

---

## Fase 3 — Transformación y carga a staging

Tu script de transformación actual probablemente ya tiene toda la lógica de negocio (limpieza, cálculos, cruces). Lo único que cambia es el destino final: en vez de escribir un `.xlsx`, escribe a la tabla de staging en SQL Server.

```python
import pyodbc
import pandas as pd

# Boceto del plan. La implementación real está en src/utils/db.py, que arma esta cadena
# desde config.yaml + .env y apunta a ICOLDATA01N con login SQL en vez de localhost con
# Trusted_Connection.
conn_str = (
    "DRIVER={ODBC Driver 18 for SQL Server};"
    "SERVER=localhost;"
    "DATABASE=AnaliticaOperativa;"
    "Trusted_Connection=yes;"
    "TrustServerCertificate=yes;"
)

def cargar_staging(df_transformado, tabla_staging, conn_str):
    with pyodbc.connect(conn_str) as conn:
        cursor = conn.cursor()
        cursor.execute(f"TRUNCATE TABLE {tabla_staging}")
        cursor.fast_executemany = True
        columnas = list(df_transformado.columns)
        placeholders = ",".join(["?"] * len(columnas))
        sql_insert = f"INSERT INTO {tabla_staging} ({','.join(columnas)}) VALUES ({placeholders})"
        cursor.executemany(sql_insert, df_transformado[columnas].values.tolist())
        conn.commit()
```

`Trusted_Connection=yes` usa tu usuario de Windows — ideal para un proceso automático local porque no necesitas guardar contraseñas en texto plano en ningún script.

**Cómo quedó:** contra el servidor corporativo esa ventaja se perdió. La cuenta de dominio `ICOLTRANS\aguzman` entra al servidor pero no tiene usuario en `AnaliticaOperativa` (error 4060) y otorgarlo depende del DBA, así que la conexión se hace con un login SQL cuyas credenciales viven en `config/.env`. `sql/06_permisos_pipeline.sql` deja lista la opción de volver a autenticación de Windows el día que el DBA dé el permiso, y `sql/07_crear_login_pipeline.sql` crea un login SQL de mínimo privilegio para no depender de `sa`. Ver [README → Conexión al servidor](README.md#conexión-al-servidor).

---

## Fase 4 — Lógica de UPSERT (staging → core)

Aquí es donde realmente se resuelve tu problema de "no reprocesar lo que no cambió".

### Opción A — `MERGE` (más compacto)

```sql
MERGE core.HechosVentas AS destino
USING staging.HechosVentas_Stage AS origen
    ON destino.LlaveNegocio = origen.LlaveNegocio
WHEN MATCHED AND destino.RowHash <> origen.RowHash THEN
    UPDATE SET
        Fecha = origen.Fecha,
        Cliente = origen.Cliente,
        Producto = origen.Producto,
        Cantidad = origen.Cantidad,
        ValorTotal = origen.ValorTotal,
        RowHash = origen.RowHash,
        FechaUltimaActualizacion = SYSDATETIME()
WHEN NOT MATCHED BY TARGET THEN
    INSERT (LlaveNegocio, Fecha, Cliente, Producto, Cantidad, ValorTotal, RowHash)
    VALUES (origen.LlaveNegocio, origen.Fecha, origen.Cliente, origen.Producto,
            origen.Cantidad, origen.ValorTotal, origen.RowHash)
OUTPUT $action INTO @ResultadoMerge;  -- opcional, para contar inserts/updates
```

Puntos importantes sobre `MERGE`:
- Es la forma más directa de expresar "si existe y cambió, actualiza; si no existe, inserta".
- Nota que **no** se incluye una rama `WHEN NOT MATCHED BY SOURCE` — eso es intencional: como staging solo trae la ventana móvil (2 meses), si activaras esa rama borraría o marcaría como eliminado todo lo que no está en esos 2 meses, es decir, todo tu histórico. Nunca uses `NOT MATCHED BY SOURCE` en este patrón de ventana parcial.
- SQL Server tiene reportados (y documentados por Microsoft) algunos comportamientos inesperados con `MERGE` bajo concurrencia alta o condiciones de carrera. Para un proceso batch que corre una vez al día en un único hilo (tu caso), es seguro. Si en el futuro corrieras cargas en paralelo, usarías la Opción B.

### Opción B — `UPDATE` + `INSERT` por separado (más verboso, pero más predecible)

```sql
BEGIN TRANSACTION;

UPDATE d
SET d.Fecha = s.Fecha, d.Cliente = s.Cliente, d.Producto = s.Producto,
    d.Cantidad = s.Cantidad, d.ValorTotal = s.ValorTotal,
    d.RowHash = s.RowHash, d.FechaUltimaActualizacion = SYSDATETIME()
FROM core.HechosVentas d
INNER JOIN staging.HechosVentas_Stage s ON d.LlaveNegocio = s.LlaveNegocio
WHERE d.RowHash <> s.RowHash;

INSERT INTO core.HechosVentas (LlaveNegocio, Fecha, Cliente, Producto, Cantidad, ValorTotal, RowHash)
SELECT s.LlaveNegocio, s.Fecha, s.Cliente, s.Producto, s.Cantidad, s.ValorTotal, s.RowHash
FROM staging.HechosVentas_Stage s
WHERE NOT EXISTS (
    SELECT 1 FROM core.HechosVentas d WHERE d.LlaveNegocio = s.LlaveNegocio
);

COMMIT;
```

**Recomendación**: empieza con `MERGE` por simplicidad (es una sola sentencia, más fácil de mantener). Si en algún momento ves comportamientos raros, migra a la Opción B — la lógica de negocio es idéntica.

### 4.1 ¿Qué pasa con los borrados en SharePoint?

Si alguien elimina un registro en SharePoint dentro de la ventana de 2 meses, con este diseño **no se elimina automáticamente en SQL Server** (porque, como se explicó arriba, no comparamos contra "todo lo que falta"). Decide conscientemente:
- **Ignorar borrados** (más simple, válido si en la práctica casi no se borran registros).
- **Soft delete controlado**: agregar una columna `Activo BIT` y, dentro de la ventana de 2 meses, marcar como inactivos los registros de `core` que estén en el rango de fechas pero no aparezcan en `staging`. Esto sí es seguro de comparar porque staging cubre exactamente esos 2 meses completos.

---

## Fase 5 — Orquestación y automatización

### 5.1 Un solo script maestro

En vez de dos scripts sueltos, crea un orquestador (`main_pipeline.py`) que llama en orden:

```python
def main():
    registrar_inicio()
    try:
        df_crudo = extraer_sharepoint(fecha_desde, fecha_hasta)
        df_transformado = transformar(df_crudo)
        cargar_staging(df_transformado, "staging.HechosVentas_Stage", conn_str)
        filas_insertadas, filas_actualizadas = ejecutar_merge(conn_str)
        registrar_exito(filas_insertadas, filas_actualizadas)
    except Exception as e:
        registrar_error(str(e))
        enviar_alerta_email(str(e))   # opcional pero muy recomendable
        raise

if __name__ == "__main__":
    main()
```

`registrar_inicio`, `registrar_exito` y `registrar_error` escriben en `core.ETL_Control` (Fase 1.2) — así tienes trazabilidad sin montar herramientas externas.

### 5.2 Programar la ejecución diaria

**Opción A — Programador de tareas de Windows (Task Scheduler)**, funciona en cualquier edición de SQL Server:
1. Abre "Programador de tareas" → "Crear tarea básica".
2. Desencadenador: diario, a la hora que prefieras (idealmente fuera de horario laboral).
3. Acción: "Iniciar un programa" → apunta al Python de tu entorno (ej. `C:\ruta\a\venv\Scripts\python.exe`) con argumento la ruta a `main_pipeline.py`.
4. En "Configuración", marca "Ejecutar la tarea lo antes posible si se pierde una ejecución programada" — te protege de que el equipo esté apagado esa noche.

**Opción B — SQL Server Agent** (solo si tu edición es Standard o Developer, no Express): puedes crear un Job con un paso "Operating system (CmdExec)" que llame al script Python, y dejar toda la orquestación dentro de SQL Server. Ventaja: historial de ejecuciones integrado en SSMS.

**Cómo quedó: opción A.** `ICOLDATA01N` es Standard y tiene el Agent activo, pero un Job correría el Python *en el servidor*, y la extracción de SharePoint necesita el token del usuario y la sesión de esta estación. Así que la orquestación se quedó en el Programador de tareas local: tarea `pipeline_ontime`, diaria a las 8:00 a.m. y al iniciar sesión, ejecutando `run_pipeline.bat`.

### 5.3 Logging y alertas

- Logging a archivo local además de la tabla `ETL_Control` (usa el módulo `logging` de Python, un archivo por día o rotativo).
- Alerta por correo si el proceso falla (usa `smtplib` con una cuenta SMTP, o un webhook de Teams/Slack si tu organización lo usa). Esto es clave porque un proceso automático que falla en silencio es peor que uno manual — al menos manualmente uno "ve" si algo no corrió.

**Cómo quedó:** el logging sí (`logs/pipeline.log` rotativo + `core.ETL_Control`), las alertas **no**: `src/utils/notificaciones.py` está escrito y probado, pero `email_habilitado` y `teams_webhook_habilitado` están en `false`, así que hoy un fallo no avisa. Es la brecha conocida frente a este plan — hoy los fallos se detectan mirando `ETL_Control` o `logs/bat_run.log`.

---

## Fase 6 — Conectar el dashboard a SQL Server

En vez de que el dashboard lea el `.xlsx` final, apúntalo directo a `core.HechosVentas` (y demás tablas core) en SQL Server. Esto elimina un paso completo de tu proceso actual (ya no necesitas que el script de transformación "termine" antes de que alguien abra el dashboard y encuentre el archivo bloqueado o desactualizado).

Si tu dashboard es **Power BI**:
- Conéctate a SQL Server con el conector nativo (Get Data → SQL Server), usando el mismo modo de autenticación de Windows.
- Configura **Incremental Refresh** en el modelo: defines parámetros `RangeStart`/`RangeEnd`, y le dices a Power BI que solo refresque las particiones de los últimos 1-2 meses, dejando el histórico ya cacheado sin volver a leerlo. Esto es literalmente el mismo principio que estás aplicando en SQL Server, pero un nivel más arriba (en el modelo de Power BI). Combinar ambos es lo más eficiente que puedes lograr sin herramientas de nube.
- Si usas Power BI Desktop con carga "Import" sin Incremental Refresh configurado, cada refresh igual va a releer toda la tabla core — el ahorro de esta fase 6 solo se materializa completo si configuras Incremental Refresh.

Si tu dashboard es otra herramienta con conexión SQL directa, el principio es el mismo: apunta la fuente de datos a las tablas `core`, no a un archivo Excel.

---

## Fase 7 — Validación y corte

No apagues el proceso actual de golpe. Plan de transición sugerido:

1. Corre el pipeline nuevo en paralelo al actual durante 1-2 semanas.
2. Compara: el Excel actual vs. lo que hay en `core.HechosVentas` para el mismo período — cuadra totales, conteos de filas, sumas de columnas clave.
3. Corre manualmente una vez un "reproceso completo" (ver Fase 8) para poblar todo el histórico en `core` antes de cortar — la ventana móvil solo aplica a las corridas incrementales del día a día, no a la carga inicial.
4. Una vez que los números cuadren de forma consistente, apunta el dashboard a SQL Server y desactiva la generación del Excel final (puedes conservar la extracción cruda como respaldo por un tiempo más).

---

## Fase 8 — Mantenimiento continuo

- **Reproceso completo periódico** (ej. una vez al mes o al trimestre, fuera de horario laboral): aunque el día a día solo toca 2 meses, es sano correr de vez en cuando una carga completa desde SharePoint para detectar correcciones retroactivas en meses "congelados" que el proceso incremental nunca vería (ej. alguien corrige en SharePoint un dato de hace 6 meses). Este reproceso completo usa la misma lógica de MERGE — no hay riesgo de duplicar nada, solo actualiza lo que cambió.
- **Backups de la base de datos**: al vivir en el servidor corporativo, los respaldos son tarea del área de TI y no del pipeline. Pendiente confirmar con ellos que `AnaliticaOperativa` quedó incluida: hoy no hay registro de backups de esa base en `msdb` y está en modo de recuperación `SIMPLE`.
- **Mantenimiento de índices**: con el tiempo, reconstruye índices periódicamente (`ALTER INDEX ... REBUILD`) si la tabla core crece mucho.
- **Monitoreo de crecimiento**: revisa cada cierto tiempo `core.ETL_Control` para ver tendencias de filas insertadas/actualizadas por corrida — te avisa si algo empieza a comportarse distinto (ej. de repente se insertan miles de "nuevos" registros que en realidad deberían ser actualizaciones, señal de que la llave de negocio no está bien definida).

---

## Checklist resumen

Estado al 2026-09-10:

- [x] Definir llave de negocio y columna de fecha por cada tabla del reporte actual
- [x] Crear base de datos con esquemas `staging` y `core`
- [x] Crear tabla `core.ETL_Control`
- [x] Crear tablas `core.*` con columna `RowHash` e índice por fecha
- [x] Crear tablas `staging.*` espejo
- [x] Modificar script de extracción para calcular ventana móvil (mes actual + anterior) y filtrar por fecha
- [x] Modificar script de transformación para calcular `RowHash` y cargar a `staging` en vez de a Excel
- [x] Escribir el `MERGE` (o UPDATE+INSERT) de staging → core por cada tabla
- [x] Unificar todo en un script maestro con manejo de errores y logging
- [x] Programar la tarea diaria (Task Scheduler o SQL Server Agent) — tarea `pipeline_ontime`
- [ ] **Configurar alerta por correo/Teams ante fallos** — el código está listo, falta habilitarlo en `config.yaml`
- [ ] Conectar el dashboard directo a SQL Server (e Incremental Refresh si es Power BI) — confirmar si ya apunta a `ICOLDATA01N`
- [x] Correr en paralelo, validar cifras, hacer el corte — el histórico se migró y el pipeline ya corre contra `ICOLDATA01N`
- [ ] **Programar reproceso completo periódico y backups** — ver Fase 8

Lo que queda en negrita es la deuda real del proyecto: sin alertas, un fallo pasa
desapercibido hasta que alguien mira el dashboard; y sin reproceso completo, una corrección
retroactiva en un mes ya congelado nunca llega a `core`.
