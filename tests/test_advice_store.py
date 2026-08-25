"""El marcador, que es lo unico que separa un asesor de un adivino.

QUE PERSIGUEN ESTAS PRUEBAS

Que el marcador no se pueda inflar sin querer. Es facilisimo escribir uno que
salga siempre bien, y todas las formas de hacerlo son sutiles:

- Puntuar contra cero en vez de contra el indice. En un mercado alcista, comprar
  cualquier cosa "acierta" y el marcador dice que el asesor es un genio.
- Ensenar un porcentaje con seis datos. Un 83 % de aciertos sobre seis casos es
  ruido con aspecto de estadistica, y basta para que alguien se juegue dinero.
- Contar los MANTENER como aciertos cuando el valor sube. Nadie tomo esa
  decision ni pago comision por ella.
- Contar como fallo lo que no se pudo medir. Un dato que falta no es un error
  del asesor.

Cada uno de esos cuatro tiene test propio, porque cada uno haria que el
marcador mintiera en la direccion agradable.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import advice_store as store
from stocks_tracker.core import db
from stocks_tracker.core.advice import Conviccion, Recomendacion, Veredicto

HOY = date(2026, 8, 20)
HACE_UN_ANO = HOY - timedelta(days=365)
PERFIL = "w1"


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def _precio(conn, ticker: str, cuando: date, close: float) -> None:
    db.upsert_df(conn, "prices_daily", pd.DataFrame([{
        "ticker": ticker, "date": cuando, "open": close, "high": close,
        "low": close, "close": close, "adj_close": close, "volume": 1_000,
        "source": "yfinance",
    }]), keys=["ticker", "date"])


def _emitir(conn, ticker: str, veredicto: Veredicto, precio: float,
            cuando: date = HACE_UN_ANO) -> None:
    store.guardar(
        conn, [Recomendacion(ticker, veredicto, Conviccion.ALTA)],
        dia=cuando, weights_hash=PERFIL, precios={ticker: precio})


def _escenario(conn, *, valor_entonces: float, valor_ahora: float,
               indice_entonces: float, indice_ahora: float,
               veredicto: Veredicto = Veredicto.COMPRAR,
               ticker: str = "AAA") -> None:
    _precio(conn, ticker, HACE_UN_ANO, valor_entonces)
    _precio(conn, ticker, HOY, valor_ahora)
    _precio(conn, "^GSPC", HACE_UN_ANO, indice_entonces)
    _precio(conn, "^GSPC", HOY, indice_ahora)
    _emitir(conn, ticker, veredicto, valor_entonces)


# ---------------------------------------------------------------------------
# Guardar: lo que entra y lo que no
# ---------------------------------------------------------------------------
def test_solo_se_guarda_lo_accionable(almacen):
    """Guardar los MANTENER llenaria la tabla de filas que nadie va a puntuar,
    y tentaria a contarlas como aciertos cuando el valor sube."""
    with db.connect() as conn:
        n = store.guardar(conn, [
            Recomendacion("AAA", Veredicto.COMPRAR, Conviccion.ALTA),
            Recomendacion("BBB", Veredicto.MANTENER, Conviccion.ALTA),
            Recomendacion("CCC", Veredicto.SIN_OPINION, Conviccion.BAJA),
            Recomendacion("DDD", Veredicto.VETADA, Conviccion.ALTA),
        ], dia=HOY, weights_hash=PERFIL,
            precios={"AAA": 10.0, "BBB": 10.0, "CCC": 10.0, "DDD": 10.0})

        guardados = conn.execute(
            "SELECT ticker FROM recommendations ORDER BY ticker").fetchall()

    assert n == 1
    assert [g[0] for g in guardados] == ["AAA"]


def test_se_guarda_el_precio_del_dia_o_no_se_puede_puntuar_despues(almacen):
    """Sin el precio de aquel dia, dentro de seis meses no hay forma de saber a
    que se recomendo comprar. El precio de hoy ya no sirve."""
    with db.connect() as conn:
        _emitir(conn, "AAA", Veredicto.COMPRAR, 42.5)
        precio = conn.execute("SELECT precio FROM recommendations").fetchone()[0]

    assert precio == pytest.approx(42.5)


def test_recalcular_el_mismo_dia_no_duplica(almacen):
    with db.connect() as conn:
        _emitir(conn, "AAA", Veredicto.COMPRAR, 10.0, cuando=HOY)
        _emitir(conn, "AAA", Veredicto.VENDER, 10.0, cuando=HOY)
        filas = conn.execute(
            "SELECT veredicto FROM recommendations").fetchall()

    assert len(filas) == 1
    assert filas[0][0] == "vender"


def test_los_motivos_sobreviven_al_viaje(almacen):
    """El marcador sin los motivos solo dice que fallaste, no por que. Aprender
    de un fallo necesita las dos cosas."""
    import json

    with db.connect() as conn:
        store.guardar(conn, [Recomendacion(
            "AAA", Veredicto.COMPRAR, Conviccion.ALTA,
            motivos=["Percentil 96 %"], desmentiria=["Si pierde 80,00"])],
            dia=HOY, weights_hash=PERFIL, precios={"AAA": 100.0})
        m, d = conn.execute(
            "SELECT motivos, desmentiria FROM recommendations").fetchone()

    assert json.loads(m) == ["Percentil 96 %"]
    assert json.loads(d) == ["Si pierde 80,00"]


# ---------------------------------------------------------------------------
# Puntuar CONTRA EL INDICE. El fallo que haria que todo pareciera funcionar
# ---------------------------------------------------------------------------
def test_subir_menos_que_el_mercado_NO_es_un_acierto(almacen):
    """EL FALLO QUE MAS ENGANA. El valor sube un 3 %, asi que un marcador
    ingenuo canta acierto. Pero el indice subio un 8 %: elegir esta empresa fue
    PEOR que no elegir nada, y eso es justo lo que hay que saber.

    Si este test cae, el marcador estara midiendo el mercado y no el asesor.
    """
    with db.connect() as conn:
        _escenario(conn, valor_entonces=100, valor_ahora=103,
                   indice_entonces=100, indice_ahora=108)
        p = store.puntuar(conn, hasta=HOY)

    assert len(p) == 1
    assert p.iloc[0]["retorno"] == pytest.approx(0.03)
    assert not p.iloc[0]["acierto"], (
        "el marcador esta puntuando contra cero en vez de contra el indice"
    )


def test_bajar_menos_que_el_mercado_SI_es_un_acierto(almacen):
    """El caso simetrico. En un mercado que cae un 20 %, perder un 5 % es
    haberlo hecho bien, y un marcador contra cero lo llamaria fallo."""
    with db.connect() as conn:
        _escenario(conn, valor_entonces=100, valor_ahora=95,
                   indice_entonces=100, indice_ahora=80)
        p = store.puntuar(conn, hasta=HOY)

    assert p.iloc[0]["acierto"]


def test_una_venta_acierta_cuando_el_valor_lo_hace_peor_que_el_mercado(almacen):
    """El signo se invierte: una venta acierta si te libraste de algo malo."""
    with db.connect() as conn:
        _escenario(conn, valor_entonces=100, valor_ahora=90,
                   indice_entonces=100, indice_ahora=110,
                   veredicto=Veredicto.VENDER)
        p = store.puntuar(conn, hasta=HOY)

    assert p.iloc[0]["acierto"]


def test_una_venta_falla_si_el_valor_siguio_subiendo(almacen):
    with db.connect() as conn:
        _escenario(conn, valor_entonces=100, valor_ahora=130,
                   indice_entonces=100, indice_ahora=105,
                   veredicto=Veredicto.VENDER)
        p = store.puntuar(conn, hasta=HOY)

    assert not p.iloc[0]["acierto"]


def test_no_se_puntua_antes_de_que_venza_el_horizonte(almacen):
    """Mirar a los quince dias una recomendacion pensada a seis meses mide el
    ruido de dos semanas, no la decision."""
    ayer = HOY - timedelta(days=1)
    with db.connect() as conn:
        _precio(conn, "AAA", ayer, 100.0)
        _precio(conn, "^GSPC", ayer, 100.0)
        _emitir(conn, "AAA", Veredicto.COMPRAR, 100.0, cuando=ayer)
        p = store.puntuar(conn, hasta=HOY)

    assert p.empty


def test_lo_que_no_se_pudo_medir_no_cuenta_como_fallo(almacen):
    """Un dato que falta no es un error del asesor. Cargarselo en su cuenta
    haria el marcador injustamente malo, y un marcador injusto se ignora igual
    que uno inflado."""
    with db.connect() as conn:
        _precio(conn, "^GSPC", HACE_UN_ANO, 100.0)
        _precio(conn, "^GSPC", HOY, 110.0)
        _emitir(conn, "SIN_PRECIO", Veredicto.COMPRAR, 100.0)
        p = store.puntuar(conn, hasta=HOY)

    assert p.empty


# ---------------------------------------------------------------------------
# El agregado: con pocos datos, no se da porcentaje
# ---------------------------------------------------------------------------
def _resultado(puntuadas: int, aciertos: int) -> store.Resultado:
    return store.Resultado("TODO", puntuadas, aciertos, 1.0, 2.0)


def test_con_pocas_recomendaciones_no_hay_tasa():
    """EL SEGUNDO FALLO QUE MAS ENGANA. Un 83 % sobre seis casos es ruido con
    aspecto de estadistica, y basta para que alguien se juegue dinero."""
    assert _resultado(6, 5).tasa is None
    assert not _resultado(6, 5).bastantes


def test_con_bastantes_si():
    r = _resultado(40, 24)

    assert r.bastantes
    assert r.tasa == pytest.approx(0.6)


def test_el_resumen_dice_que_esta_vacio_y_por_que():
    """El estado inicial, que va a durar meses. Una pantalla que no lo explique
    parecera rota."""
    texto = store.resumen_honesto([])

    assert "empieza vacio" in texto
    assert "hacia delante" in texto


def test_el_resumen_no_da_porcentaje_cuando_no_toca():
    """La frase mas facil de inflar sin querer, con test palabra por palabra."""
    texto = store.resumen_honesto([_resultado(6, 5)])

    assert "%" not in texto, "esta ensenando un porcentaje con seis datos"
    assert "6 recomendaciones" in texto
    assert str(store.MIN_PARA_OPINAR) in texto


def test_el_resumen_da_la_cifra_cuando_hay_de_donde():
    texto = store.resumen_honesto([_resultado(40, 24)])

    assert "60%" in texto
    assert "indice" in texto


def test_el_marcador_separa_comprar_de_vender(almacen):
    """"Acierta el asesor" y "acierta comprando pero no vendiendo" son
    preguntas distintas, y la segunda es la que dice que hay que arreglar."""
    with db.connect() as conn:
        _escenario(conn, valor_entonces=100, valor_ahora=130,
                   indice_entonces=100, indice_ahora=105, ticker="SUBE")
        _escenario(conn, valor_entonces=100, valor_ahora=130,
                   indice_entonces=100, indice_ahora=105, ticker="MAL",
                   veredicto=Veredicto.VENDER)
        m = store.marcador(store.puntuar(conn, hasta=HOY))

    por_veredicto = {r.veredicto: r for r in m}
    assert por_veredicto["comprar"].aciertos == 1
    assert por_veredicto["vender"].aciertos == 0
    assert por_veredicto["TODO"].puntuadas == 2


def test_sin_indice_no_se_puntua_nada(almacen):
    """AGUJERO ENCONTRADO POR LA BATERIA DE MUTACION.

    Si falta la serie del indice no hay con que comparar, y seguir adelante
    significaria medir contra cero: exactamente el fallo que este modulo existe
    para evitar, colado por la puerta de atras el dia que la descarga de ^GSPC
    falle.

    Mejor no dar marcador que dar uno que mide el mercado.
    """
    with db.connect() as conn:
        _precio(conn, "AAA", HACE_UN_ANO, 100.0)
        _precio(conn, "AAA", HOY, 130.0)
        _emitir(conn, "AAA", Veredicto.COMPRAR, 100.0)   # sin ^GSPC en el almacen
        p = store.puntuar(conn, hasta=HOY)

    assert p.empty, "se esta puntuando sin indice con el que comparar"


def test_sin_ningun_precio_tampoco(almacen):
    with db.connect() as conn:
        _emitir(conn, "AAA", Veredicto.COMPRAR, 100.0)
        assert store.puntuar(conn, hasta=HOY).empty
