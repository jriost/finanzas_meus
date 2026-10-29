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
| `backend/finanzas.db` | SQLite: `items` y `deudas` (los maestros), `pagos`, `bonos` y `ahorros` (por mes) y `config` (sueldo, tasa del bolsillo) |
| `backend/semilla.json` | Con que se llena una base vacia. Tus cifras, no va a git; `semilla.example.json` es la plantilla |
| `.env` | Tu usuario y contraseña. No va a git |
| `deploy.ps1` | Compila y publica en el servidor |
| `web/src/App.jsx` | La página: resumen, quincenas, deudas y maestros |
| `web/src/styles.css` | Tokens de color y tipografía (claro y oscuro) |

Los conceptos marcados como **ambas** quincenas se parten por la mitad en cada
una. Los pagos se guardan por mes, así que cambiar de mes empieza la lista en
limpio sin perder el historial.

**El saldo de una deuda no se guarda: se deduce.** Lo que la base guarda es el
saldo inicial con su mes, y los pagos marcados. De ahi el saldo de hoy sale
recorriendo mes a mes: cada mes corre el interes sobre lo que se debia y se
resta lo que se marco pagado ese mes.

Eso trae tres cosas que antes no estaban. La deuda avanza sola con el
calendario, sin que nadie haga clic. Un mes sin marcar no es un mes sin pasar:
el interes corre igual y la deuda sube, que es lo que de verdad ocurre. Y el
resultado no depende del orden en que se marcaron los pagos, porque las marcas
son un conjunto, no una secuencia de restas.

Marcar la cuota de un mes que aun no llega solo la deja anotada: el recorrido
llega hasta el mes corriente, asi que no mueve el saldo de hoy.

Marcar como pagada la cuota de una tarjeta o un credito **le baja el saldo**,
pero no por el valor de la cuota: primero se van los intereses del mes y solo
el resto toca el capital. Cada pago guarda cuanto abono, asi que desmarcarlo
devuelve exactamente eso. Marcar un gasto fijo no mueve nada, porque un gasto
no tiene saldo.

El **ahorro** se lleva aparte: un aporte por mes en la tabla `ahorros`, y el
saldo se calcula aplicando mes a mes el rendimiento del bolsillo sobre lo que
ya habia y sumando encima el aporte. Asi se separa lo que pusiste tu de lo que
puso el banco, que es el punto de verlo. La tasa del bolsillo y lo que ya
tenias antes del primer aporte viven en `config`. Un aporte anotado en un mes
que aun no llega no infla el saldo de hoy, igual que los pagos.

La seccion de deudas encabeza con **cuando quedas libre**: la fecha la marca
la deuda que mas tarda, no la suma de todas, asi que tambien dice cual es y en
que fecha quedarias sin la demora de esa. Supone que las cuotas no cambian y
que no vuelves a usar las tarjetas; las deudas sin tasa, sin cuota o cuya
cuota no cubre los intereses se nombran aparte en vez de falsear la fecha.

No todo gasto es mensual. Cada uno lleva **cada cuántos meses** se cobra y,
si no es mensual, **desde qué mes**: uno cada 4 quincenas es `cada_meses` 2.
En los meses que no toca simplemente no aparece, así que el comprometido del
mes cambia segun caiga o no el bimestral, el semestral o el anual.

Los **bonos** son ingreso suelto de una quincena concreta, no un maestro: se
guardan por mes igual que los pagos, suman al ingreso de esa quincena y
desaparecen al cambiar de mes. Guardar uno en cero lo borra.

Lo que debes vive aparte de lo que gastas: un gasto fijo solo tiene monto,
categoría y quincena, mientras que una **deuda** —tarjeta o crédito— lleva
saldo, cuota, tasa y un `tope` que se lee distinto según el tipo: en una
tarjeta es el cupo y la barra se llena al deber, y en un crédito es con cuánto
empezaste y la barra se llena al pagar. `init_db` migra sola las bases de
versiones anteriores.
