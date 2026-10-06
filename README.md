# Vida Nueva

Finanzas personales de un solo usuario: sueldo, gastos fijos, tarjetas y lo que
queda libre en cada quincena. FastAPI + SQLite atrás, React (Vite) adelante.

## Arrancar

```powershell
.\start.ps1
```

Compila el frontend y levanta el servidor en <http://127.0.0.1:8000>.

## Tu usuario

Vive en el archivo `.env` de esta carpeta, que no se sube a git:

```
FINANZAS_USER=alexander
FINANZAS_PASSWORD=la-que-quieras
```

Cambias cualquiera de los dos ahí y reinicias. `.env.example` tiene la plantilla
con las variables opcionales.

## Verlo desde el celular

Con el computador y el celular en la misma red WiFi:

```powershell
$env:FINANZAS_HOST = "0.0.0.0"
.\start.ps1
```

y en el celular abre `http://<ip-del-computador>:8000` (la ves con `ipconfig`).

## Desarrollo

```powershell
cd backend; python -m uvicorn app:app --reload   # API en :8000
cd web;     npm run dev                          # Vite en :5173, con proxy a la API
```

## En el servidor

Corre como el servicio systemd `vidanueva` en el puerto 8100, con su propio
`.env` y su propia base en `/opt/vidanueva`. El destino sale de
`FINANZAS_DEPLOY_HOST` y `FINANZAS_DEPLOY_KEY` en tu `.env`.

```powershell
.\deploy.ps1     # compila, sube backend + dist y reinicia el servicio
```

```bash
systemctl status vidanueva      # como va
journalctl -u vidanueva -f      # logs
```

Hoy responde por HTTP, asi que usuario y contrasena viajan sin cifrar. Para
pasarlo a HTTPS: crea un registro A hacia el servidor, agrega un vhost de nginx
que haga proxy a `127.0.0.1:8100`, vuelve a poner `--host 127.0.0.1` en el
servicio y corre `certbot --nginx -d <el-subdominio>`.

## Comprobaciones

```powershell
cd backend; python app.py --test    # cálculo quincenal, token y lectura del .env
```

## Qué hay dentro

| Ruta | Qué es |
|---|---|
| `backend/app.py` | API completa: login, maestros, pagos y los números calculados |
| `backend/finanzas.db` | SQLite: `items` y `deudas` (los maestros), `pagos` (por mes) y `config` (sueldo) |
| `backend/semilla.json` | Con que se llena una base vacia. Tus cifras, no va a git; `semilla.example.json` es la plantilla |
| `.env` | Tu usuario y contraseña. No va a git |
| `deploy.ps1` | Compila y publica en el servidor |
| `web/src/App.jsx` | La página: resumen, quincenas, deudas y maestros |
| `web/src/styles.css` | Tokens de color y tipografía (claro y oscuro) |

Los conceptos marcados como **ambas** quincenas se parten por la mitad en cada
una. Los pagos se guardan por mes, así que cambiar de mes empieza la lista en
limpio sin perder el historial.

Lo que debes vive aparte de lo que gastas: un gasto fijo solo tiene monto,
categoría y quincena, mientras que una **deuda** —tarjeta o crédito— lleva
saldo, cuota, tasa y un `tope` que se lee distinto según el tipo: en una
tarjeta es el cupo y la barra se llena al deber, y en un crédito es con cuánto
empezaste y la barra se llena al pagar. `init_db` migra sola las bases de
versiones anteriores.
