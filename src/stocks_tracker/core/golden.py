"""El calculo financiero de referencia: mismos datos dentro, mismos numeros fuera.

QUE PROBLEMA RESUELVE

Los tests normales comprueban propiedades: que el RSI este entre 0 y 100, que un
stop no baje, que un dato imposible se descarte. Todo eso puede seguir siendo
cierto DESPUES de que un cambio haya movido el score de un valor de 82,4 a 79,1.
Nada da error. Nada se pone rojo. El numero simplemente es otro, y el dashboard
lo ensena con la misma cara de siempre.

Esto es lo que cierra ese agujero: un juego de precios y fundamentales FIJOS
—`tests/fixtures/*.csv`, que no los genera nadie en tiempo de test— y los
resultados que producen hoy, congelados. Si manana un cambio los mueve, salta.

QUE SIGNIFICA QUE SALTE

No significa "has metido un fallo". Significa "has cambiado un numero
financiero, di cual y por que". Muchas veces sera correcto: se arregla una
formula y los resultados cambian, faltaria mas. Lo que no puede pasar es que
cambien sin que nadie se entere.

Actualizar la referencia es un acto deliberado y se hace con `make oro`, que
reescribe el fichero y deja el cambio en el diff para que se revise igual que
cualquier otro codigo.

POR QUE SE COMPARA CON TOLERANCIA Y NO CON IGUALDAD EXACTA

Porque el CI corre en otra maquina con otra version de numpy, y las operaciones
en coma flotante pueden diferir en el ultimo bit. Una tolerancia relativa de
1e-9 esta ocho ordenes de magnitud por debajo de cualquier cambio real de
formula —cambiar una media de 20 a 21 sesiones mueve los numeros en la primera
cifra significativa— asi que no tapa nada y evita rojos que no son de nadie.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from . import indicators as ind_mod
from . import signals as sig_mod
from .config import get_factor_config, project_root
from .scoring import compute_scores, weights_hash

# Diferencia relativa a partir de la cual dos numeros son "otro numero".
TOLERANCIA = 1e-9

# Cifras que se guardan en el fichero de referencia. No es la tolerancia de la
# comparacion: es cuanto se escribe, para que el JSON sea legible en un diff.
DECIMALES = 8


def carpeta() -> Path:
    return project_root() / "tests" / "fixtures"


def cargar_precios() -> pd.DataFrame:
    """Los precios de referencia. Un CSV commiteado, no un generador.

    Es CSV y no un generador con semilla a proposito: un generador produce
    numeros distintos si cambia la version de numpy, y entonces la referencia
    se mueve sola sin que nadie haya tocado nada del calculo. El fichero es la
    verdad.
    """
    datos = pd.read_csv(carpeta() / "precios_oro.csv")
    datos["date"] = pd.to_datetime(datos["date"])
    return datos


def cargar_fundamentales() -> pd.DataFrame:
    return pd.read_csv(carpeta() / "fundamentales_oro.csv")


# ---------------------------------------------------------------------------
# El calculo, exactamente el de produccion
# ---------------------------------------------------------------------------
def calcular() -> dict[str, Any]:
    """Recorre el pipeline real y devuelve el resumen que se congela.

    Se llama a `ind_mod.compute_all`, `sig_mod.detect` y `compute_scores` —las
    mismas funciones que usa `run_compute`— y no a copias simplificadas. Una
    referencia calculada con codigo paralelo congela ese codigo paralelo, no el
    programa.
    """
    precios = cargar_precios()
    indicadores, senales = [], []

    for ticker, grupo in precios.groupby("ticker", sort=True):
        serie = grupo.set_index("date").sort_index()
        ind = ind_mod.compute_all(serie)
        detectadas = sig_mod.detect(ind)
        if not detectadas.empty:
            detectadas = detectadas.copy()
            detectadas.insert(0, "ticker", ticker)
            senales.append(detectadas)
        ind = ind.reset_index().rename(columns={"index": "date"})
        ind.insert(0, "ticker", ticker)
        indicadores.append(ind)

    todos = pd.concat(indicadores, ignore_index=True)
    disparadas = (pd.concat(senales, ignore_index=True) if senales
                  else pd.DataFrame(columns=["ticker", "date", "signal_id"]))

    return {
        "indicadores": _ultima_fila_por_ticker(todos),
        "senales": _resumen_de_senales(disparadas),
        "scores": _scores(todos, disparadas),
    }


def _ultima_fila_por_ticker(todos: pd.DataFrame) -> dict:
    """La ultima sesion de cada valor, con todos sus indicadores.

    La ultima y no una del medio porque es la que arrastra mas historia: un
    cambio en cualquier ventana movil acaba notandose ahi.
    """
    ultimas = todos.sort_values("date").groupby("ticker", sort=True).tail(1)
    salida = {}
    for fila in ultimas.itertuples(index=False):
        datos = fila._asdict()
        ticker = str(datos.pop("ticker"))
        datos.pop("date", None)
        salida[ticker] = {k: _numero(v) for k, v in sorted(datos.items())}
    return salida


def _resumen_de_senales(disparadas: pd.DataFrame) -> dict:
    """Cuantas veces disparo cada senal en cada valor, y cuando fue la ultima.

    No se guarda la lista entera de disparos: son miles de filas y un diff
    ilegible es un diff que nadie revisa. El conteo mas la fecha del ultimo
    detecta cualquier cambio en la deteccion.
    """
    if disparadas.empty:
        return {}
    salida: dict[str, dict] = {}
    for (ticker, signal_id), grupo in disparadas.groupby(["ticker", "signal_id"],
                                                        sort=True):
        salida.setdefault(str(ticker), {})[str(signal_id)] = {
            "veces": int(len(grupo)),
            "ultima": str(pd.to_datetime(grupo["date"]).max().date()),
        }
    return salida


def _scores(todos: pd.DataFrame, disparadas: pd.DataFrame) -> dict:
    """El ranking del ultimo dia, con todos los perfiles de pesos.

    Todos los perfiles y no solo el equilibrado: cada uno pondera distinto y un
    cambio puede mover uno sin tocar los demas.
    """
    ultima_fecha = todos["date"].max()
    foto = todos[todos["date"] == ultima_fecha].copy()
    foto = foto.merge(cargar_fundamentales(), on="ticker", how="left")

    activas = {}
    if not disparadas.empty:
        del_dia = disparadas[pd.to_datetime(disparadas["date"]) == ultima_fecha]
        activas = del_dia.groupby("ticker")["signal_id"].apply(list).to_dict()
    foto["technical_raw"] = foto.apply(
        lambda r: sig_mod.technical_score(r, activas.get(r["ticker"], [])), axis=1
    )

    cfg = get_factor_config()
    salida: dict[str, dict] = {}
    for perfil in sorted(cfg.presets):
        pesos = cfg.weights(perfil)
        scores, _ = compute_scores(foto, pesos, cfg, group_col="gics_sector")
        if scores.empty:
            continue
        salida[perfil] = {
            "hash_pesos": weights_hash(pesos),
            "valores": {
                str(f.ticker): {
                    k: _numero(v) for k, v in sorted(f._asdict().items())
                    if k not in ("Index", "ticker")
                }
                for f in scores.itertuples()
            },
        }
    return salida


def _numero(valor: Any) -> Any:
    """Deja el valor en algo que un JSON pueda guardar y un diff leer."""
    if valor is None:
        return None
    if isinstance(valor, (bool, np.bool_)):
        return bool(valor)
    if isinstance(valor, (int, np.integer)):
        return int(valor)
    if isinstance(valor, (float, np.floating)):
        f = float(valor)
        # NaN no sobrevive a JSON de forma portable y ademas es informacion: que
        # un indicador no exista es un hecho que hay que congelar igual que su
        # valor. Se escribe como null.
        return None if not np.isfinite(f) else round(f, DECIMALES)
    return str(valor)


# ---------------------------------------------------------------------------
# Guardar y comparar
# ---------------------------------------------------------------------------
def ruta_referencia() -> Path:
    return carpeta() / "esperado.json"


def guardar(resultado: dict[str, Any]) -> Path:
    destino = ruta_referencia()
    destino.write_text(
        json.dumps(resultado, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destino


def cargar_referencia() -> dict[str, Any]:
    return json.loads(ruta_referencia().read_text("utf-8"))


def diferencias(esperado: Any, obtenido: Any, ruta: str = "") -> list[str]:
    """Todas las diferencias, no la primera.

    La primera no basta: si un cambio ha movido treinta numeros, ver uno solo
    lleva a arreglar ese y volver a ejecutar treinta veces. Y ademas la forma
    del cambio —todos los de un perfil, o todos los de un ticker— es justo lo
    que dice si fue intencionado.
    """
    fuera: list[str] = []

    if isinstance(esperado, dict) and isinstance(obtenido, dict):
        for clave in sorted(set(esperado) | set(obtenido)):
            sub = f"{ruta}.{clave}" if ruta else str(clave)
            if clave not in obtenido:
                fuera.append(f"{sub}: ha desaparecido (valia {esperado[clave]!r})")
            elif clave not in esperado:
                fuera.append(f"{sub}: es nuevo (vale {obtenido[clave]!r})")
            else:
                fuera.extend(diferencias(esperado[clave], obtenido[clave], sub))
        return fuera

    if isinstance(esperado, float) and isinstance(obtenido, float):
        escala = max(abs(esperado), abs(obtenido))
        if escala > 0 and abs(esperado - obtenido) / escala > TOLERANCIA:
            fuera.append(f"{ruta}: {esperado!r} -> {obtenido!r}")
        return fuera

    if esperado != obtenido:
        fuera.append(f"{ruta}: {esperado!r} -> {obtenido!r}")
    return fuera
