"""Contrastar el broker con el programa.

Paso 11 del plan, y la unica comprobacion de todo el proyecto que se hace contra
la VERDAD. Las demas contrastan un proveedor con otro, o un dato consigo mismo.
Aqui hay un tercero que no se equivoca en lo que importa, porque es quien tiene
el dinero.

Lo que se prueba, sobre todo, es que las tres diferencias posibles NO son la
misma cosa, y que la peor de las tres —una posicion que solo existe en un lado—
no se pierda entre las pequenas.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import db, reconcile


def posicion(qty: float, coste: float) -> dict:
    return {"qty": qty, "avg_cost": coste}


# ---------------------------------------------------------------------------
# Cuando cuadra
# ---------------------------------------------------------------------------

def test_dos_contabilidades_iguales_no_dan_diferencias():
    cartera = {"AAA": posicion(10, 95.0), "BBB": posicion(5, 200.0)}

    assert reconcile.comparar(cartera, dict(cartera)) == []


def test_dos_carteras_vacias_cuadran():
    assert reconcile.comparar({}, {}) == []


def test_una_fraccion_de_redondeo_no_cuenta():
    """El broker redondea las fracciones a menos decimales que nosotros."""
    diferencias = reconcile.comparar({"AAA": posicion(10.0000001, 95.0)},
                                     {"AAA": posicion(10.0, 95.0)})

    assert diferencias == []


def test_medio_centimo_de_coste_no_cuenta():
    assert reconcile.comparar({"AAA": posicion(10, 95.004)},
                              {"AAA": posicion(10, 95.0)}) == []


# ---------------------------------------------------------------------------
# Las tres diferencias, que no son la misma
# ---------------------------------------------------------------------------

def test_una_cantidad_distinta_se_detecta():
    """De este numero sale el tamano de la siguiente orden."""
    d = reconcile.comparar({"AAA": posicion(12, 95.0)},
                           {"AAA": posicion(10, 95.0)})

    assert len(d) == 1
    assert d[0].campo == "qty"
    assert d[0].broker == 12 and d[0].propio == 10


def test_un_coste_medio_distinto_se_detecta():
    """La cantidad cuadra, asi que lo que no es tuyo es el P&L."""
    d = reconcile.comparar({"AAA": posicion(10, 91.5)},
                           {"AAA": posicion(10, 95.0)})

    assert len(d) == 1
    assert d[0].campo == "avg_cost"
    assert "P&L" in d[0].detalle


def test_una_posicion_que_el_programa_no_ve_se_detecta():
    """La peor de las tres: existe con tu dinero dentro, no tiene stop, no
    cuenta para el limite de exposicion y no sale en ningun aviso."""
    d = reconcile.comparar({"AAA": posicion(10, 95.0)}, {})

    assert len(d) == 1
    assert d[0].campo == "posicion_ausente"
    assert "no tiene stop" in d[0].detalle


def test_una_posicion_que_solo_existe_en_el_programa_se_detecta():
    """Se recorren las DOS claves. Mirando solo las del broker, esta quedaria
    invisible: el programa seguiria contandola para el limite de exposicion y
    dejaria de comprar por una posicion que no tiene."""
    d = reconcile.comparar({}, {"AAA": posicion(10, 95.0)})

    assert len(d) == 1
    assert d[0].campo == "posicion_fantasma"


def test_el_efectivo_se_compara_aparte():
    """De el sale cuanto se puede comprar."""
    d = reconcile.comparar({}, {}, efectivo_broker=1000.0, efectivo_propio=1500.0)

    assert len(d) == 1
    assert d[0].campo == "cash"
    assert d[0].ticker is None


def test_sin_efectivo_declarado_no_se_inventa_una_diferencia():
    """Un broker que no informa del efectivo no es un broker que tenga cero."""
    assert reconcile.comparar({}, {}, efectivo_broker=None,
                              efectivo_propio=1500.0) == []


# ---------------------------------------------------------------------------
# El orden
# ---------------------------------------------------------------------------

def test_lo_mas_gordo_sale_primero():
    """Una lista ordenada por ticker esconde la diferencia de mil euros entre
    veinte de tres centimos."""
    d = reconcile.comparar(
        {"AAA": posicion(10, 95.03), "ZZZ": posicion(10, 1095.0)},
        {"AAA": posicion(10, 95.0), "ZZZ": posicion(10, 95.0)},
    )

    assert [x.ticker for x in d] == ["ZZZ", "AAA"]


def test_una_posicion_ausente_pesa_mas_que_cualquier_diferencia():
    """No se puede medir en euros contra las otras: no hay con que compararla,
    y ademas es la que mas duele. Va siempre arriba."""
    d = reconcile.comparar(
        {"AAA": posicion(10, 1_000_000.0), "NUEVA": posicion(1, 5.0)},
        {"AAA": posicion(10, 95.0)},
    )

    assert d[0].campo == "posicion_ausente"


# ---------------------------------------------------------------------------
# Lo que NO se hace
# ---------------------------------------------------------------------------

def test_comparar_no_toca_ninguna_de_las_dos_contabilidades():
    """Copiar los numeros del broker BORRA la prueba de que hubo un desajuste, y
    con ella la pregunta de por que lo hubo. Un desajuste tiene una causa y esa
    causa se va a repetir."""
    broker = {"AAA": posicion(12, 95.0)}
    propio = {"AAA": posicion(10, 95.0)}

    reconcile.comparar(broker, propio)

    assert propio["AAA"]["qty"] == 10, "se ha 'corregido' la cartera propia"
    assert broker["AAA"]["qty"] == 12


def test_el_modulo_no_escribe_en_positions():
    """Guardarrail. La tentacion de arreglar el descuadre copiando el broker es
    exactamente como empiezan las contabilidades que no cuadran."""
    from stocks_tracker.core.config import project_root

    src = (project_root() / "src/stocks_tracker/core/reconcile.py").read_text("utf-8")

    assert "UPDATE positions" not in src
    assert "INSERT INTO positions" not in src
    assert "DELETE FROM positions" not in src


# ---------------------------------------------------------------------------
# Guardar
# ---------------------------------------------------------------------------

@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def test_se_guarda_tambien_cuando_todo_cuadra(warehouse):
    """Guardar solo los desajustes deja una tabla en la que no se distingue
    "hoy cuadra" de "hace tres meses que nadie lo mira"."""
    with db.connect() as conn:
        reconcile.guardar(conn, [], "ibkr", "run-1", n_posiciones=7)

    filas = db.query("SELECT * FROM reconciliation")
    assert len(filas) == 1
    assert filas.iloc[0]["estado"] == reconcile.CUADRA
    assert filas.iloc[0]["broker"] == 7


def test_se_guarda_cada_diferencia_con_los_dos_numeros(warehouse):
    diferencias = reconcile.comparar({"AAA": posicion(12, 95.0)},
                                     {"AAA": posicion(10, 95.0)})
    with db.connect() as conn:
        reconcile.guardar(conn, diferencias, "ibkr", "run-1", n_posiciones=1)

    fila = db.query("SELECT * FROM reconciliation").iloc[0]
    assert fila["estado"] == reconcile.DIFIERE
    assert fila["broker"] == 12
    assert fila["propio"] == 10
    assert fila["diferencia"] == 2


def test_las_posiciones_del_almacen_excluyen_las_cerradas(warehouse):
    from datetime import date

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES ('p1','AAA',10,95.0,'EUR',?,NULL,'',NULL)",
            [date(2026, 1, 5)])
        conn.execute(
            "INSERT INTO positions VALUES ('p2','BBB',5,50.0,'EUR',?,?,'',NULL)",
            [date(2026, 1, 5), date(2026, 6, 1)])
        propio = reconcile.posiciones_del_almacen(conn)

    assert set(propio) == {"AAA"}


def test_el_resumen_destaca_las_posiciones_de_un_solo_lado(warehouse):
    diferencias = reconcile.comparar({"AAA": posicion(10, 95.0)}, {})

    texto = reconcile.resumen(diferencias, n_posiciones=1)

    assert "solo existen en un lado" in texto


def test_el_resumen_cuando_cuadra_lo_dice_sin_adornos():
    assert "cuadran" in reconcile.resumen([], n_posiciones=4)


# ---------------------------------------------------------------------------
# El comando
# ---------------------------------------------------------------------------

class BrokerFalso:
    """Adaptador minimo con la interfaz que usa la reconciliacion."""

    def __init__(self, posiciones, efectivo=1000.0, revienta=False):
        self._posiciones = posiciones
        self._efectivo = efectivo
        self._revienta = revienta

    def get_positions(self):
        from stocks_tracker.trading.brokers.base import BrokerError, Position

        if self._revienta:
            raise BrokerError("la API no responde")
        return [
            Position(symbol=t, qty=d["qty"], avg_entry_price=d["avg_cost"],
                     market_value=0.0, unrealized_pl=0.0, unrealized_plpc=0.0,
                     current_price=0.0)
            for t, d in self._posiciones.items()
        ]

    def get_account(self):
        from stocks_tracker.trading.brokers.base import Account

        return Account(account_id="x", currency="EUR", cash=self._efectivo,
                       equity=0.0, buying_power=0.0, last_equity=0.0)


def test_el_comando_traduce_las_posiciones_del_broker(warehouse):
    from datetime import date

    from stocks_tracker.trading import reconcile_cli

    with db.connect() as conn:
        conn.execute(
            "INSERT INTO positions VALUES ('p1','AAA',10,95.0,'EUR',?,NULL,'',NULL)",
            [date(2026, 1, 5)])

    diferencias = reconcile_cli.reconciliar(
        BrokerFalso({"AAA": posicion(12, 95.0)}), efectivo_propio=1000.0)

    assert len(diferencias) == 1
    assert diferencias[0].campo == "qty"


def test_si_el_broker_no_da_las_posiciones_no_se_compara_nada(warehouse):
    """Tratar el fallo como "cartera vacia" produciria una posicion fantasma por
    cada valor que SI tienes, y un rojo aparatoso que no describe la realidad."""
    from stocks_tracker.trading import reconcile_cli
    from stocks_tracker.trading.brokers.base import BrokerError

    with pytest.raises(BrokerError):
        reconcile_cli.del_broker(BrokerFalso({}, revienta=True))


def test_en_modo_simulado_no_se_declara_que_cuadra():
    """Las posiciones del broker simulado salen del mismo sitio que las del
    programa. Compararlas daria 'cuadra' siempre, y ese verde no lo ha ganado
    nadie."""
    from stocks_tracker.trading import reconcile_cli

    codigo = reconcile_cli.main([])

    assert codigo == reconcile_cli.EXIT_SIN_BROKER_REAL
    assert codigo != 0, "un exito aqui se leeria como que la cartera cuadra"
