# Monitor de Servidor

Este proyecto monitorea si un servidor se cae y manda alertas cuando:

- el servicio deja de responder
- el servicio se recupera

Tambien guarda un historial con:

- fecha y hora de inicio de la caida
- fecha y hora de recuperacion
- duracion total
- motivo del fallo
- detalles tecnicos del chequeo

## Que puede monitorear

Puedes elegir uno de estos modos:

- `http`: revisa una URL y valida el codigo HTTP esperado
- `tcp`: revisa si un puerto esta abierto
- `ping`: revisa conectividad basica al host

## Instalacion

```bash
pip install -r requirements.txt
```

## Configuracion

1. Crea tu archivo de configuracion a partir del ejemplo.
2. Ajusta el target, el intervalo y el metodo de alerta.
3. No subas `config.json` a GitHub. El repo ya incluye un `.gitignore` para evitarlo.

En Windows PowerShell:

```powershell
Copy-Item .\config.example.json .\config.json
```

## Uso

Prueba una sola verificacion:

```bash
python monitor_servidor.py --config config.json --once
```

Ejecuta el monitoreo continuo:

```bash
python monitor_servidor.py --config config.json
```

## Configuracion para tu UniFi

Para una consola UniFi normalmente basta con usar una configuracion como la de [config.example.json](./config.example.json):

- `https://TU_IP_O_HOST:8443/`

Ese equipo responde con certificado autofirmado y redirecciona a `/manage`, por eso la configuracion usa:

- `verify_ssl: false`
- `allow_redirects: false`
- `expected_status_codes: [200, 302]`

Con eso el monitor toma como sano un `200` o un `302`, que en UniFi suele indicar que la consola esta arriba.

## Archivos que genera

- `data/monitor.log`: log de ejecucion
- `data/state.json`: estado actual del monitor
- `data/incidentes.csv`: historial de caidas y recuperaciones

Todos esos archivos locales ya estan excluidos por `.gitignore`.

## Alertas

El script soporta dos tipos de alerta:

- Email SMTP
- Webhook
- Alerta local de Windows con sonido y ventana emergente

Para webhook puedes usar:

- `discord`
- `slack`
- `teams`
- `generic`

En tu `config.json` puedes activar la alerta local para que, cuando el UniFi se caiga:

- suene una alarma repetida
- aparezca una ventana emergente con fecha, hora y detalle del fallo

Si luego quieres cambiarlo, revisa la seccion `alerts.desktop`.

## Publicarlo en GitHub sin exponer datos

Este repo ya quedo preparado para eso:

- `config.json` no se sube
- `data/` no se sube
- `__pycache__/` no se sube
- el ejemplo publico usa placeholders en lugar de tu IP real

Flujo recomendado:

```powershell
cd "ruta\\del\\proyecto"
git init
git add .gitignore README.md monitor_servidor.py requirements.txt config.example.json
git commit -m "Agregar monitor de servidor configurable"
```

Si usas GitHub CLI y quieres crear el repo desde terminal:

```powershell
gh repo create monitor-servidor-unifi --public --source . --remote origin --push
```

Si prefieres que el repo quede privado, cambia `--public` por `--private`.

## Ejemplo de configuracion por puerto TCP

```json
{
  "target": {
    "name": "SQL Produccion",
    "type": "tcp",
    "host": "TU_HOST_O_IP",
    "port": 1433,
    "timeout_seconds": 5
  }
}
```

## Ejemplo de configuracion por ping

```json
{
  "target": {
    "name": "Servidor Interno",
    "type": "ping",
    "host": "TU_HOST_O_IP",
    "timeout_seconds": 5
  }
}
```

## Recomendaciones

- Usa `failure_threshold` en `2` o `3` para evitar falsos positivos por microcortes.
- Si usas Gmail, normalmente necesitas una app password.
- Deja el script corriendo con el Programador de tareas de Windows, NSSM o como servicio.

## Idea para dejarlo siempre activo en Windows

Puedes crear una tarea programada para que arranque al iniciar sesion o al encender el equipo.
Si quieres, en el siguiente paso te dejo tambien el `.bat` o la tarea de Windows lista para que se ejecute sola.
