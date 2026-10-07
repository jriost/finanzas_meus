import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api.js";

const COLORES = {
  Vivienda: "var(--s1)",
  Tarjetas: "var(--s2)",
  "Crédito": "var(--s3)",
  Servicios: "var(--s4)",
  "Comida y vida": "var(--s5)",
  Salud: "var(--s6)",
  Otros: "var(--s7)",
};
const color = (c) => COLORES[c] || "var(--s7)";

const cop = new Intl.NumberFormat("es-CO", {
  style: "currency",
  currency: "COP",
  maximumFractionDigits: 0,
});
const money = (n) => cop.format(Math.round(n || 0));

const primerDia = (d) => new Date(d.getFullYear(), d.getMonth(), 1);
const clave = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const nombreMes = (d) => d.toLocaleDateString("es-CO", { month: "long", year: "numeric" });

export default function App() {
  const [autenticado, setAutenticado] = useState(() => !!getToken());

  useEffect(() => {
    const salir = () => setAutenticado(false);
    window.addEventListener("vidanueva:salir", salir);
    return () => window.removeEventListener("vidanueva:salir", salir);
  }, []);

  return autenticado ? (
    <Panel onSalir={() => { setToken(null); setAutenticado(false); }} />
  ) : (
    <Entrada onEntrar={() => setAutenticado(true)} />
  );
}

/* ------------------------------------------------------------------ entrada */
function Entrada({ onEntrar }) {
  const [usuario, setUsuario] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [cargando, setCargando] = useState(false);

  async function enviar(e) {
    e.preventDefault();
    setCargando(true);
    setError("");
    try {
      const { token } = await api("/login", { method: "POST", body: { usuario, password } });
      setToken(token);
      onEntrar();
    } catch (err) {
      setError(err.message);
      setPassword("");
    } finally {
      setCargando(false);
    }
  }

  return (
    <div className="gate">
      <form onSubmit={enviar}>
        <div className="brand">Vida <span>Nueva</span></div>
        <p>Tus finanzas desde que te independizaste.</p>
        <input
          id="usuario"
          type="text"
          autoFocus
          autoComplete="username"
          placeholder="Usuario"
          value={usuario}
          onChange={(e) => setUsuario(e.target.value)}
        />
        <input
          id="password"
          type="password"
          autoComplete="current-password"
          placeholder="Contraseña"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
        />
        {error && <p className="err">{error}</p>}
        <button className="btn" disabled={cargando || !usuario || !password}>
          {cargando ? "Entrando…" : "Entrar"}
        </button>
      </form>
    </div>
  );
}

/* ------------------------------------------------------------------- panel */
function Panel({ onSalir }) {
  const [mes, setMes] = useState(() => primerDia(new Date()));
  const [data, setData] = useState(null);
  const [aviso, setAviso] = useState("");

  const cargar = useCallback(async () => {
    try {
      setData(await api(`/state?mes=${clave(mes)}`));
    } catch (err) {
      setAviso(err.message);
    }
  }, [mes]);

  useEffect(() => { cargar(); }, [cargar]);

  useEffect(() => {
    if (!aviso) return;
    const t = setTimeout(() => setAviso(""), 3500);
    return () => clearTimeout(t);
  }, [aviso]);

  // Envuelve cualquier escritura: recarga al terminar y muestra el error si falla.
  const accion = useCallback(
    async (fn) => {
      try {
        await fn();
        await cargar();
      } catch (err) {
        setAviso(err.message);
      }
    },
    [cargar]
  );

  const mover = (n) => setMes(new Date(mes.getFullYear(), mes.getMonth() + n, 1));

  return (
    <>
      <header className="bar">
        <div className="bar-in">
          <div className="brand">Vida <span>Nueva</span></div>
          <div className="month">
            <button onClick={() => mover(-1)} aria-label="Mes anterior">‹</button>
            <b className="num">{nombreMes(mes)}</b>
            <button onClick={() => mover(1)} aria-label="Mes siguiente">›</button>
          </div>
          <button className="btn ghost" onClick={onSalir}>Salir</button>
        </div>
      </header>

      <div className="wrap">
        {!data ? (
          <div className="hero">
            <div className="eyebrow">Cargando</div>
            <h1>Un segundo…</h1>
          </div>
        ) : (
          <>
            <Resumen data={data} />
            <Quincenas data={data} mes={clave(mes)} nombre={nombreMes(mes)} accion={accion} />
            <Deudas data={data} mes={mes} />
            <Maestros data={data} accion={accion} />
            <footer>
              SQLite propia · {data.items.length} gastos fijos · {data.deudas.length} deudas
            </footer>
          </>
        )}
      </div>

      {aviso && <div className="toast" role="status">{aviso}</div>}
    </>
  );
}

/* ----------------------------------------------------------------- resumen */
function Resumen({ data }) {
  const { sueldo, total, libre, por_categoria, deuda_total, cuota_deuda } = data;
  return (
    <div className="hero">
      <div className="eyebrow">Presupuesto quincenal · pesos colombianos</div>
      <h1>
        {libre >= 0
          ? `Te quedan ${money(libre)} este mes`
          : `Este mes te faltan ${money(-libre)}`}
      </h1>
      <p>
        Sueldo, gastos fijos y las dos quincenas. Marca cada pago apenas lo hagas y
        el saldo se ajusta solo.
      </p>

      <div className="tiles">
        <div className="tile">
          <div className="k">{data.bono_mes ? "Ingresos del mes" : "Sueldo del mes"}</div>
          <div className="v num">{money(data.ingreso_mes)}</div>
          <div className="s num">
            {data.bono_mes
              ? `${money(sueldo)} de sueldo + ${money(data.bono_mes)} de bonos`
              : `${money(sueldo / 2)} por quincena`}
          </div>
        </div>
        <div className="tile">
          <div className="k">Deuda total</div>
          <div className="v num">{deuda_total ? money(deuda_total) : "—"}</div>
          <div className="s">
            {!deuda_total
              ? `${money(cuota_deuda)} al mes · registra tus saldos`
              : data.interes_mes
                ? `${money(cuota_deuda)} de cuota · ${money(data.interes_mes)} son interés`
                : `${money(cuota_deuda)} de cuota al mes`}
          </div>
        </div>
        <div className="tile lead">
          <div className="k">Libre para gastar</div>
          <div className={"v num " + (libre >= 0 ? "ok" : "bad")}>{money(libre)}</div>
          <div className="s">
            {libre >= 0 ? `${money(libre / 2)} por quincena` : "Toca recortar algo"}
          </div>
        </div>
      </div>

      <div className="rail">
        <div className="rail-top">
          <h3>En qué se va el mes</h3>
          <div className="meta num">{money(total)} de {money(sueldo)}</div>
        </div>
        <div className="stack">
          {por_categoria.map(({ categoria, monto }) => {
            const pct = total ? (monto / total) * 100 : 0;
            return (
              <div
                key={categoria}
                className="seg"
                style={{ flex: `${pct} 0 0`, background: color(categoria) }}
                title={`${categoria} · ${money(monto)} (${pct.toFixed(0)}%)`}
              >
                {pct >= 12 ? `${pct.toFixed(0)}%` : ""}
              </div>
            );
          })}
        </div>
        <div className="legend">
          {por_categoria.map(({ categoria, monto }) => (
            <div className="lg" key={categoria}>
              <span className="dot" style={{ background: color(categoria) }} />
              <span>{categoria} <b className="num">{money(monto)}</b></span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/* --------------------------------------------------------------- quincenas */
/** El bono de una quincena: un ingreso suelto de ese mes, no un gasto fijo. */
function Bono({ q, mes, accion }) {
  const [abierto, setAbierto] = useState(false);
  const [monto, setMonto] = useState(q.bono?.monto ?? "");
  const [nota, setNota] = useState(q.bono?.nota ?? "");

  const guardar = (e) => {
    e.preventDefault();
    setAbierto(false);
    accion(() =>
      api("/bonos", {
        method: "POST",
        body: { mes, quincena: q.quincena, monto: +monto || 0, nota },
      })
    );
  };

  if (abierto) {
    return (
      <form className="bono edit" onSubmit={guardar}>
        <input
          type="number"
          step="1000"
          min="0"
          autoFocus
          placeholder="Cuánto"
          value={monto}
          onChange={(e) => setMonto(e.target.value)}
        />
        <input
          type="text"
          maxLength={60}
          placeholder="De qué (prima, bono…)"
          value={nota}
          onChange={(e) => setNota(e.target.value)}
        />
        <button className="btn">Guardar</button>
        <button type="button" className="btn ghost" onClick={() => setAbierto(false)}>
          Cancelar
        </button>
      </form>
    );
  }

  if (!q.bono) {
    return (
      <button className="bono add" onClick={() => setAbierto(true)}>
        + Agregar bono
      </button>
    );
  }

  return (
    <button className="bono tiene" onClick={() => setAbierto(true)}>
      <span className="nm">{q.bono.nota || "Bono"}</span>
      <span className="amt num">+ {money(q.bono.monto)}</span>
    </button>
  );
}

function Quincenas({ data, mes, nombre, accion }) {
  const togglear = (f, q) =>
    accion(() =>
      api("/pagos", {
        method: "POST",
        body: { mes, origen: f.origen, ref: f.ref, quincena: q, pagado: !f.pagado },
      })
    );

  return (
    <section>
      <div className="head">
        <h2>Lo que debo pagar</h2>
        <div className="note num">
          {data.pendientes
            ? `${data.pendientes} ${data.pendientes === 1 ? "pago pendiente" : "pagos pendientes"} en ${nombre}`
            : `Todo pagado en ${nombre}`}
        </div>
      </div>
      <div className="cols">
        {data.por_quincena.map((q) => (
          <div className="card" key={q.quincena}>
            <div className="card-h">
              <h3>{q.quincena === "1" ? "Primera quincena" : "Segunda quincena"}</h3>
              <span className="chip num" title={q.bono ? `${money(q.sueldo)} de sueldo + ${money(q.bono.monto)} de bono` : ""}>
                + {money(q.ingreso)}
              </span>
            </div>
            <Bono q={q} mes={mes} accion={accion} key={mes + q.quincena} />
            {q.filas.map((f) => (
              <label className={"row" + (f.pagado ? " done" : "")} key={f.origen + f.ref}>
                <input type="checkbox" checked={f.pagado} onChange={() => togglear(f, q.quincena)} />
                <span className="dot" style={{ background: color(f.categoria) }} />
                <span className="nm">{f.nombre}</span>
                {f.cada_meses > 1 ? <span className="cada">{cadaTexto(f.cada_meses)}</span> : null}
                <span className="amt num">{money(f.parte)}</span>
              </label>
            ))}
            <div className="card-f">
              <span className="lbl">Pagado {money(q.pagado)} de {money(q.debe)}</span>
              <span
                className="big num"
                style={{ color: q.libre >= 0 ? "var(--good)" : "var(--crit)" }}
              >
                {money(q.libre)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- deudas */
const tasaTexto = (t) => `${t.toLocaleString("es-CO", { maximumFractionDigits: 2 })}% E.A.`;
const esTarjeta = (d) => d.tipo === "tarjeta";

/** Qué parte de la próxima cuota se va en intereses, o el aviso si no alcanza. */
function Interes({ plan, tasa }) {
  if (!plan || !tasa) return <div className="pie">Registra la tasa para ver cuánto es interés</div>;
  if (plan.crece) {
    return (
      <div className="pie alerta num">
        La cuota no cubre los {money(plan.interes_mes)} de interés: la deuda crece cada mes
      </div>
    );
  }
  return (
    <div className="pie num">
      De la cuota, <b>{money(plan.interes_mes)}</b> son interés y {money(plan.abono_capital)} bajan la deuda
    </div>
  );
}

const mesLargo = (clave) => {
  const [y, m] = clave.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("es-CO", { month: "long", year: "numeric" });
};
const lista = (nombres) =>
  nombres.length === 1
    ? nombres[0]
    : `${nombres.slice(0, -1).join(", ")} y ${nombres[nombres.length - 1]}`;

/** Cuándo quedas libre de deudas si sigues pagando las cuotas de hoy. */
function Proyeccion({ p }) {
  if (!p || !p.deudas) {
    return (
      <div className="proy libre">
        <div className="k">Sin deudas</div>
        <div className="fecha">No debes nada</div>
      </div>
    );
  }

  const avisos = [];
  if (p.nunca_termina.length)
    avisos.push(
      <p className="alerta" key="nunca">
        {lista(p.nunca_termina)} {p.nunca_termina.length === 1 ? "no entra" : "no entran"} en la
        cuenta: con la cuota de hoy {p.nunca_termina.length === 1 ? "no termina" : "no terminan"}{" "}
        nunca, porque no alcanza ni para los intereses.
      </p>
    );
  if (p.sin_cuota.length)
    avisos.push(
      <p key="sincuota">
        Falta la cuota de {lista(p.sin_cuota)} para poder contar{" "}
        {p.sin_cuota.length === 1 ? "esa deuda" : "esas deudas"}.
      </p>
    );
  if (p.sin_tasa.length)
    avisos.push(
      <p key="sintasa">
        Sin la tasa de {lista(p.sin_tasa)}, la fecha real será más tarde que esta.
      </p>
    );

  return (
    <div className="proy">
      <div className="k">Libre de deudas en</div>
      <div className="fecha">{p.fin ? mesLargo(p.fin) : "—"}</div>
      {p.fin ? (
        <div className="s num">
          {p.cuotas} {p.cuotas === 1 ? "cuota" : "cuotas"} · pagarás {money(p.interes_total)} de
          intereses en el camino
        </div>
      ) : null}
      {p.ultima && p.fin_sin_la_ultima ? (
        <p className="palanca">
          <b>{p.ultima}</b> es la que marca la fecha. Terminando esa primero, quedarías libre en{" "}
          <b>{mesLargo(p.fin_sin_la_ultima)}</b>.
        </p>
      ) : null}
      {avisos}
      <p className="letra-chica">
        Cuenta con que no vuelvas a usar las tarjetas y que las cuotas no cambien.
      </p>
    </div>
  );
}

function Deudas({ data, mes }) {
  const { deudas, deuda_total, cuota_deuda, cupo_total, cupo_disponible } = data;
  // Si sigues pagando la misma cuota, el mes en que la terminas de pagar.
  const ultimaCuota = (n) =>
    n ? nombreMes(new Date(mes.getFullYear(), mes.getMonth() + n - 1, 1)) : "";

  return (
    <section>
      <div className="head">
        <h2>Tarjetas y créditos</h2>
        <div className="note num">
          {deuda_total
            ? `${money(cuota_deuda)} al mes · ${money(deuda_total)} de deuda`
            : `${money(cuota_deuda)} al mes · registra tus saldos en los maestros`}
          {cupo_total ? ` · ${money(cupo_disponible)} de cupo libre` : ""}
        </div>
      </div>
      <Proyeccion p={data.proyeccion} />
      <div className="cards-grid">
        {deudas.map((d) => {
          // La barra de una tarjeta se llena al deber; la de un crédito, al pagar.
          const relleno = esTarjeta(d) ? d.uso : d.avance;
          return (
            <div className="tc" key={d.id}>
              <div className="nm">
                <span className="dot" style={{ background: color(d.categoria) }} />
                <span>{d.nombre}</span>
                {d.tasa ? <span className="tasa num">{tasaTexto(d.tasa)}</span> : null}
              </div>
              <div className="cuota num">
                {money(d.cuota)} <small>cuota al mes</small>
              </div>
              <div className="deuda">
                <span className="num">
                  {d.saldo
                    ? esTarjeta(d)
                      ? `Debes ${money(d.saldo)}`
                      : `Te faltan ${money(d.saldo)}`
                    : "Saldo sin registrar"}
                </span>
                <span className="num">
                  {d.plan && d.plan.cuotas
                    ? `${d.plan.cuotas} ${d.plan.cuotas === 1 ? "cuota" : "cuotas"}`
                    : d.dia_pago
                      ? `paga el ${d.dia_pago}`
                      : d.quincena === "ambas"
                        ? "las dos quincenas"
                        : `${d.quincena}ª quincena`}
                </span>
              </div>
              <div className="bar-s meta">
                <i style={{ width: `${(relleno ?? 0) * 100}%`, background: color(d.categoria) }} />
              </div>
              <div className="pie num">
                {esTarjeta(d) ? (
                  d.tope ? (
                    `${money(d.disponible)} libres de ${money(d.tope)}`
                  ) : (
                    "Cupo sin registrar"
                  )
                ) : d.tope ? (
                  <>
                    Llevas <b>{(d.avance * 100).toFixed(0)}%</b> — {money(d.pagado)} de {money(d.tope)}
                    {d.plan && d.plan.cuotas ? ` · terminas en ${ultimaCuota(d.plan.cuotas)}` : ""}
                  </>
                ) : (
                  "Registra con cuánto empezó para ver el avance"
                )}
              </div>
              <Interes plan={d.plan} tasa={d.tasa} />
            </div>
          );
        })}
      </div>
    </section>
  );
}

/* --------------------------------------------------------------- maestros */
const GASTO_NUEVO = { nombre: "Nuevo gasto", monto: 0, categoria: "Otros", quincena: "1", cada_meses: 1, desde: "" };
const CADA = [
  [1, "Todos los meses"],
  [2, "Cada 2 meses (4 quincenas)"],
  [3, "Cada 3 meses"],
  [6, "Cada 6 meses"],
  [12, "Una vez al año"],
];
const cadaTexto = (n) => (CADA.find(([v]) => v === n) || [0, `Cada ${n} meses`])[1];
const DEUDA_NUEVA = {
  nombre: "Nueva tarjeta", tipo: "tarjeta", tope: 0, saldo: 0,
  cuota: 0, dia_pago: 0, tasa: 0, quincena: "1",
};
const CAMPOS_GASTO = ["nombre", "monto", "categoria", "quincena", "cada_meses", "desde"];
const CAMPOS_DEUDA = ["nombre", "tipo", "tope", "saldo", "cuota", "dia_pago", "tasa", "quincena"];

function SelectQuincena({ value, onChange }) {
  return (
    <select value={value} onChange={onChange}>
      <option value="1">1ª quincena</option>
      <option value="2">2ª quincena</option>
      <option value="ambas">Las dos</option>
    </select>
  );
}

function Maestros({ data, accion }) {
  const guardar = (ruta, fila, campos, cambio) =>
    accion(() =>
      api(`${ruta}/${fila.id}`, {
        method: "PUT",
        body: { ...Object.fromEntries(campos.map((k) => [k, fila[k]])), ...cambio },
      })
    );
  const guardarDeuda = (d, cambio) => guardar("/deudas", d, CAMPOS_DEUDA, cambio);
  const guardarGasto = (g, cambio) => guardar("/items", g, CAMPOS_GASTO, cambio);

  return (
    <section>
      <details className="ed">
        <summary>Maestros: gastos fijos, deudas y sueldo</summary>
        <div className="ed-body">
          <div className="sueldo-box">
            <label htmlFor="sueldo">Sueldo mensual</label>
            <input
              id="sueldo"
              type="number"
              step="1000"
              min="0"
              defaultValue={data.sueldo}
              onBlur={(e) =>
                accion(() => api("/sueldo", { method: "PUT", body: { sueldo: +e.target.value || 0 } }))
              }
            />
            <span className="chip gray num">{money(data.sueldo / 2)} por quincena</span>
          </div>

          <h3 className="ed-h">Tarjetas y créditos</h3>
          <div className="scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ minWidth: 150 }}>Nombre</th>
                  <th style={{ minWidth: 110 }}>Tipo</th>
                  <th style={{ minWidth: 130 }}>Cupo o valor inicial</th>
                  <th style={{ minWidth: 115 }}>Saldo que debes</th>
                  <th style={{ minWidth: 115 }}>Cuota mensual</th>
                  <th style={{ minWidth: 80 }}>Día de pago</th>
                  <th style={{ minWidth: 100 }}>Tasa % E.A.</th>
                  <th style={{ minWidth: 120 }}>Se paga</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.deudas.map((d) => (
                  <tr key={d.id}>
                    <td>
                      <input
                        type="text"
                        defaultValue={d.nombre}
                        onBlur={(e) => guardarDeuda(d, { nombre: e.target.value.trim() || d.nombre })}
                      />
                    </td>
                    <td>
                      <select value={d.tipo} onChange={(e) => guardarDeuda(d, { tipo: e.target.value })}>
                        <option value="tarjeta">Tarjeta</option>
                        <option value="credito">Crédito</option>
                      </select>
                    </td>
                    {["tope", "saldo", "cuota"].map((campo) => (
                      <td key={campo}>
                        <input
                          type="number"
                          step="1000"
                          min="0"
                          placeholder="0"
                          defaultValue={d[campo] || ""}
                          onBlur={(e) => guardarDeuda(d, { [campo]: +e.target.value || 0 })}
                        />
                      </td>
                    ))}
                    <td>
                      <input
                        type="number"
                        min="0"
                        max="31"
                        placeholder="—"
                        defaultValue={d.dia_pago || ""}
                        onBlur={(e) => guardarDeuda(d, { dia_pago: +e.target.value || 0 })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        max="500"
                        placeholder="—"
                        defaultValue={d.tasa || ""}
                        onBlur={(e) => guardarDeuda(d, { tasa: +e.target.value || 0 })}
                      />
                    </td>
                    <td>
                      <SelectQuincena
                        value={d.quincena}
                        onChange={(e) => guardarDeuda(d, { quincena: e.target.value })}
                      />
                    </td>
                    <td>
                      <button
                        className="btn x"
                        title={`Eliminar ${d.nombre}`}
                        onClick={() => accion(() => api(`/deudas/${d.id}`, { method: "DELETE" }))}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ed-row">
            <button
              className="btn"
              onClick={() => accion(() => api("/deudas", { method: "POST", body: DEUDA_NUEVA }))}
            >
              Agregar tarjeta o crédito
            </button>
          </div>

          <h3 className="ed-h">Gastos fijos</h3>
          <div className="scroll">
            <table className="tbl">
              <thead>
                <tr>
                  <th style={{ minWidth: 170 }}>Concepto</th>
                  <th style={{ minWidth: 120 }}>Mensual</th>
                  <th style={{ minWidth: 150 }}>Categoría</th>
                  <th style={{ minWidth: 130 }}>Se paga</th>
                  <th style={{ minWidth: 180 }}>Cada cuánto</th>
                  <th style={{ minWidth: 130 }}>Primer cobro</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {data.items.map((it) => (
                  <tr key={it.id}>
                    <td>
                      <input
                        type="text"
                        defaultValue={it.nombre}
                        onBlur={(e) => guardarGasto(it, { nombre: e.target.value.trim() || it.nombre })}
                      />
                    </td>
                    <td>
                      <input
                        type="number"
                        step="1000"
                        min="0"
                        defaultValue={it.monto}
                        onBlur={(e) => guardarGasto(it, { monto: +e.target.value || 0 })}
                      />
                    </td>
                    <td>
                      <select
                        value={it.categoria}
                        onChange={(e) => guardarGasto(it, { categoria: e.target.value })}
                      >
                        {data.categorias.map((c) => (
                          <option key={c}>{c}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <SelectQuincena
                        value={it.quincena}
                        onChange={(e) => guardarGasto(it, { quincena: e.target.value })}
                      />
                    </td>
                    <td>
                      <select
                        value={it.cada_meses}
                        onChange={(e) => {
                          const cada = +e.target.value;
                          // Si deja de ser mensual hay que anclarlo: arranca en el mes en pantalla.
                          guardarGasto(it, {
                            cada_meses: cada,
                            desde: cada > 1 ? it.desde || data.mes : "",
                          });
                        }}
                      >
                        {CADA.map(([v, t]) => (
                          <option key={v} value={v}>{t}</option>
                        ))}
                      </select>
                    </td>
                    <td>
                      {it.cada_meses > 1 ? (
                        <input
                          type="month"
                          defaultValue={it.desde}
                          onBlur={(e) => guardarGasto(it, { desde: e.target.value })}
                        />
                      ) : (
                        <span className="vacio">—</span>
                      )}
                    </td>
                    <td>
                      <button
                        className="btn x"
                        title={`Eliminar ${it.nombre}`}
                        onClick={() => accion(() => api(`/items/${it.id}`, { method: "DELETE" }))}
                      >
                        ×
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="ed-row">
            <button
              className="btn"
              onClick={() => accion(() => api("/items", { method: "POST", body: GASTO_NUEVO }))}
            >
              Agregar gasto
            </button>
          </div>
        </div>
      </details>
    </section>
  );
}
