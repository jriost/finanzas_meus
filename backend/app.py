"""Vida Nueva — API de finanzas personales (un solo usuario).

Arranque:  uvicorn app:app --reload    (desde esta carpeta)
Usuario:   FINANZAS_USER y FINANZAS_PASSWORD en el .env de la raíz del proyecto.
Datos:     SQLite (finanzas.db) con dos maestros —gastos fijos y deudas— más el
           sueldo y los pagos marcados de cada mes.
"""
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE = Path(__file__).parent
ENV_PATH = BASE.parent / ".env"


def cargar_env(path: Path = ENV_PATH):
    """Lee CLAVE=valor del .env sin pisar lo que ya venga del entorno."""
    if not path.is_file():
        return
    for linea in path.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        k, v = linea.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


cargar_env()
USUARIO = os.environ.get("FINANZAS_USER", "")
PASSWORD = os.environ.get("FINANZAS_PASSWORD", "")
DB_PATH = Path(os.environ.get("FINANZAS_DB", BASE / "finanzas.db"))
DIST = BASE.parent / "web" / "dist"

# Lo que debes no es un gasto fijo más: tarjetas y créditos tienen saldo, tope,
# tasa y plazo. Viven en su propio maestro y estas no son categorías de gasto.
CATEGORIAS = ["Vivienda", "Servicios", "Comida y vida", "Salud", "Otros"]
CATEGORIAS_GRAFICO = ["Vivienda", "Tarjetas", "Crédito", "Servicios", "Comida y vida", "Salud", "Otros"]
QUINCENAS = ["1", "2", "ambas"]
TIPOS = ["tarjeta", "credito"]
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 días

CAT_DEUDA = {"tarjeta": "Tarjetas", "credito": "Crédito"}


def cargar_semilla() -> tuple[int, list, list]:
    """Con qué llenar una base vacía: semilla.json si existe, si no la plantilla.

    Los gastos van como [nombre, monto, categoría, quincena] y las deudas como
    [nombre, tipo, cuota, quincena]; saldo, tope, tasa y día de pago empiezan en
    cero porque solo tú los sabes.
    """
    ruta = BASE / "semilla.json"
    if not ruta.is_file():
        ruta = BASE / "semilla.example.json"
    d = json.loads(ruta.read_text(encoding="utf-8"))
    return d["sueldo"], d["gastos"], d["deudas"]


# ---------------------------------------------------------------- base de datos
@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def cfg_get(c, k, default=None):
    row = c.execute("SELECT v FROM config WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def cfg_set(c, k, v):
    c.execute("INSERT INTO config(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
              (k, json.dumps(v)))


def columnas(c, tabla) -> set[str]:
    return {r[1] for r in c.execute(f"SELECT * FROM pragma_table_info('{tabla}')")}


def existe(c, tabla) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                          (tabla,)).fetchone())


def migrar(c):
    """Lleva una base de cualquier versión anterior al esquema de hoy."""
    # Los pagos apuntaban solo a items, antes de que hubiera un maestro de deudas.
    if existe(c, "pagos") and "item_id" in columnas(c, "pagos"):
        c.executescript("""
          ALTER TABLE pagos RENAME TO pagos_viejo;
          CREATE TABLE pagos(
            mes TEXT NOT NULL, origen TEXT NOT NULL, ref INTEGER NOT NULL, quincena TEXT NOT NULL,
            PRIMARY KEY(mes, origen, ref, quincena));
          INSERT INTO pagos(mes,origen,ref,quincena)
            SELECT mes,'gasto',item_id,quincena FROM pagos_viejo;
          DROP TABLE pagos_viejo;
        """)
    c.execute("UPDATE pagos SET origen='gasto' WHERE origen='item'")

    # La tabla 'tarjetas' pasó a ser 'deudas', que también guarda los créditos.
    if existe(c, "tarjetas"):
        for t in c.execute("SELECT * FROM tarjetas").fetchall():
            cur = c.execute(
                "INSERT INTO deudas(nombre,tipo,tope,saldo,cuota,dia_pago,tasa,quincena)"
                " VALUES(?,'tarjeta',?,?,?,?,?,?)",
                (t["nombre"], t["cupo"], t["saldo"], t["cuota"], t["dia_pago"],
                 t["tasa"] if "tasa" in t.keys() else 0, t["quincena"]))
            c.execute("UPDATE pagos SET origen='deuda', ref=? WHERE origen='tarjeta' AND ref=?",
                      (cur.lastrowid, t["id"]))
        c.execute("DROP TABLE tarjetas")

    # Los gastos de categoría Tarjetas o Crédito eran deudas disfrazadas.
    cols = columnas(c, "items")
    for g in c.execute("SELECT * FROM items WHERE categoria IN ('Tarjetas','Crédito')").fetchall():
        tipo = "tarjeta" if g["categoria"] == "Tarjetas" else "credito"
        cur = c.execute(
            "INSERT INTO deudas(nombre,tipo,tope,saldo,cuota,tasa,quincena) VALUES(?,?,?,?,?,?,?)",
            (g["nombre"], tipo,
             g["deuda_inicial"] if "deuda_inicial" in cols else 0,
             g["deuda"] if "deuda" in cols else 0,
             g["monto"], g["tasa"] if "tasa" in cols else 0, g["quincena"]))
        c.execute("UPDATE pagos SET origen='deuda', ref=? WHERE origen='gasto' AND ref=?",
                  (cur.lastrowid, g["id"]))
        c.execute("DELETE FROM items WHERE id=?", (g["id"],))

    # Un gasto fijo ya no lleva saldo, valor inicial ni tasa: eso es de una deuda.
    sobran = cols - {"id", "nombre", "monto", "categoria", "quincena", "cada_meses", "desde"}
    if sobran:
        c.executescript("""
          ALTER TABLE items RENAME TO items_viejo;
          CREATE TABLE items(
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, monto INTEGER NOT NULL,
            categoria TEXT NOT NULL, quincena TEXT NOT NULL,
            cada_meses INTEGER NOT NULL DEFAULT 1, desde TEXT NOT NULL DEFAULT '');
          INSERT INTO items(id,nombre,monto,categoria,quincena)
            SELECT id,nombre,monto,categoria,quincena FROM items_viejo;
          DROP TABLE items_viejo;
        """)

    # Bases creadas antes de que un gasto pudiera no ser mensual.
    faltan = {"cada_meses": "INTEGER NOT NULL DEFAULT 1", "desde": "TEXT NOT NULL DEFAULT ''"}
    for col, tipo in faltan.items():
        if col not in columnas(c, "items"):
            c.execute(f"ALTER TABLE items ADD COLUMN {col} {tipo}")


def init_db():
    with conn() as c:
        c.executescript("""
          CREATE TABLE IF NOT EXISTS config(k TEXT PRIMARY KEY, v TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, monto INTEGER NOT NULL,
            categoria TEXT NOT NULL, quincena TEXT NOT NULL,
            cada_meses INTEGER NOT NULL DEFAULT 1,  -- 1 = todos los meses, 2 = cada dos...
            desde TEXT NOT NULL DEFAULT '');        -- AAAA-MM del primer cobro, si no es mensual
          CREATE TABLE IF NOT EXISTS deudas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, tipo TEXT NOT NULL DEFAULT 'tarjeta',
            tope INTEGER NOT NULL DEFAULT 0,      -- cupo de la tarjeta, o con cuánto empezó el crédito
            saldo INTEGER NOT NULL DEFAULT 0, cuota INTEGER NOT NULL DEFAULT 0,
            dia_pago INTEGER NOT NULL DEFAULT 0, tasa REAL NOT NULL DEFAULT 0,
            quincena TEXT NOT NULL DEFAULT '1');
          CREATE TABLE IF NOT EXISTS bonos(
            mes TEXT NOT NULL, quincena TEXT NOT NULL,
            monto INTEGER NOT NULL, nota TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(mes, quincena));
          CREATE TABLE IF NOT EXISTS pagos(
            mes TEXT NOT NULL, origen TEXT NOT NULL, ref INTEGER NOT NULL, quincena TEXT NOT NULL,
            PRIMARY KEY(mes, origen, ref, quincena));
        """)
        migrar(c)

        if cfg_get(c, "secret") is None:
            cfg_set(c, "secret", secrets.token_hex(32))
        sueldo, gastos, deudas = cargar_semilla()
        if cfg_get(c, "sueldo") is None:
            cfg_set(c, "sueldo", sueldo)
        if not c.execute("SELECT 1 FROM items LIMIT 1").fetchone():
            c.executemany("INSERT INTO items(nombre,monto,categoria,quincena) VALUES(?,?,?,?)", gastos)
        if not c.execute("SELECT 1 FROM deudas LIMIT 1").fetchone():
            c.executemany("INSERT INTO deudas(nombre,tipo,cuota,quincena) VALUES(?,?,?,?)", deudas)


# ---------------------------------------------------------------- autenticación
def sign_token(secret: str, exp: int) -> str:
    sig = hmac.new(secret.encode(), str(exp).encode(), hashlib.sha256).hexdigest()
    return f"{exp}.{sig}"


def verify_token(secret: str, token: str) -> bool:
    try:
        exp_s, sig = token.split(".", 1)
        exp = int(exp_s)
    except (ValueError, AttributeError):
        return False
    if exp < time.time():
        return False
    return hmac.compare_digest(sign_token(secret, exp), token)


def auth(request: Request):
    token = (request.headers.get("authorization") or "").removeprefix("Bearer ").strip()
    with conn() as c:
        secret = cfg_get(c, "secret", "")
    if not token or not verify_token(secret, token):
        raise HTTPException(401, "Sesión vencida. Vuelve a entrar.")


# ---------------------------------------------------------------- cálculo
TOPE_CUOTAS = 600  # 50 años: más allá de eso la deuda no se está pagando


def tasa_mensual(tasa_ea: float) -> float:
    """Mensual equivalente a una efectiva anual, que es como se citan en Colombia."""
    return (1 + tasa_ea / 100) ** (1 / 12) - 1 if tasa_ea else 0.0


def plan_pago(saldo: int, cuota: int, tasa_ea: float) -> dict | None:
    """Cuántas cuotas faltan, y cuánto de la próxima es interés y cuánto capital.

    Amortiza mes a mes en vez de dividir saldo entre cuota: con intereses esa
    división subestima el plazo. Sin tasa registrada da lo mismo que dividir.
    `crece` avisa el caso feo: la cuota no alcanza ni para los intereses, así
    que el saldo sube cada mes y la deuda nunca termina.
    """
    if saldo <= 0 or cuota <= 0:
        return None
    i = tasa_mensual(tasa_ea)
    interes_mes = round(saldo * i)
    if cuota <= saldo * i:
        return {"cuotas": None, "interes_mes": interes_mes, "abono_capital": 0,
                "interes_total": None, "crece": True}

    restante, cuotas, interes = float(saldo), 0, 0.0
    while restante > 0 and cuotas < TOPE_CUOTAS:
        cargo = restante * i
        restante += cargo - cuota
        interes += cargo
        cuotas += 1
    return {"cuotas": cuotas, "interes_mes": interes_mes,
            "abono_capital": cuota - interes_mes, "interes_total": round(interes), "crece": False}


def mirar_deuda(d: dict) -> dict:
    """Una deuda con lo que se puede deducir de ella.

    `tope` se lee distinto según el tipo: en una tarjeta es el cupo, y lo que
    importa es cuánto llevas usado; en un crédito es con cuánto empezaste, y lo
    que importa es cuánto llevas pagado. Ambos salen de la misma resta.
    """
    tope, saldo = d["tope"], d["saldo"]
    return {
        **d,
        "categoria": CAT_DEUDA[d["tipo"]],
        "disponible": tope - saldo if tope else None,
        "pagado": max(0, tope - saldo) if tope else 0,
        "avance": round(max(0, tope - saldo) / tope, 4) if tope else None,
        "uso": round(min(1, saldo / tope), 4) if tope else None,
        "plan": plan_pago(saldo, d["cuota"], d["tasa"]),
    }


def sumar_meses(mes: str, n: int) -> str:
    """'AAAA-MM' más n meses."""
    y, m = map(int, mes.split("-"))
    total = (y * 12 + m - 1) + n
    return f"{total // 12:04d}-{total % 12 + 1:02d}"


def mes_actual() -> str:
    return date.today().strftime("%Y-%m")


def proyeccion(vistas: list[dict], desde: str | None = None) -> dict:
    """Cuándo quedas libre de deudas, si sigues pagando las cuotas de hoy.

    Cuenta desde el mes corriente, no desde el que se esté mirando en pantalla:
    el saldo guardado es el de hoy, así que hojear meses adelante no cambia lo
    que falta por pagar ni, por tanto, la fecha en que se acaba.

    Es un estimado, y hacia abajo: supone que no vuelves a usar las tarjetas y
    que las cuotas no cambian. Las deudas sin tasa registrada terminarán más
    tarde de lo que dice esto, así que se nombran aparte en vez de callarlo.
    """
    desde = desde or mes_actual()
    con_saldo = [d for d in vistas if d["saldo"] > 0]
    con_fin = [d for d in con_saldo if d["plan"] and d["plan"]["cuotas"]]
    plazos = sorted((d["plan"]["cuotas"], d["nombre"]) for d in con_fin)
    ultima = max(con_fin, key=lambda d: d["plan"]["cuotas"], default=None)
    # Casi siempre una sola deuda marca la fecha; saber cuánto cambiaría sin ella
    # dice a cuál atacar primero.
    penultima = plazos[-2][0] if len(plazos) > 1 else None
    return {
        "deudas": len(con_saldo),
        "cuotas": ultima["plan"]["cuotas"] if ultima else None,
        "desde": desde,
        "fin": sumar_meses(desde, ultima["plan"]["cuotas"] - 1) if ultima else None,
        "ultima": ultima["nombre"] if ultima else None,
        "fin_sin_la_ultima": sumar_meses(desde, penultima - 1) if penultima else None,
        "interes_total": sum(d["plan"]["interes_total"] for d in con_fin) if con_fin else 0,
        # Las que impiden dar una fecha, cada una por su motivo.
        "nunca_termina": [d["nombre"] for d in con_saldo if d["plan"] and d["plan"]["crece"]],
        "sin_cuota": [d["nombre"] for d in con_saldo if not d["plan"]],
        "sin_tasa": [d["nombre"] for d in con_fin if not d["tasa"]],
    }


def meses_entre(desde: str, hasta: str) -> int:
    """Meses de 'AAAA-MM' a 'AAAA-MM'. Negativo si el segundo es anterior."""
    (ya, ma), (yb, mb) = (map(int, desde.split("-")), map(int, hasta.split("-")))
    return (yb - ya) * 12 + (mb - ma)


def cae_en_mes(item: dict, mes: str) -> bool:
    """Si un gasto se cobra en ese mes.

    Lo normal es que sí: `cada_meses` 1 es todos los meses. Un gasto cada 4
    quincenas es `cada_meses` 2, y entonces `desde` dice en cuál de los dos
    empieza; sin ese ancla no hay forma de saberlo, así que cae siempre.
    """
    cada = item.get("cada_meses") or 1
    desde = item.get("desde") or ""
    if cada <= 1 or not desde:
        return True
    d = meses_entre(desde, mes)
    return d >= 0 and d % cada == 0


def compromisos(items: list[dict], deudas: list[dict], mes: str) -> list[dict]:
    """Lo que hay que pagar ESE mes: los dos maestros en una sola lista.

    Un gasto que no se cobra ese mes simplemente no aparece, así que el total
    del mes cambia según toque o no el bimestral, el semestral o el anual.
    """
    de_gastos = [
        {"origen": "gasto", "ref": i["id"], "nombre": i["nombre"], "monto": i["monto"],
         "categoria": i["categoria"], "quincena": i["quincena"],
         "cada_meses": i.get("cada_meses") or 1}
        for i in items
        if cae_en_mes(i, mes)
    ]
    de_deudas = [
        {"origen": "deuda", "ref": d["id"], "nombre": d["nombre"], "monto": d["cuota"],
         "categoria": CAT_DEUDA[d["tipo"]], "quincena": d["quincena"]}
        for d in deudas
    ]
    return de_gastos + de_deudas


def parte_quincena(c: dict, q: str) -> int:
    """Lo que cae en la quincena q. Un compromiso de 'ambas' se parte por la mitad."""
    if c["quincena"] == "ambas":
        return round(c["monto"] / 2)
    return c["monto"] if c["quincena"] == q else 0


def resumen(sueldo: int, items: list[dict], deudas: list[dict],
            pagados: set[tuple[str, int, str]], bonos: dict[str, dict] | None = None,
            mes: str = "1970-01") -> dict:
    """Los números de ese mes. `bonos` son los de ese mes, por quincena: entran
    como ingreso extra, así que un mes con bono deja más libre que uno sin él."""
    bonos = bonos or {}
    lista = compromisos(items, deudas, mes)
    total = sum(c["monto"] for c in lista)

    por_quincena = []
    for q in ("1", "2"):
        filas = [
            {**c, "parte": parte_quincena(c, q), "pagado": (c["origen"], c["ref"], q) in pagados}
            for c in lista
            if parte_quincena(c, q) > 0
        ]
        filas.sort(key=lambda f: -f["parte"])
        debe = sum(f["parte"] for f in filas)
        bono = bonos.get(q)
        sueldo_q = round(sueldo / 2)
        ingreso = sueldo_q + (bono["monto"] if bono else 0)
        por_quincena.append({
            "quincena": q,
            "sueldo": sueldo_q,
            "bono": bono,
            "ingreso": ingreso,
            "debe": debe,
            "pagado": sum(f["parte"] for f in filas if f["pagado"]),
            "libre": ingreso - debe,
            "filas": filas,
        })
    bono_mes = sum(q["bono"]["monto"] for q in por_quincena if q["bono"])

    por_categoria = [
        {"categoria": cat, "monto": sum(c["monto"] for c in lista if c["categoria"] == cat)}
        for cat in CATEGORIAS_GRAFICO
    ]
    vistas = [mirar_deuda(d) for d in deudas]
    tarjetas = [d for d in vistas if d["tipo"] == "tarjeta"]
    cupo = sum(t["tope"] for t in tarjetas)
    usado = sum(t["saldo"] for t in tarjetas)
    return {
        "sueldo": sueldo,
        "bono_mes": bono_mes,
        "ingreso_mes": sueldo + bono_mes,
        "total": total,
        "libre": sueldo + bono_mes - total,
        "pendientes": sum(1 for q in por_quincena for f in q["filas"] if not f["pagado"]),
        "por_quincena": por_quincena,
        "por_categoria": [c for c in por_categoria if c["monto"] > 0],
        "deudas": sorted(vistas, key=lambda d: (d["tipo"], -d["saldo"])),
        "deuda_total": sum(d["saldo"] for d in vistas),
        "cuota_deuda": sum(d["cuota"] for d in vistas),
        "interes_mes": sum(d["plan"]["interes_mes"] for d in vistas if d["plan"]),
        "cupo_total": cupo,
        "cupo_disponible": cupo - usado,
        "proyeccion": proyeccion(vistas),
    }


# ---------------------------------------------------------------- API
class Login(BaseModel):
    usuario: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class Item(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    monto: int = Field(ge=0, le=10**10)
    categoria: str
    quincena: str
    cada_meses: int = Field(default=1, ge=1, le=24)
    desde: str = Field(default="", pattern=r"^(\d{4}-\d{2})?$")

    def validar(self):
        if self.categoria not in CATEGORIAS:
            raise HTTPException(422, "Esa categoría no existe. Las tarjetas y créditos "
                                     "van en su propio maestro.")
        if self.quincena not in QUINCENAS:
            raise HTTPException(422, "La quincena debe ser 1, 2 o ambas.")
        if self.cada_meses > 1 and not self.desde:
            raise HTTPException(422, "Dime desde qué mes se cobra, para saber en cuáles cae.")
        if self.desde and not 1 <= int(self.desde[5:]) <= 12:
            raise HTTPException(422, "Ese mes no existe.")
        return self


class Deuda(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    tipo: str = "tarjeta"
    tope: int = Field(default=0, ge=0, le=10**12)
    saldo: int = Field(default=0, ge=0, le=10**12)
    cuota: int = Field(default=0, ge=0, le=10**10)
    dia_pago: int = Field(default=0, ge=0, le=31)
    tasa: float = Field(default=0, ge=0, le=500)  # % efectivo anual
    quincena: str = "1"

    def validar(self):
        if self.tipo not in TIPOS:
            raise HTTPException(422, "El tipo debe ser tarjeta o credito.")
        if self.quincena not in QUINCENAS:
            raise HTTPException(422, "La quincena debe ser 1, 2 o ambas.")
        if self.tope and self.saldo > self.tope:
            raise HTTPException(422, "El saldo no puede pasarse del cupo de la tarjeta."
                                if self.tipo == "tarjeta" else
                                "El saldo no puede ser mayor que el valor inicial del crédito.")
        return self


class Sueldo(BaseModel):
    sueldo: int = Field(ge=0, le=10**10)


class Bono(BaseModel):
    mes: str = Field(pattern=r"^\d{4}-\d{2}$")
    quincena: str
    monto: int = Field(ge=0, le=10**10)
    nota: str = Field(default="", max_length=60)


class Pago(BaseModel):
    mes: str = Field(pattern=r"^\d{4}-\d{2}$")
    origen: str
    ref: int
    quincena: str
    pagado: bool


app = FastAPI(title="Vida Nueva")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"], allow_headers=["*"],
)
init_db()


@app.post("/api/login")
def login(body: Login):
    if not USUARIO or not PASSWORD:
        raise HTTPException(500, "Falta FINANZAS_USER o FINANZAS_PASSWORD en el .env.")
    time.sleep(0.25)  # ponytail: freno simple a la fuerza bruta; basta para una app de un usuario
    ok_u = hmac.compare_digest(body.usuario, USUARIO)
    ok_p = hmac.compare_digest(body.password, PASSWORD)
    if not (ok_u and ok_p):
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    with conn() as c:
        secret = cfg_get(c, "secret")
    return {"token": sign_token(secret, int(time.time()) + TOKEN_TTL)}


@app.get("/api/state", dependencies=[Depends(auth)])
def state(mes: str):
    if len(mes) != 7 or mes[4] != "-" or not (mes[:4] + mes[5:]).isdigit():
        raise HTTPException(422, "El mes debe venir como AAAA-MM.")
    with conn() as c:
        sueldo = cfg_get(c, "sueldo", 0)
        items = [dict(r) for r in c.execute("SELECT * FROM items ORDER BY monto DESC")]
        deudas = [dict(r) for r in c.execute("SELECT * FROM deudas ORDER BY saldo DESC")]
        pagados = {(r["origen"], r["ref"], r["quincena"])
                   for r in c.execute("SELECT origen,ref,quincena FROM pagos WHERE mes=?", (mes,))}
        bonos = {r["quincena"]: {"monto": r["monto"], "nota": r["nota"]}
                 for r in c.execute("SELECT quincena,monto,nota FROM bonos WHERE mes=?", (mes,))}
    return {"mes": mes, "items": items, "categorias": CATEGORIAS,
            **resumen(sueldo, items, deudas, pagados, bonos, mes)}


@app.put("/api/sueldo", dependencies=[Depends(auth)])
def set_sueldo(body: Sueldo):
    with conn() as c:
        cfg_set(c, "sueldo", body.sueldo)
    return {"ok": True}


# --- maestro: gastos fijos ---
@app.post("/api/items", dependencies=[Depends(auth)])
def crear_item(body: Item):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO items(nombre,monto,categoria,quincena,cada_meses,desde)"
            " VALUES(?,?,?,?,?,?)",
            (body.nombre, body.monto, body.categoria, body.quincena, body.cada_meses, body.desde))
        return {"id": cur.lastrowid, **body.model_dump()}


@app.put("/api/items/{item_id}", dependencies=[Depends(auth)])
def editar_item(item_id: int, body: Item):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "UPDATE items SET nombre=?,monto=?,categoria=?,quincena=?,cada_meses=?,desde=?"
            " WHERE id=?",
            (body.nombre, body.monto, body.categoria, body.quincena, body.cada_meses,
             body.desde, item_id))
        if not cur.rowcount:
            raise HTTPException(404, "Ese gasto ya no existe.")
    return {"id": item_id, **body.model_dump()}


@app.delete("/api/items/{item_id}", dependencies=[Depends(auth)])
def borrar_item(item_id: int):
    with conn() as c:
        c.execute("DELETE FROM pagos WHERE origen='gasto' AND ref=?", (item_id,))
        c.execute("DELETE FROM items WHERE id=?", (item_id,))
    return {"ok": True}


# --- maestro: tarjetas y créditos ---
@app.post("/api/deudas", dependencies=[Depends(auth)])
def crear_deuda(body: Deuda):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO deudas(nombre,tipo,tope,saldo,cuota,dia_pago,tasa,quincena)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (body.nombre, body.tipo, body.tope, body.saldo, body.cuota, body.dia_pago,
             body.tasa, body.quincena))
        return {"id": cur.lastrowid, **body.model_dump()}


@app.put("/api/deudas/{deuda_id}", dependencies=[Depends(auth)])
def editar_deuda(deuda_id: int, body: Deuda):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "UPDATE deudas SET nombre=?,tipo=?,tope=?,saldo=?,cuota=?,dia_pago=?,tasa=?,quincena=?"
            " WHERE id=?",
            (body.nombre, body.tipo, body.tope, body.saldo, body.cuota, body.dia_pago,
             body.tasa, body.quincena, deuda_id))
        if not cur.rowcount:
            raise HTTPException(404, "Esa deuda ya no existe.")
    return {"id": deuda_id, **body.model_dump()}


@app.delete("/api/deudas/{deuda_id}", dependencies=[Depends(auth)])
def borrar_deuda(deuda_id: int):
    with conn() as c:
        c.execute("DELETE FROM pagos WHERE origen='deuda' AND ref=?", (deuda_id,))
        c.execute("DELETE FROM deudas WHERE id=?", (deuda_id,))
    return {"ok": True}


@app.post("/api/bonos", dependencies=[Depends(auth)])
def guardar_bono(body: Bono):
    """Un bono por quincena. Monto en cero lo borra, que es como se quita."""
    if body.quincena not in ("1", "2"):
        raise HTTPException(422, "La quincena debe ser 1 o 2.")
    with conn() as c:
        if body.monto:
            c.execute("INSERT INTO bonos(mes,quincena,monto,nota) VALUES(?,?,?,?)"
                      " ON CONFLICT(mes,quincena) DO UPDATE SET monto=excluded.monto,"
                      " nota=excluded.nota",
                      (body.mes, body.quincena, body.monto, body.nota.strip()))
        else:
            c.execute("DELETE FROM bonos WHERE mes=? AND quincena=?", (body.mes, body.quincena))
    return {"ok": True}


@app.post("/api/pagos", dependencies=[Depends(auth)])
def marcar_pago(body: Pago):
    if body.quincena not in ("1", "2"):
        raise HTTPException(422, "La quincena debe ser 1 o 2.")
    if body.origen not in ("gasto", "deuda"):
        raise HTTPException(422, "El origen debe ser gasto o deuda.")
    with conn() as c:
        if body.pagado:
            c.execute("INSERT OR IGNORE INTO pagos(mes,origen,ref,quincena) VALUES(?,?,?,?)",
                      (body.mes, body.origen, body.ref, body.quincena))
        else:
            c.execute("DELETE FROM pagos WHERE mes=? AND origen=? AND ref=? AND quincena=?",
                      (body.mes, body.origen, body.ref, body.quincena))
    return {"ok": True}


if DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        return FileResponse(DIST / "index.html")


# ---------------------------------------------------------------- autocomprobación
def _test():
    # Tasa: una efectiva anual del 26,82% es 2% mensual.
    assert abs(tasa_mensual(26.8242) - 0.02) < 1e-5, tasa_mensual(26.8242)
    assert tasa_mensual(0) == 0.0

    # Sin tasa, amortizar es dividir saldo entre cuota.
    assert plan_pago(2000000, 500000, 0)["cuotas"] == 4
    assert plan_pago(2000001, 500000, 0)["cuotas"] == 5  # el resto deja otra cuota
    assert plan_pago(2000000, 500000, 0)["interes_total"] == 0

    # Con tasa, la misma deuda tarda más y el interés se nota.
    con = plan_pago(2000000, 500000, 26.8242)
    assert con["cuotas"] == 5, con          # 4 cuotas ya no alcanzan
    assert con["interes_mes"] == 40000, con  # 2% de 2.000.000
    assert con["abono_capital"] == 460000, con
    assert 100000 < con["interes_total"] < 150000, con
    assert plan_pago(2000000, 200000, 26.8242)["interes_total"] > con["interes_total"]

    # La cuota que no cubre ni los intereses: la deuda crece y no termina nunca.
    ahoga = plan_pago(2000000, 40000, 26.8242)
    assert ahoga["crece"] and ahoga["cuotas"] is None and ahoga["interes_total"] is None, ahoga
    assert plan_pago(2000000, 39999, 26.8242)["crece"]
    assert not plan_pago(2000000, 40001, 26.8242)["crece"]
    assert plan_pago(0, 500000, 20) is None and plan_pago(2000000, 0, 20) is None

    # El mismo `tope` se lee al derecho en una tarjeta y al revés en un crédito.
    base = {"nombre": "X", "tope": 8000000, "saldo": 2000000, "cuota": 500000,
            "dia_pago": 0, "tasa": 0, "quincena": "1"}
    tar = mirar_deuda({**base, "tipo": "tarjeta"})
    cre = mirar_deuda({**base, "tipo": "credito"})
    assert (tar["categoria"], cre["categoria"]) == ("Tarjetas", "Crédito")
    assert tar["uso"] == 0.25 and tar["disponible"] == 6000000   # usaste un cuarto del cupo
    assert cre["avance"] == 0.75 and cre["pagado"] == 6000000    # pagaste tres cuartos
    assert tar["plan"]["cuotas"] == 4
    sin_tope = mirar_deuda({**base, "tipo": "tarjeta", "tope": 0})
    assert sin_tope["uso"] is None and sin_tope["avance"] is None and sin_tope["disponible"] is None
    assert mirar_deuda({**base, "tipo": "tarjeta", "saldo": 9999999})["uso"] == 1  # nunca pasa de 1

    # Juego de datos propio, con cifras redondas, para no depender de la semilla.
    gastos = [("Arriendo", 1000000, "Vivienda", "ambas"),
              ("Internet", 100000, "Servicios", "2")]
    deudas_raw = [("Tarjeta A", "tarjeta", 200000, "1"),
                  ("Tarjeta B", "tarjeta", 300000, "2"),
                  ("Crédito", "credito", 400000, "1")]
    items = [{"id": n, "nombre": nom, "monto": m, "categoria": c, "quincena": q}
             for n, (nom, m, c, q) in enumerate(gastos, 1)]
    deudas = [{"id": n, "nombre": nom, "tipo": t, "tope": 0, "saldo": 0, "cuota": cu,
               "dia_pago": 0, "tasa": 0, "quincena": q}
              for n, (nom, t, cu, q) in enumerate(deudas_raw, 1)]

    r = resumen(2400000, items, deudas, pagados={("gasto", 1, "1")})
    assert r["total"] == 2000000, r["total"]
    assert r["libre"] == 400000, r["libre"]
    q1, q2 = r["por_quincena"]
    assert q1["debe"] + q2["debe"] == r["total"], (q1["debe"], q2["debe"])
    assert (q1["debe"], q2["debe"]) == (1100000, 900000), (q1["debe"], q2["debe"])
    assert q1["libre"] == 100000 and q2["libre"] == 300000
    assert q1["pagado"] == 500000, q1["pagado"]  # arriendo marcado = media cuota
    assert r["pendientes"] == len(q1["filas"]) + len(q2["filas"]) - 1
    assert r["cuota_deuda"] == 900000, r["cuota_deuda"]
    assert sum(c["monto"] for c in r["por_categoria"]) == r["total"]
    assert [c["categoria"] for c in r["por_categoria"]] == \
        [c for c in CATEGORIAS_GRAFICO if c in {x["categoria"] for x in r["por_categoria"]}]

    # Cuándo quedas libre: manda la deuda que más tarda, no la suma de todas.
    assert sumar_meses("2026-10", 0) == "2026-10"
    assert sumar_meses("2026-10", 3) == "2027-01"
    assert sumar_meses("2026-12", 1) == "2027-01"
    assert sumar_meses("2026-01", 23) == "2027-12"
    d = lambda **kw: mirar_deuda({"nombre": "X", "tipo": "tarjeta", "tope": 0, "saldo": 0,
                                  "cuota": 0, "dia_pago": 0, "tasa": 0, "quincena": "1", **kw})
    corta = d(nombre="Corta", saldo=400000, cuota=200000)       # 2 cuotas
    larga = d(nombre="Larga", saldo=1000000, cuota=200000)      # 5 cuotas
    p = proyeccion([corta, larga], "2026-10")
    assert (p["cuotas"], p["ultima"]) == (5, "Larga"), p
    assert p["fin"] == "2027-02", p["fin"]  # octubre es la primera de las 5
    assert p["deudas"] == 2 and sorted(p["sin_tasa"]) == ["Corta", "Larga"]
    assert p["fin_sin_la_ultima"] == "2026-11", p  # sin la Larga mandaria la Corta, 2 cuotas
    assert proyeccion([corta], "2026-10")["fin_sin_la_ultima"] is None  # con una sola, no aplica
    assert p["interes_total"] == 0 and not p["nunca_termina"] and not p["sin_cuota"]

    # Con tasa tarda más y cobra intereses; el estimado sin tasa se queda corto.
    con_tasa = proyeccion([d(nombre="Larga", saldo=1000000, cuota=200000, tasa=26.8242)], "2026-10")
    assert con_tasa["cuotas"] > 5 and con_tasa["interes_total"] > 0, con_tasa
    assert con_tasa["sin_tasa"] == []

    # Hojear meses no mueve la fecha: el saldo guardado sigue siendo el de hoy.
    hoy = proyeccion([larga])
    assert hoy["desde"] == mes_actual() and hoy["fin"] == sumar_meses(mes_actual(), 4)
    assert proyeccion([larga], "2026-10")["fin"] == "2027-02"
    assert proyeccion([larga], "2027-08")["fin"] == "2027-12"  # anclada aparte, se mueve con el ancla

    # Una deuda saldada no cuenta; sin deudas no hay fecha.
    assert proyeccion([d(saldo=0, cuota=200000)], "2026-10")["deudas"] == 0
    assert proyeccion([], "2026-10")["fin"] is None

    # Las que impiden dar fecha se nombran, y no tumban el resto del cálculo.
    rara = proyeccion([larga,
                       d(nombre="Ahogada", saldo=2000000, cuota=40000, tasa=26.8242),
                       d(nombre="Sin cuota", saldo=500000, cuota=0)], "2026-10")
    assert rara["nunca_termina"] == ["Ahogada"] and rara["sin_cuota"] == ["Sin cuota"]
    assert rara["deudas"] == 3 and rara["cuotas"] == 5, rara  # sigue dando la de las que sí

    # Periodicidad: cada 4 quincenas es cada 2 meses, y `desde` dice en cuál empieza.
    assert meses_entre("2026-10", "2026-12") == 2
    assert meses_entre("2026-11", "2027-02") == 3
    assert meses_entre("2026-12", "2026-10") == -2
    cada2 = {"cada_meses": 2, "desde": "2026-10"}
    assert [m for m in ("2026-09", "2026-10", "2026-11", "2026-12", "2027-01", "2027-02")
            if cae_en_mes(cada2, m)] == ["2026-10", "2026-12", "2027-02"]
    assert not cae_en_mes(cada2, "2026-08"), "antes del primer cobro no cae"
    anual = {"cada_meses": 12, "desde": "2026-03"}
    assert cae_en_mes(anual, "2027-03") and not cae_en_mes(anual, "2027-02")
    # Lo normal sigue siendo mensual, y sin ancla cae siempre.
    assert all(cae_en_mes({"cada_meses": 1, "desde": ""}, m) for m in ("2026-10", "2026-11"))
    assert cae_en_mes({"cada_meses": 2, "desde": ""}, "2026-11")
    assert cae_en_mes({}, "2026-11")

    # Un gasto bimestral no suma en el mes que no toca.
    bimestral = [{"id": 99, "nombre": "Lavada del carro", "monto": 200000,
                  "categoria": "Otros", "quincena": "1", **cada2}]
    toca = resumen(2400000, items + bimestral, deudas, set(), mes="2026-10")
    no_toca = resumen(2400000, items + bimestral, deudas, set(), mes="2026-11")
    assert toca["total"] == no_toca["total"] + 200000, (toca["total"], no_toca["total"])
    assert toca["libre"] == no_toca["libre"] - 200000
    assert any(f["nombre"] == "Lavada del carro" for f in toca["por_quincena"][0]["filas"])
    assert not any(f["nombre"] == "Lavada del carro"
                   for q in no_toca["por_quincena"] for f in q["filas"])
    assert no_toca["total"] == r["total"], "el mes sin el bimestral queda como si no existiera"

    # Un bono entra como ingreso de su quincena y solo de esa.
    con_bono = resumen(2400000, items, deudas, pagados=set(),
                       bonos={"1": {"monto": 600000, "nota": "Prima"}})
    b1, b2 = con_bono["por_quincena"]
    assert (b1["sueldo"], b1["ingreso"]) == (1200000, 1800000), b1
    assert b1["bono"]["nota"] == "Prima" and b2["bono"] is None
    assert (b2["sueldo"], b2["ingreso"]) == (1200000, 1200000), b2
    assert b1["libre"] == 1800000 - b1["debe"], b1      # el bono sube lo libre de la quincena
    assert b2["libre"] == r["por_quincena"][1]["libre"]  # la otra quincena no cambia
    assert con_bono["bono_mes"] == 600000
    assert con_bono["ingreso_mes"] == 2400000 + 600000
    assert con_bono["libre"] == r["libre"] + 600000, con_bono["libre"]
    assert con_bono["total"] == r["total"], "un bono no es un gasto"

    # Bonos en las dos quincenas se suman; sin bonos nada cambia.
    dos = resumen(2400000, items, deudas, pagados=set(),
                  bonos={"1": {"monto": 100000, "nota": ""}, "2": {"monto": 50000, "nota": "Bono"}})
    assert dos["bono_mes"] == 150000 and dos["libre"] == r["libre"] + 150000
    sin = resumen(2400000, items, deudas, pagados=set(), bonos={})
    assert sin["bono_mes"] == 0 and sin["libre"] == r["libre"]
    assert all(q["bono"] is None for q in sin["por_quincena"])

    # Un gasto y una deuda con el mismo id no se confunden al marcar el pago.
    solo_gasto = resumen(2400000, items, deudas, pagados={("gasto", 2, "2")})
    solo_deuda = resumen(2400000, items, deudas, pagados={("deuda", 2, "2")})
    assert solo_gasto["por_quincena"][1]["pagado"] == 100000  # Internet
    assert solo_deuda["por_quincena"][1]["pagado"] == 300000  # Tarjeta B

    # El cupo es solo de las tarjetas; un crédito no tiene cupo rotativo.
    con_cifras = [{**d, "tope": 2000000, "saldo": 500000} for d in deudas]
    r2 = resumen(2400000, items, con_cifras, pagados=set())
    assert r2["cupo_total"] == 4000000 and r2["cupo_disponible"] == 3000000, r2["cupo_total"]
    assert r2["deuda_total"] == 1500000, r2["deuda_total"]
    assert r2["interes_mes"] == 0, "sin tasa registrada, nada del pago es interes"

    r3 = resumen(2400000, items, [{**d, "tope": 2000000, "saldo": 500000, "tasa": 26.8242}
                                  for d in deudas], pagados=set())
    assert r3["interes_mes"] == round(500000 * 0.02) * 3, r3["interes_mes"]
    assert all(d["plan"] for d in r3["deudas"])

    s = secrets.token_hex(16)
    t = sign_token(s, int(time.time()) + 60)
    assert verify_token(s, t)
    assert not verify_token(s, sign_token(s, int(time.time()) - 60))  # vencido
    assert not verify_token(s, t[:-1] + ("0" if t[-1] != "0" else "1"))  # firma alterada
    assert not verify_token(s, "basura")
    assert not verify_token(secrets.token_hex(16), t)  # otro secreto

    import tempfile
    env = Path(tempfile.mkdtemp()) / ".env"
    env.write_text('# comentario\nFINANZAS_USER = ana \nFINANZAS_PASSWORD="con espacio"\nbasura\n', encoding="utf-8")
    os.environ.pop("FINANZAS_USER", None)
    os.environ.pop("FINANZAS_PASSWORD", None)
    cargar_env(env)
    assert os.environ["FINANZAS_USER"] == "ana", os.environ["FINANZAS_USER"]
    assert os.environ["FINANZAS_PASSWORD"] == "con espacio"
    os.environ["FINANZAS_USER"] = "ya-estaba"
    cargar_env(env)
    assert os.environ["FINANZAS_USER"] == "ya-estaba"  # el entorno manda sobre el .env

    for f in ("semilla.json", "semilla.example.json"):
        if not (BASE / f).is_file():
            continue
        d = json.loads((BASE / f).read_text(encoding="utf-8"))
        assert isinstance(d["sueldo"], int) and d["sueldo"] > 0, f
        assert all(len(g) == 4 and g[2] in CATEGORIAS and g[3] in QUINCENAS for g in d["gastos"]), f
        assert all(len(x) == 4 and x[1] in TIPOS and x[3] in QUINCENAS for x in d["deudas"]), f
    print("OK — cálculo, un maestro de deudas, token y .env")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
