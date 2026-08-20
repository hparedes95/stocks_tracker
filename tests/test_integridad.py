"""El panel de integridad: ocho comprobaciones en una lista.

Las comprobaciones ya existian repartidas —calidad de precios, cuarentena,
consenso, contradicciones, frescura, validacion—. Cada una en su sitio esta
bien para el codigo y mal para quien mira: hacen falta cuatro pantallas y una
consola para saber si hoy te puedes fiar del programa.

Lo que se prueba aqui es sobre todo UNA distincion, que es la que hace que el
panel sirva para algo: gris no es verde. Verde significa "se ha comprobado y no
se ha encontrado nada". Gris significa "no se ha comprobado". Un panel que
pinta de verde lo que no ha mirado da tranquilidad sin haberla ganado, y es
peor que no tener panel.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from stocks_tracker.core import db, integrity

HOY = date(2026, 8, 20)
AHORA = pd.Timestamp("2026-08-20 12:00:00")


@pytest.fixture
def warehouse(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return Stub


def punto(nombre: str, puntos: list[integrity.Punto]) -> integrity.Punto:
    encontrados = [p for p in puntos if p.nombre == nombre]
    assert encontrados, f"no hay ningun punto llamado {nombre!r}"
    return encontrados[0]


def revisar() -> list[integrity.Punto]:
    with db.connect(read_only=True) as conn:
        return integrity.revisar(conn, AHORA)


def con_precios(source: str = "yfinance", ultima: date = HOY) -> None:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class, gics_sector, is_active) "
            "VALUES ('AAA', 'equity', 'Tecnologia', TRUE)"
        )
        precios = pd.DataFrame([
            {"ticker": "AAA", "date": ultima - timedelta(days=d), "close": 100.0,
             "adj_close": 100.0, "open": 99.0, "high": 101.0, "low": 98.0,
             "volume": 1_000_000, "source": source}
            for d in range(5)
        ])
        db.upsert_df(conn, "prices_daily", precios, keys=["ticker", "date"])


# ---------------------------------------------------------------------------
# LA distincion
# ---------------------------------------------------------------------------

def test_un_almacen_vacio_no_esta_verde(warehouse):
    """Sin datos no hay nada comprobado. Ninguna comprobacion puede salir en
    verde, y el veredicto global no puede ser 'bien'."""
    puntos = revisar()

    assert integrity.veredicto(puntos) != integrity.BIEN
    assert all(p.estado != integrity.BIEN for p in puntos), (
        "hay comprobaciones en verde sobre un almacen vacio"
    )


def test_lo_que_no_se_ha_comprobado_sale_gris_y_no_verde(warehouse):
    """El consenso entre proveedores no se ha ejecutado nunca: eso es gris."""
    con_precios()

    p = punto("Consenso entre proveedores", revisar())

    assert p.estado == integrity.SIN_COMPROBAR
    assert p.donde, "un punto pendiente sin decir adonde ir es una alarma sin salida"


def test_una_comprobacion_caducada_vuelve_a_gris(warehouse):
    """Una comprobacion de hace tres semanas no dice nada del estado de hoy, y
    pintarla en verde es la misma mentira que pintar de verde lo no mirado."""
    con_precios()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO data_quality (check_name, passed, severity, checked_at, "
            "run_id) VALUES ('ohlc_incoherente', TRUE, 'info', ?, 'r1')",
            [AHORA.to_pydatetime() - timedelta(days=21)],
        )

    assert punto("Calidad de los precios", revisar()).estado == integrity.SIN_COMPROBAR


def test_una_comprobacion_reciente_y_limpia_si_sale_verde(warehouse):
    """El contrario, para que el de arriba no pase por el motivo equivocado."""
    con_precios()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO data_quality (check_name, passed, severity, checked_at, "
            "run_id) VALUES ('ohlc_incoherente', TRUE, 'info', ?, 'r1')",
            [AHORA.to_pydatetime() - timedelta(hours=2)],
        )

    assert punto("Calidad de los precios", revisar()).estado == integrity.BIEN


# ---------------------------------------------------------------------------
# Los datos de prueba
# ---------------------------------------------------------------------------

def test_los_datos_sinteticos_son_rojo_y_no_aviso(warehouse):
    """No es un dato viejo ni incompleto: es que NADA de lo que ensena el
    programa describe el mercado."""
    con_precios(source="synthetic")

    p = punto("Datos", revisar())

    assert p.estado == integrity.MAL
    assert "DATOS DE PRUEBA" in p.detalle


def test_unos_precios_reales_y_frescos_estan_bien(warehouse):
    con_precios()
    assert punto("Datos", revisar()).estado == integrity.BIEN


def test_unos_precios_viejos_avisan(warehouse):
    con_precios(ultima=HOY - timedelta(days=20))
    assert punto("Datos", revisar()).estado == integrity.AVISO


# ---------------------------------------------------------------------------
# El veredicto global
# ---------------------------------------------------------------------------

def test_el_veredicto_es_el_peor_y_no_una_media():
    """Un panel con siete verdes y un rojo NO esta al 87 %: esta roto.
    Promediar estados es la forma clasica de que un problema grave desaparezca
    detras de una mayoria de cosas que van bien."""
    puntos = [integrity.Punto(f"p{i}", integrity.BIEN, "") for i in range(7)]
    puntos.append(integrity.Punto("malo", integrity.MAL, ""))

    assert integrity.veredicto(puntos) == integrity.MAL


def test_el_gris_pesa_mas_que_el_aviso():
    """No saber es peor que saber que algo va regular: un aviso esta medido y
    un gris no. Con el orden al reves, un panel entero sin comprobar se
    resumiria como 'aviso' y pareceria bajo control."""
    puntos = [integrity.Punto("a", integrity.AVISO, ""),
              integrity.Punto("b", integrity.SIN_COMPROBAR, "")]

    assert integrity.veredicto(puntos) == integrity.SIN_COMPROBAR


def test_todo_verde_da_verde():
    puntos = [integrity.Punto(f"p{i}", integrity.BIEN, "") for i in range(3)]
    assert integrity.veredicto(puntos) == integrity.BIEN


def test_sin_puntos_no_se_declara_que_todo_va_bien():
    """Una lista vacia es la ausencia de informacion, no una buena noticia."""
    assert integrity.veredicto([]) == integrity.SIN_COMPROBAR


def test_los_pendientes_salen_con_lo_peor_primero():
    puntos = [integrity.Punto("a", integrity.BIEN, ""),
              integrity.Punto("b", integrity.SIN_COMPROBAR, ""),
              integrity.Punto("c", integrity.MAL, ""),
              integrity.Punto("d", integrity.AVISO, "")]

    # Mismo orden que el veredicto: rojo, gris, amarillo. El gris va antes que
    # el amarillo por lo mismo que en `ORDEN`: no saber es peor que saber que
    # algo va regular.
    assert [p.nombre for p in integrity.pendientes(puntos)] == ["c", "b", "d"]


# ---------------------------------------------------------------------------
# Robustez
# ---------------------------------------------------------------------------

def test_una_comprobacion_que_revienta_no_tumba_el_panel(warehouse, monkeypatch):
    """Un panel de integridad que se cae entero porque una consulta falla es
    justo lo contrario de lo que hace falta el dia que algo se rompe."""
    con_precios()

    def explota(conn):
        raise RuntimeError("consulta rota")

    monkeypatch.setattr(
        integrity, "COMPROBACIONES",
        (("Datos", integrity._datos, True), ("Rota", explota, False)),
    )
    puntos = revisar()

    assert len(puntos) == 2
    rota = punto("Rota", puntos)
    assert rota.estado == integrity.SIN_COMPROBAR
    assert "consulta rota" in rota.detalle


def test_cada_estado_tiene_su_icono():
    """Un estado sin icono saldria en blanco y pareceria que no pasa nada."""
    assert set(integrity.SEMAFORO) == set(integrity.ORDEN)


def test_todas_las_comprobaciones_devuelven_un_punto(warehouse):
    """Guardarrail del contrato: una que devolviera None dejaria un hueco en la
    lista sin que nada fallase."""
    con_precios()
    puntos = revisar()

    assert len(puntos) == len(integrity.COMPROBACIONES)
    assert all(isinstance(p, integrity.Punto) for p in puntos)
    assert all(p.detalle for p in puntos), "hay puntos sin explicacion"
    assert all(p.estado in integrity.SEMAFORO for p in puntos)


def test_todo_punto_que_no_esta_verde_dice_adonde_ir(warehouse):
    """Una alarma sin salida entrena a ignorar las alarmas."""
    con_precios(source="synthetic")

    for p in integrity.pendientes(revisar()):
        assert p.donde, f"'{p.nombre}' esta en {p.estado} y no dice donde mirar"


# ---------------------------------------------------------------------------
# El esquema al dia
# ---------------------------------------------------------------------------

def test_el_dashboard_actualiza_el_esquema_al_arrancar():
    """Averia encontrada por el propio panel al pintarlo por primera vez.

    Hasta ahora el esquema solo se actualizaba al ingerir o al calcular. Un
    usuario que actualiza el programa y abre el dashboard antes de descargar
    nada se encuentra "Catalog Error: Table with name price_consensus does not
    exist": la version nueva consulta una tabla que su almacen, creado con la
    anterior, no tiene.

    Se comprueba sobre el codigo porque lo que importa es que la llamada este
    en el arranque. Probar `migrate()` en si no sirve: esa funcion ya funciona,
    lo que faltaba era llamarla.
    """
    from stocks_tracker.core.config import project_root

    src = (project_root() / "src/stocks_tracker/app/main.py").read_text("utf-8")

    assert "migrate()" in src, "el dashboard ya no pone al dia el esquema"
    assert "_actualizar_esquema" in src
    # Y no puede tumbar el arranque: DuckDB admite un solo escritor, asi que
    # abrir el dashboard mientras corre la ingesta tiene que seguir funcionando.
    assert "except Exception" in src


def test_una_tabla_nueva_aparece_en_un_almacen_viejo(tmp_path, monkeypatch):
    """La prueba de verdad de lo anterior: crear el almacen, borrarle una tabla
    y comprobar que `migrate` la devuelve sin tocar los datos de las demas."""
    class Stub:
        warehouse_path = tmp_path / "viejo.duckdb"
        compute: dict = {}
        raw: dict = {}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO instruments (ticker, asset_class) VALUES ('AAA', 'equity')"
        )
        conn.execute("DROP TABLE price_consensus")

    db.migrate()

    with db.connect(read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM price_consensus").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM instruments").fetchone()[0] == 1, (
            "migrar ha borrado datos que ya estaban"
        )
