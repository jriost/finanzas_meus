"""Vida Nueva — API de finanzas personales (un solo usuario).

Arranque:  uvicorn app:app --reload    (desde esta carpeta)
Usuario:   FINANZAS_USER y FINANZAS_PASSWORD en el .env de la raíz del proyecto.
Datos:     SQLite (finanzas.db) con dos maestros —gastos fijos y tarjetas— más
           el sueldo y los pagos marcados de cada mes.
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

# "Tarjetas" no es categoría de un gasto fijo: las tarjetas son su propio maestro.
CATEGORIAS = ["Vivienda", "Crédito", "Servicios", "Comida y vida", "Salud", "Otros"]
CATEGORIAS_GRAFICO = ["Vivienda", "Tarjetas", "Crédito", "Servicios", "Comida y vida", "Salud", "Otros"]
QUINCENAS = ["1", "2", "ambas"]
TOKEN_TTL = 60 * 60 * 24 * 30  # 30 días

def cargar_semilla() -> tuple[int, list, list]:
    """Con qué llenar una base vacía: semilla.json si existe, si no la plantilla.

    Los gastos van como [nombre, monto, categoría, quincena, deuda] y las
    tarjetas como [nombre, cuota, quincena]; cupo, saldo y día de pago empiezan
    en cero porque solo tú los sabes.
    """
    ruta = BASE / "semilla.json"
    if not ruta.is_file():
        ruta = BASE / "semilla.example.json"
    d = json.loads(ruta.read_text(encoding="utf-8"))
    return d["sueldo"], d["gastos"], d["tarjetas"]


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


def init_db():
    with conn() as c:
        c.executescript("""
          CREATE TABLE IF NOT EXISTS config(k TEXT PRIMARY KEY, v TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, monto INTEGER NOT NULL,
            categoria TEXT NOT NULL, quincena TEXT NOT NULL, deuda INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS tarjetas(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nombre TEXT NOT NULL, cupo INTEGER NOT NULL DEFAULT 0,
            saldo INTEGER NOT NULL DEFAULT 0, cuota INTEGER NOT NULL DEFAULT 0,
            dia_pago INTEGER NOT NULL DEFAULT 0, quincena TEXT NOT NULL DEFAULT '1');
          CREATE TABLE IF NOT EXISTS pagos(
            mes TEXT NOT NULL, origen TEXT NOT NULL, ref INTEGER NOT NULL, quincena TEXT NOT NULL,
            PRIMARY KEY(mes, origen, ref, quincena));
        """)

        # Bases creadas antes de que las tarjetas fueran su propio maestro.
        if "item_id" in columnas(c, "pagos"):
            c.executescript("""
              ALTER TABLE pagos RENAME TO pagos_viejo;
              CREATE TABLE pagos(
                mes TEXT NOT NULL, origen TEXT NOT NULL, ref INTEGER NOT NULL, quincena TEXT NOT NULL,
                PRIMARY KEY(mes, origen, ref, quincena));
              INSERT INTO pagos(mes,origen,ref,quincena)
                SELECT mes,'item',item_id,quincena FROM pagos_viejo;
              DROP TABLE pagos_viejo;
            """)
        viejas = c.execute("SELECT * FROM items WHERE categoria='Tarjetas'").fetchall()
        for t in viejas:
            cur = c.execute("INSERT INTO tarjetas(nombre,saldo,cuota,quincena) VALUES(?,?,?,?)",
                            (t["nombre"], t["deuda"], t["monto"], t["quincena"]))
            c.execute("UPDATE pagos SET origen='tarjeta', ref=? WHERE origen='item' AND ref=?",
                      (cur.lastrowid, t["id"]))
            c.execute("DELETE FROM items WHERE id=?", (t["id"],))

        if cfg_get(c, "secret") is None:
            cfg_set(c, "secret", secrets.token_hex(32))
        sueldo, gastos, tarjetas = cargar_semilla()
        if cfg_get(c, "sueldo") is None:
            cfg_set(c, "sueldo", sueldo)
        if not c.execute("SELECT 1 FROM items LIMIT 1").fetchone():
            c.executemany("INSERT INTO items(nombre,monto,categoria,quincena,deuda) VALUES(?,?,?,?,?)",
                          gastos)
        if not c.execute("SELECT 1 FROM tarjetas LIMIT 1").fetchone():
            c.executemany("INSERT INTO tarjetas(nombre,cuota,quincena) VALUES(?,?,?)", tarjetas)


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
def compromisos(items: list[dict], tarjetas: list[dict]) -> list[dict]:
    """Los dos maestros vistos como una sola lista de cosas por pagar."""
    de_items = [
        {"origen": "item", "ref": i["id"], "nombre": i["nombre"], "monto": i["monto"],
         "categoria": i["categoria"], "quincena": i["quincena"]}
        for i in items
    ]
    de_tarjetas = [
        {"origen": "tarjeta", "ref": t["id"], "nombre": t["nombre"], "monto": t["cuota"],
         "categoria": "Tarjetas", "quincena": t["quincena"]}
        for t in tarjetas
    ]
    return de_items + de_tarjetas


def parte_quincena(c: dict, q: str) -> int:
    """Lo que cae en la quincena q. Un compromiso de 'ambas' se parte por la mitad."""
    if c["quincena"] == "ambas":
        return round(c["monto"] / 2)
    return c["monto"] if c["quincena"] == q else 0


def resumen(sueldo: int, items: list[dict], tarjetas: list[dict],
            pagados: set[tuple[str, int, str]]) -> dict:
    lista = compromisos(items, tarjetas)
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
        por_quincena.append({
            "quincena": q,
            "ingreso": round(sueldo / 2),
            "debe": debe,
            "pagado": sum(f["parte"] for f in filas if f["pagado"]),
            "libre": round(sueldo / 2) - debe,
            "filas": filas,
        })

    por_categoria = [
        {"categoria": cat, "monto": sum(c["monto"] for c in lista if c["categoria"] == cat)}
        for cat in CATEGORIAS_GRAFICO
    ]
    creditos = [i for i in items if i["categoria"] == "Crédito"]
    cupo = sum(t["cupo"] for t in tarjetas)
    saldo = sum(t["saldo"] for t in tarjetas)
    return {
        "sueldo": sueldo,
        "total": total,
        "libre": sueldo - total,
        "pendientes": sum(1 for q in por_quincena for f in q["filas"] if not f["pagado"]),
        "por_quincena": por_quincena,
        "por_categoria": [c for c in por_categoria if c["monto"] > 0],
        "deuda_total": saldo + sum(i["deuda"] for i in creditos),
        "cuota_deuda": sum(t["cuota"] for t in tarjetas) + sum(i["monto"] for i in creditos),
        "cupo_total": cupo,
        "cupo_disponible": cupo - saldo,
        "creditos": sorted(creditos, key=lambda i: -i["monto"]),
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
    deuda: int = Field(default=0, ge=0, le=10**12)

    def validar(self):
        if self.categoria not in CATEGORIAS:
            raise HTTPException(422, "Categoría desconocida.")
        if self.quincena not in QUINCENAS:
            raise HTTPException(422, "La quincena debe ser 1, 2 o ambas.")
        return self


class Tarjeta(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    cupo: int = Field(default=0, ge=0, le=10**12)
    saldo: int = Field(default=0, ge=0, le=10**12)
    cuota: int = Field(default=0, ge=0, le=10**10)
    dia_pago: int = Field(default=0, ge=0, le=31)
    quincena: str = "1"

    def validar(self):
        if self.quincena not in QUINCENAS:
            raise HTTPException(422, "La quincena debe ser 1, 2 o ambas.")
        if self.cupo and self.saldo > self.cupo:
            raise HTTPException(422, "El saldo no puede pasarse del cupo.")
        return self


class Sueldo(BaseModel):
    sueldo: int = Field(ge=0, le=10**10)


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
        tarjetas = [dict(r) for r in c.execute("SELECT * FROM tarjetas ORDER BY cuota DESC")]
        pagados = {(r["origen"], r["ref"], r["quincena"])
                   for r in c.execute("SELECT origen,ref,quincena FROM pagos WHERE mes=?", (mes,))}
    return {
        "mes": mes, "items": items, "tarjetas": tarjetas, "categorias": CATEGORIAS,
        **resumen(sueldo, items, tarjetas, pagados),
    }


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
        cur = c.execute("INSERT INTO items(nombre,monto,categoria,quincena,deuda) VALUES(?,?,?,?,?)",
                        (body.nombre, body.monto, body.categoria, body.quincena, body.deuda))
        return {"id": cur.lastrowid, **body.model_dump()}


@app.put("/api/items/{item_id}", dependencies=[Depends(auth)])
def editar_item(item_id: int, body: Item):
    body.validar()
    with conn() as c:
        cur = c.execute("UPDATE items SET nombre=?,monto=?,categoria=?,quincena=?,deuda=? WHERE id=?",
                        (body.nombre, body.monto, body.categoria, body.quincena, body.deuda, item_id))
        if not cur.rowcount:
            raise HTTPException(404, "Ese gasto ya no existe.")
    return {"id": item_id, **body.model_dump()}


@app.delete("/api/items/{item_id}", dependencies=[Depends(auth)])
def borrar_item(item_id: int):
    with conn() as c:
        c.execute("DELETE FROM pagos WHERE origen='item' AND ref=?", (item_id,))
        c.execute("DELETE FROM items WHERE id=?", (item_id,))
    return {"ok": True}


# --- maestro: tarjetas de crédito ---
@app.post("/api/tarjetas", dependencies=[Depends(auth)])
def crear_tarjeta(body: Tarjeta):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO tarjetas(nombre,cupo,saldo,cuota,dia_pago,quincena) VALUES(?,?,?,?,?,?)",
            (body.nombre, body.cupo, body.saldo, body.cuota, body.dia_pago, body.quincena))
        return {"id": cur.lastrowid, **body.model_dump()}


@app.put("/api/tarjetas/{tarjeta_id}", dependencies=[Depends(auth)])
def editar_tarjeta(tarjeta_id: int, body: Tarjeta):
    body.validar()
    with conn() as c:
        cur = c.execute(
            "UPDATE tarjetas SET nombre=?,cupo=?,saldo=?,cuota=?,dia_pago=?,quincena=? WHERE id=?",
            (body.nombre, body.cupo, body.saldo, body.cuota, body.dia_pago, body.quincena, tarjeta_id))
        if not cur.rowcount:
            raise HTTPException(404, "Esa tarjeta ya no existe.")
    return {"id": tarjeta_id, **body.model_dump()}


@app.delete("/api/tarjetas/{tarjeta_id}", dependencies=[Depends(auth)])
def borrar_tarjeta(tarjeta_id: int):
    with conn() as c:
        c.execute("DELETE FROM pagos WHERE origen='tarjeta' AND ref=?", (tarjeta_id,))
        c.execute("DELETE FROM tarjetas WHERE id=?", (tarjeta_id,))
    return {"ok": True}


@app.post("/api/pagos", dependencies=[Depends(auth)])
def marcar_pago(body: Pago):
    if body.quincena not in ("1", "2"):
        raise HTTPException(422, "La quincena debe ser 1 o 2.")
    if body.origen not in ("item", "tarjeta"):
        raise HTTPException(422, "El origen debe ser item o tarjeta.")
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
    # Juego de datos propio, con cifras redondas, para no depender de la semilla.
    gastos = [("Arriendo", 1000000, "Vivienda", "ambas", 0),
              ("Crédito", 400000, "Crédito", "1", 0),
              ("Internet", 100000, "Servicios", "2", 0)]
    tarj = [("Tarjeta A", 200000, "1"), ("Tarjeta B", 300000, "2")]
    items = [{"id": n, "nombre": nom, "monto": m, "categoria": c, "quincena": q, "deuda": d}
             for n, (nom, m, c, q, d) in enumerate(gastos, 1)]
    tarjetas = [{"id": n, "nombre": nom, "cupo": 0, "saldo": 0, "cuota": cu, "dia_pago": 0, "quincena": q}
                for n, (nom, cu, q) in enumerate(tarj, 1)]

    r = resumen(2400000, items, tarjetas, pagados={("item", 1, "1")})
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

    # Un item y una tarjeta con el mismo id no se confunden al marcar el pago.
    solo_item = resumen(2400000, items, tarjetas, pagados={("item", 2, "1")})
    solo_tarj = resumen(2400000, items, tarjetas, pagados={("tarjeta", 2, "2")})
    assert solo_item["por_quincena"][0]["pagado"] == 400000  # el crédito
    assert solo_tarj["por_quincena"][1]["pagado"] == 300000  # la tarjeta B

    # Cupo y saldo salen de las tarjetas; la deuda del crédito, de los gastos.
    con_cupo = [{**t, "cupo": 2000000, "saldo": 500000} for t in tarjetas]
    con_credito = [{**i, "deuda": 6000000 if i["nombre"] == "Crédito" else 0} for i in items]
    r2 = resumen(2400000, con_credito, con_cupo, pagados=set())
    assert r2["cupo_total"] == 4000000 and r2["cupo_disponible"] == 3000000
    assert r2["deuda_total"] == 1000000 + 6000000, r2["deuda_total"]

    # Las dos semillas se leen y tienen la forma que init_db espera.
    for f in ("semilla.json", "semilla.example.json"):
        if not (BASE / f).is_file():
            continue
        d = json.loads((BASE / f).read_text(encoding="utf-8"))
        assert isinstance(d["sueldo"], int) and d["sueldo"] > 0, f
        assert all(len(g) == 5 and g[2] in CATEGORIAS and g[3] in QUINCENAS for g in d["gastos"]), f
        assert all(len(t) == 3 and t[2] in QUINCENAS for t in d["tarjetas"]), f

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
    print("OK — cálculo, dos maestros, token y .env")


if __name__ == "__main__":
    if "--test" in sys.argv:
        _test()
    else:
        import uvicorn
        uvicorn.run(app, host="127.0.0.1", port=8000)
