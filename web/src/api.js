const KEY = "vidanueva.token";

export const getToken = () => {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
};

export const setToken = (t) => {
  try {
    t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY);
  } catch {
    /* modo privado: la sesión dura lo que dure la pestaña */
  }
};

/** Lanza Error con el mensaje del backend. Un 401 limpia la sesión. */
export async function api(path, { method = "GET", body } = {}) {
  const token = getToken();
  const res = await fetch("/api" + path, {
    method,
    headers: {
      ...(body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: "Bearer " + token } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) {
    setToken(null);
    window.dispatchEvent(new Event("vidanueva:salir"));
  }
  if (!res.ok) {
    const data = await res.json().catch(() => ({}));
    throw new Error(data.detail || "No se pudo completar la acción.");
  }
  return res.json();
}
