"""Dos ordenadores, el mismo dia, oportunidades distintas.

EL FALLO, REPORTADO DESDE EL USO REAL

El usuario instalo el programa en varios ordenadores y las Oportunidades salian
distintas en cada uno. No era una impresion: es real, y no es un fallo de
calculo sino una propiedad del calculo que nadie estaba diciendo.

QUE PASA

Todo el scoring es TRANSVERSAL. El z-score de un valor sale de la mediana y la
MAD de los DEMAS valores presentes; el percentil es su puesto entre los
presentes; la winsorizacion usa los cuantiles de los presentes. Es decir:

    el ranking no es una propiedad del valor,
    es una propiedad del valor Y DE CON QUIEN SE LE COMPARA.

Eso esta bien —es lo que significa "el mejor del mercado"— pero convierte al
universo en una entrada del calculo tan importante como los precios.

Y el universo SI cambia entre maquinas. `resolve_universe` baja los
constituyentes de Wikipedia y, si falla, se cae a la lista manual de
`universe.yaml`:

    SP500     wikipedia ~503 valores    respaldo manual  30
    NASDAQ100 wikipedia ~100 valores    respaldo manual  20

Con Wikipedia funcionando el universo ronda los 620 valores; con la descarga
caida, unos 240. Un cambio de cabecera en una tabla de Wikipedia, un proxy, un
timeout o `lxml` sin instalar bastan para que un ordenador puntue contra 620 y
el de al lado contra 240.

MEDIDO, no supuesto (620 valores frente a los mismos menos 380):

    Top-20 que cada maquina ENSENA, en comun ......... 7 de 20
    Top-20 entre los valores comunes ................ 19 de 20
    Cambio de puesto entre los comunes .............. mediana 8, maximo 48
    Salto de percentil .............................. hasta 0,21

Trece oportunidades distintas de veinte. Sin un error, sin un aviso, y con las
dos pantallas igual de convincentes.

LO QUE SE ARREGLA Y LO QUE NO

No se puede hacer que dos universos distintos den el mismo ranking: seria pedir
que "el mejor de 620" y "el mejor de 240" fueran el mismo valor. Lo que se
arregla es que la dependencia deje de ser invisible:

- Cada calculo registra CONTRA QUE se puntuo (`scoring_runs`): cuantos valores,
  que huella tiene el conjunto exacto y cuantos sectores.
- La pantalla de Oportunidades lo ensena. Comparar dos ordenadores pasa a ser
  comparar ocho caracteres.
- Si el universo encoge mas de un 5 % de una noche para otra, se avisa en rojo:
  es la firma de una descarga de constituyentes caida.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from stocks_tracker.core.config import get_factor_config
from stocks_tracker.core.scoring import compute_scores, huella_universo

SECTORES = ["Tech", "Salud", "Banca", "Energia", "Consumo", "Industria",
            "Utilities", "Materiales"]


def _universo(n: int, semilla: int = 42) -> pd.DataFrame:
    """Un universo sintetico con todos los campos que mira el scoring.

    Es SIMULADO: aqui no hay salida a internet. Sirve para medir como responde
    el ranking a que falten valores, que es geometria del calculo y no depende
    de que los numeros sean precios de verdad. No sirve para afirmar nada sobre
    el ranking real.
    """
    rng = np.random.default_rng(semilla)
    cfg = get_factor_config()
    campos = sorted({s.field for spec in cfg.factors.values()
                     for s in spec.submetrics})
    df = pd.DataFrame({
        "ticker": [f"T{i:03d}" for i in range(n)],
        "gics_sector": rng.choice(SECTORES, n),
    })
    for campo in campos:
        df[campo] = rng.normal(0, 1, n) * 10 + 50
    return df


def _pesos() -> dict[str, float]:
    return {k: 1.0 for k in get_factor_config().factors}


# ---------------------------------------------------------------------------
# Lo que SI es determinista, comprobado antes de acusar a nadie
# ---------------------------------------------------------------------------
def test_el_calculo_da_lo_mismo_dos_veces():
    """Antes de culpar al universo hay que descartar que el calculo tenga algo
    aleatorio dentro. No lo tiene."""
    df = _universo(300)

    a, _ = compute_scores(df, _pesos())
    b, _ = compute_scores(df, _pesos())

    assert a["composite"].equals(b["composite"])


def test_el_orden_de_las_filas_no_cambia_el_resultado():
    """Un ranking que dependiera del orden de llegada de las filas seria un
    fallo de verdad: la ingesta no garantiza ningun orden."""
    df = _universo(300)
    barajado = df.sample(frac=1.0, random_state=7).reset_index(drop=True)

    a = compute_scores(df, _pesos())[0].set_index("ticker")["composite"]
    b = compute_scores(barajado, _pesos())[0].set_index("ticker")["composite"]

    assert np.allclose(a, b.loc[a.index], rtol=0, atol=1e-12)


# ---------------------------------------------------------------------------
# Lo que NO lo es, y es la causa
# ---------------------------------------------------------------------------
def test_quitar_valores_cambia_el_ranking_de_los_que_quedan():
    """LA CAUSA, medida. Un valor que no se ha tocado cambia de puesto solo
    porque han desaparecido otros: su z-score sale de la mediana y la MAD de los
    presentes."""
    df = _universo(620, semilla=11)
    completo = compute_scores(df, _pesos())[0].set_index("ticker")

    reducido = df.sample(240, random_state=3).reset_index(drop=True)
    parcial = compute_scores(reducido, _pesos())[0].set_index("ticker")

    comunes = completo.index.intersection(parcial.index)
    puestos_a = completo.loc[comunes, "composite"].rank(ascending=False)
    puestos_b = parcial.loc[comunes, "composite"].rank(ascending=False)
    movimiento = (puestos_a - puestos_b).abs()

    assert movimiento.max() > 10, (
        "si esto deja de cumplirse, el scoring ha dejado de ser transversal y "
        "toda la explicacion de este fichero hay que rehacerla"
    )


def test_el_top20_que_ve_cada_maquina_es_distinto():
    """EL SINTOMA EXACTO QUE REPORTO EL USUARIO, reproducido."""
    df = _universo(620, semilla=11)
    completo = compute_scores(df, _pesos())[0].set_index("ticker")
    reducido = df.sample(240, random_state=3).reset_index(drop=True)
    parcial = compute_scores(reducido, _pesos())[0].set_index("ticker")

    top_a = set(completo["composite"].sort_values(ascending=False).index[:20])
    top_b = set(parcial["composite"].sort_values(ascending=False).index[:20])

    assert len(top_a & top_b) < 15, (
        "el sintoma reportado no se reproduce; revisar la explicacion"
    )


# ---------------------------------------------------------------------------
# La huella: lo que permite comparar dos instalaciones
# ---------------------------------------------------------------------------
def test_el_mismo_universo_da_la_misma_huella():
    assert huella_universo(["AAPL", "MSFT"]) == huella_universo(["AAPL", "MSFT"])


def test_el_orden_no_cambia_la_huella():
    """Dos maquinas no descargan en el mismo orden. Si el orden contara, la
    huella diria "sois distintos" siempre y no serviria para nada."""
    assert huella_universo(["MSFT", "AAPL"]) == huella_universo(["AAPL", "MSFT"])


def test_un_solo_valor_de_diferencia_cambia_la_huella():
    """Es lo unico que tiene que hacer: si difieren, saberlo."""
    assert huella_universo(["AAPL", "MSFT"]) != huella_universo(["AAPL"])


def test_los_duplicados_y_los_espacios_no_cuentan():
    """El mismo universo escrito de dos formas es el mismo universo."""
    assert huella_universo([" aapl ", "AAPL", "MSFT"]) == huella_universo(
        ["AAPL", "MSFT"])


def test_la_huella_es_corta_para_poder_compararla_a_ojo():
    """Si no cabe en una linea de pantalla, nadie la va a comparar."""
    assert len(huella_universo(["AAPL"])) == 8


def test_un_universo_vacio_no_revienta():
    assert huella_universo([]) == huella_universo([])


# ---------------------------------------------------------------------------
# Que quede registrado y se avise
# ---------------------------------------------------------------------------
@pytest.fixture
def almacen(tmp_path, monkeypatch):
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    import streamlit as st

    st.cache_data.clear()
    return Stub


def test_se_registra_contra_que_se_puntuo(almacen):
    from datetime import date

    from stocks_tracker.compute.run_compute import _registrar_universo
    from stocks_tracker.core import db

    tickers = ["AAA", "BBB", "CCC"]
    with db.connect() as conn:
        _registrar_universo(conn, date(2026, 8, 20), "w1", tickers,
                            pd.Series(["Tech", "Tech", "Banca"]))
        fila = conn.execute(
            "SELECT n_tickers, universe_hash, n_sectores FROM scoring_runs"
        ).fetchone()

    assert fila[0] == 3
    assert fila[1] == huella_universo(tickers)
    assert fila[2] == 2


def test_se_avisa_cuando_el_universo_encoge(almacen, capsys):
    """El aviso que faltaba. Una descarga de constituyentes caida pasa el
    universo de 620 a 240 sin un solo error a la vista."""
    from datetime import date

    from stocks_tracker.compute.run_compute import _registrar_universo
    from stocks_tracker.core import db

    sectores = pd.Series(["Tech"])
    with db.connect() as conn:
        _registrar_universo(conn, date(2026, 8, 19), "w1",
                            [f"T{i}" for i in range(620)], sectores)
        capsys.readouterr()
        _registrar_universo(conn, date(2026, 8, 20), "w1",
                            [f"T{i}" for i in range(240)], sectores)

    salida = capsys.readouterr().out
    assert "encogido" in salida
    assert "620" in salida and "240" in salida


def test_una_variacion_normal_no_da_la_alarma(almacen, capsys):
    """Un puñado de valores que fallan una noche es normal. Un aviso que salta
    siempre deja de leerse, y entonces el que importa tampoco se lee."""
    from datetime import date

    from stocks_tracker.compute.run_compute import _registrar_universo
    from stocks_tracker.core import db

    sectores = pd.Series(["Tech"])
    with db.connect() as conn:
        _registrar_universo(conn, date(2026, 8, 19), "w1",
                            [f"T{i}" for i in range(620)], sectores)
        capsys.readouterr()
        _registrar_universo(conn, date(2026, 8, 20), "w1",
                            [f"T{i}" for i in range(600)], sectores)

    assert "encogido" not in capsys.readouterr().out


def test_crecer_no_da_la_alarma(almacen, capsys):
    """Recuperar valores es una buena noticia, no un problema."""
    from datetime import date

    from stocks_tracker.compute.run_compute import _registrar_universo
    from stocks_tracker.core import db

    sectores = pd.Series(["Tech"])
    with db.connect() as conn:
        _registrar_universo(conn, date(2026, 8, 19), "w1",
                            [f"T{i}" for i in range(240)], sectores)
        capsys.readouterr()
        _registrar_universo(conn, date(2026, 8, 20), "w1",
                            [f"T{i}" for i in range(620)], sectores)

    assert "encogido" not in capsys.readouterr().out


def test_el_primer_calculo_no_avisa_de_nada(almacen, capsys):
    """Sin calculo anterior no hay con que comparar."""
    from datetime import date

    from stocks_tracker.compute.run_compute import _registrar_universo
    from stocks_tracker.core import db

    with db.connect() as conn:
        _registrar_universo(conn, date(2026, 8, 20), "w1", ["AAA"],
                            pd.Series(["Tech"]))

    assert "encogido" not in capsys.readouterr().out


def test_la_pantalla_puede_leer_contra_que_se_puntuo(almacen):
    """De nada sirve registrarlo si la pantalla no lo ensena."""
    from datetime import date

    import streamlit as st

    from stocks_tracker.app import data_access as da
    from stocks_tracker.core import db
    from stocks_tracker.core.scoring import preset_hash

    with db.connect() as conn:
        _registrar = __import__(
            "stocks_tracker.compute.run_compute", fromlist=["_registrar_universo"]
        )._registrar_universo
        _registrar(conn, date(2026, 8, 20), preset_hash("balanced"),
                   ["AAA", "BBB"], pd.Series(["Tech", "Banca"]))
    st.cache_data.clear()

    info = da.scoring_universe(None)

    assert info["n_tickers"] == 2
    assert info["huella"] == huella_universo(["AAA", "BBB"])


def test_sin_registro_la_pantalla_no_inventa_nada(almacen):
    """Un almacen calculado antes de que esto existiera no puede ensenar un
    numero de valores inventado."""
    import streamlit as st

    from stocks_tracker.app import data_access as da

    st.cache_data.clear()

    assert da.scoring_universe(None) == {}


def test_el_comando_de_diagnostico_ensena_la_huella(almacen, capsys):
    """El comando que resuelve la duda del usuario: dos ordenadores, dos
    huellas, y en un segundo se sabe si el problema es el universo o no."""
    from datetime import date

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db
    from stocks_tracker.core.scoring import preset_hash

    with db.connect() as conn:
        rc._registrar_universo(conn, date(2026, 8, 20), preset_hash("balanced"),
                               ["AAA", "BBB"], pd.Series(["Tech", "Banca"]))

    assert rc._informe_del_universo(None) == 0
    salida = capsys.readouterr().out
    assert huella_universo(["AAA", "BBB"]) in salida
    assert "2" in salida


def test_sin_registro_el_comando_lo_dice_en_vez_de_callarse(almacen, capsys):
    """Un almacen viejo no tiene el registro. Salir en silencio con codigo 0
    haria pensar que las dos instalaciones son iguales."""
    from stocks_tracker.compute import run_compute as rc

    assert rc._informe_del_universo(None) == 1
    assert "no tiene registrado" in capsys.readouterr().out


def test_el_hueco_entre_lo_guardado_y_lo_puntuado_se_ve(almacen, capsys):
    """LA PREGUNTA QUE LLEGO DEL USO REAL: "¿es este el ordenador bueno?"

    Venia de mirar un "632 de 633 instrumentos" en pantalla, que es la
    cobertura de simbolos de TradingView y no dice nada del ranking. El numero
    que si importa —cuantos valores se PUNTUARON— salia solo:

        valores         : 582

    Sin nada al lado, 582 parece completo. Con los dos delante se ve que hay 51
    valores descargados que no entraron en el ranking, y eso es un sintoma que
    hay que mirar: cada valor se puntua comparandolo con los demas, asi que el
    universo decide el orden tanto como los precios.
    """
    from datetime import date

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db
    from stocks_tracker.core.scoring import preset_hash

    with db.connect() as conn:
        for t in ("AAA", "BBB", "CCC", "DDD"):
            conn.execute("INSERT INTO instruments (ticker, name, asset_class) "
                         "VALUES (?, ?, 'equity')", [t, t])
        # Una cripto NO cuenta como "guardado" a estos efectos: el ranking es
        # de acciones y ETF, y meterla en el denominador inventaria un hueco
        # que no existe. Es el mismo filtro que usa `current_session`.
        conn.execute("INSERT INTO instruments (ticker, name, asset_class) "
                     "VALUES ('BTC-EUR', 'Bitcoin', 'crypto')")
        rc._registrar_universo(conn, date(2026, 8, 20), preset_hash("balanced"),
                               ["AAA", "BBB"], pd.Series(["Tech", "Banca"]))

    assert rc._informe_del_universo(None) == 0
    salida = capsys.readouterr().out

    assert "2 puntuados de 4 guardados" in salida
    assert "2 descargados" in salida, (
        "el hueco tiene que decirse, no dejarse a que el usuario reste"
    )


def test_sin_hueco_no_se_avisa_de_nada(almacen, capsys):
    """El contrapeso. Un aviso que sale siempre deja de leerse, y si los
    puntuados son todos los guardados no hay nada que mirar."""
    from datetime import date

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db
    from stocks_tracker.core.scoring import preset_hash

    with db.connect() as conn:
        for t in ("AAA", "BBB"):
            conn.execute("INSERT INTO instruments (ticker, name, asset_class) "
                         "VALUES (?, ?, 'equity')", [t, t])
        rc._registrar_universo(conn, date(2026, 8, 20), preset_hash("balanced"),
                               ["AAA", "BBB"], pd.Series(["Tech", "Banca"]))

    assert rc._informe_del_universo(None) == 0
    assert "no entraron en el ranking" not in capsys.readouterr().out


def test_una_version_sin_registrar_dice_como_arreglarse(almacen, capsys):
    """`version codigo: sin-git` en los DOS ordenadores.

    La version se guarda al calcular, asi que un almacen calculado con el
    codigo anterior —que no sabia leer `.version` en una instalacion sin git—
    conserva su "sin-git" por mucho que se actualice el programa. Sin este
    aviso, el usuario compara dos lineas identicas y concluye que las dos
    maquinas corren lo mismo, que es justo lo que no sabe.
    """
    from datetime import date

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db, lineage
    from stocks_tracker.core.scoring import preset_hash

    with db.connect() as conn:
        rc._registrar_universo(conn, date(2026, 8, 20), preset_hash("balanced"),
                               ["AAA", "BBB"], pd.Series(["Tech", "Banca"]))
        conn.execute("UPDATE scoring_runs SET git_commit = ?", [lineage.SIN_GIT])

    assert rc._informe_del_universo(None) == 0
    salida = capsys.readouterr().out
    assert "no quedo registrada" in salida
    assert "daily" in salida, "tiene que decir QUE hacer, no solo que falta"


def test_la_lista_dice_QUE_valores_se_puntuaron(almacen, tmp_path, capsys):
    """El paso siguiente a la huella, y el unico que se puede accionar.

    `--universo` dice SI dos ordenadores puntuaron lo mismo. Con eso ya se sabe
    de quien es la culpa de que las oportunidades no coincidan, pero no que
    hacer: "b175e2e9 contra c3fea71c" no se lee, y no hay nada que corregir en
    dos hashes.

    Con la lista de los dos, un `fc` da los valores exactos que le faltan a
    uno, y ahi si se ve el patron —un indice entero, un mercado, un scrapeo de
    constituyentes que fallo— que es lo que se arregla.
    """
    from datetime import date

    from stocks_tracker.compute import run_compute as rc
    from stocks_tracker.core import db
    from stocks_tracker.core.scoring import preset_hash

    whash = preset_hash("balanced")
    with db.connect() as conn:
        for t in ("CCC", "AAA", "BBB"):
            conn.execute(
                "INSERT INTO factor_scores (ticker, date, weights_hash, "
                "composite) VALUES (?, DATE '2026-08-20', ?, 1.0)", [t, whash])
        # Un ranking VIEJO con un valor que ya no esta en el universo. Si la
        # consulta no se cine a la ultima sesion, se cuela aqui y los dos
        # ficheros dejan de ser comparables: uno tendria la historia del otro.
        conn.execute(
            "INSERT INTO factor_scores (ticker, date, weights_hash, composite) "
            "VALUES ('ZZZ', DATE '2026-01-05', ?, 1.0)", [whash])
        rc._registrar_universo(conn, date(2026, 8, 20), whash,
                               ["AAA", "BBB", "CCC"],
                               pd.Series(["Tech", "Banca", "Tech"]))

    destino = tmp_path / "universo.txt"
    assert rc._lista_del_universo(None, str(destino)) == 0

    # Ordenados: dos ficheros de la misma cartera tienen que salir identicos
    # byte a byte, o `fc` marcaria diferencias que no existen.
    assert destino.read_text().split() == ["AAA", "BBB", "CCC"]
    assert "3 valores" in capsys.readouterr().out


def test_sin_ranking_la_lista_lo_dice_en_vez_de_escribir_un_fichero_vacio(
        almacen, tmp_path, capsys):
    """Un fichero vacio se compararia con el del otro ordenador y diria que
    faltan los seiscientos, cuando lo que pasa es que aqui no se ha calculado
    nada todavia."""
    from stocks_tracker.compute import run_compute as rc

    destino = tmp_path / "universo.txt"
    assert rc._lista_del_universo(None, str(destino)) == 1
    assert not destino.exists()
    assert "ningun ranking" in capsys.readouterr().out
