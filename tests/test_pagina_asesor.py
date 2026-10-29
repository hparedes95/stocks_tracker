"""La pagina del asesor, pintada con una cartera que de verdad dispara consejos.

El smoke test de `test_paginas.py` comprueba que las doce paginas no revientan,
pero no siembra cartera: la mitad de esta pagina —la que decide sobre lo que ya
tienes— no llegaba a ejecutarse.

Y hay una comprobacion que solo se puede hacer aqui: que el marcador diga en voz
alta que esta vacio. Una pantalla de consejos que empieza sin marcador y no lo
explica parece rota o, peor, parece validada.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from stocks_tracker.core import db
from stocks_tracker.core.config import project_root

HOY = date(2026, 8, 20)
RUTA = project_root() / "src/stocks_tracker/app/pages/12_asesor.py"


@pytest.fixture
def almacen(tmp_path, monkeypatch):
    class Stub:
        warehouse_path = tmp_path / "test.duckdb"
        compute: dict = {"weights_preset": "balanced"}
        raw: dict = {}
        ui: dict = {"data_freshness_warn_hours": 30}

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    st.cache_data.clear()
    st.cache_resource.clear()
    yield Stub
    st.cache_data.clear()
    st.cache_resource.clear()


def _sembrar_cartera() -> None:
    """Una posicion con el margen desplomado y la deuda disparada.

    Es el caso que tiene que producir un veredicto, no un silencio: si la
    pagina lo pinta como "mantener", el asesor esta callando justo cuando hay
    algo que decir.
    """
    from stocks_tracker.app import data_access as da

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "AAA", "name": "Alfa", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Tecnologia", "is_active": True},
        ]), keys=["ticker"])
        for tabla, extra in (("prices_daily", {"open": 100.0, "high": 100.0,
                                               "low": 100.0, "adj_close": 100.0,
                                               "volume": 1_000,
                                               "source": "yfinance"}),
                             ("indicators_daily", {"atr_pct": 2.0})):
            db.upsert_df(conn, tabla, pd.DataFrame([
                {"ticker": "AAA", "date": HOY, "close": 100.0, **extra},
            ]), keys=["ticker", "date"])
        # La foto de fundamentales de hoy y la del dia de la compra.
        db.upsert_df(conn, "fundamentals_snapshot", pd.DataFrame([
            {"ticker": "AAA", "as_of": HOY, "profit_margin": 8.0,
             "net_debt_to_ebitda": 5.0},
            {"ticker": "AAA", "as_of": HOY - timedelta(days=200),
             "profit_margin": 24.0, "net_debt_to_ebitda": 0.9},
        ]), keys=["ticker", "as_of"])

    da.add_position("AAA", 10, 90.0, "EUR")
    with db.connect() as conn:
        conn.execute("UPDATE positions SET opened_at = ?",
                     [HOY - timedelta(days=180)])
    st.cache_data.clear()


def _pintar() -> AppTest:
    prueba = AppTest.from_file(str(RUTA), default_timeout=120)
    prueba.run()
    assert not prueba.exception, prueba.exception[0].message
    return prueba


def test_saving_profile_applies_new_horizon_in_the_same_render(almacen, monkeypatch):
    from stocks_tracker.app import runtime_reads

    seen = []
    monkeypatch.setattr(runtime_reads, 'get_holding_analysis',
                        lambda *, horizon: seen.append(horizon) or [])
    page = _pintar()
    horizon = next(s for s in page.selectbox if s.label == 'Horizonte de la recomendación (meses)')
    horizon.set_value(1)
    next(b for b in page.button if b.label == 'Guardar perfil del asesor').click().run()
    assert not page.exception
    assert seen[-1] == 21


@pytest.mark.parametrize('partial,owned', [(False, False), (True, False), (True, True)])
def test_real_rule_purchase_is_saved_and_shown_before_research_scoreboard(almacen, partial, owned):
    import json

    from stocks_tracker.compute.run_advice import calcular_y_guardar
    from stocks_tracker.core.investor_profile import DEFAULTS, save
    from stocks_tracker.core.scoring import preset_hash
    from stocks_tracker.core.trading_calendar import sessions

    days = sessions('XMAD', date.today() - timedelta(days=450), date.today())[-260:]
    with db.connect() as conn:
        conn.execute("INSERT INTO instruments(ticker,country,currency,asset_class) VALUES "
                     "('AAA','Spain','EUR','equity'),('EWP','US','EUR','etf')")
        values = [100 + i / 10 for i in range(len(days))]
        db.upsert_df(conn, 'prices_daily', pd.DataFrame({'ticker':'AAA', 'date':days,
                     'close':values, 'adj_close':values, 'source':'fixture'}), keys=['ticker','date'])
        conn.execute("INSERT INTO indicators_daily(ticker,date,close,atr_pct) VALUES ('AAA',?,?,2)",
                     [days[-1].date(), values[-1]])
        conn.execute("INSERT INTO factor_scores(ticker,date,weights_hash,composite,composite_pctile,coverage) "
                     "VALUES ('AAA',?,?,2,.99,.9)", [days[-1].date(), preset_hash('balanced')])
        save(conn, {**DEFAULTS, 'confirmed':True})
        if partial:
            from datetime import UTC, datetime

            from stocks_tracker.core import snaptrade_portfolio as snap

            now = datetime.now(UTC).isoformat()
            book = [{'id': t, 'symbol': t, 'kind': 'cfd', 'listing_currency': 'USD',
                     'exchange': 'OTC', 'qty': 2., 'avg_cost': None, 'currency': 'USD',
                     'price': 100., 'cash_equivalent': False} for t in ('CIBR', 'ITA')]
            if owned:
                conn.execute("UPDATE instruments SET exchange='XMAD' WHERE ticker='AAA'")
                book.append({'id': 'aaa', 'symbol': 'AAA', 'kind': 'stock',
                    'listing_currency': 'EUR', 'exchange': 'XMAD', 'qty': 2., 'avg_cost': None,
                    'currency': 'EUR', 'price': values[-1], 'cash_equivalent': False})
            snap.store_snapshot(conn, {'account_id': 'fixture', 'as_of': now, 'cash_as_of': now,
                'balances': [{'currency': 'EUR', 'cash': 390.12}], 'positions': book})
            snap.enable(conn, True)
    assert calcular_y_guardar(caja=10000) == 1
    with db.connect(read_only=True) as conn:
        verdict, raw = conn.execute("SELECT veredicto,assessment FROM recommendations").fetchone()
        assert verdict == ('ampliar_condicionado' if owned else 'comprar_condicionado' if partial else 'comprar')
        assert json.loads(raw)['decision_basis'] == 'rules'
        assert conn.execute('SELECT count(*) FROM recommendation_ledger').fetchone()[0] == 1
        assert conn.execute('SELECT count(*) FROM orders').fetchone()[0] == 0
        if partial:
            assert conn.execute('SELECT importe_eur,titulos,stop,riesgo_eur FROM recommendations').fetchone() == (None,) * 4
            assert conn.execute('SELECT count(*) FROM evaluation_ledger').fetchone()[0] == 0
    st.cache_data.clear()
    page = _pintar()
    assert any(('Ampliar' if owned else 'Comprar') in str(m.value) for m in page.markdown)
    if partial:
        assert any('Sin importe ni títulos' in str(w.value) for w in page.warning)
        assert not any(m.label == 'Títulos a soltar' for m in page.metric)
    headings = [s.value for s in page.subheader]
    assert headings.index('Compras nuevas') < headings.index('¿Acierta este asesor?')


def test_el_marcador_vacio_se_explica_en_vez_de_parecer_roto(almacen):
    """LO PRIMERO QUE VA A VER EL USUARIO, y va a durar meses.

    Sin esta frase, una pantalla sin marcador parece que no funciona; o peor,
    parece que el asesor esta validado y simplemente no ensena numeros.
    """
    prueba = _pintar()

    texto = " ".join(str(e.value) for e in prueba.info)
    assert "empieza vacio" in texto
    assert "hacia delante" in texto


def test_cfd_exposure_and_sized_stock_advice_are_visible_without_manual_fields(almacen):
    from stocks_tracker.core.advice_store import guardar_recomendaciones
    from stocks_tracker.core.scoring import preset_hash
    from stocks_tracker.core.timeutils import utcnow
    from tests.test_cfd_risk import calculate, prepare
    from tests.test_holding_analysis import DAY, seed

    with db.connect() as conn:
        seed(conn)
        prepare(conn)
        recs = list(calculate(conn).values())
        weights = preset_hash('balanced')
        conn.execute('INSERT INTO advisor_runs VALUES (?,?,?)', [weights, utcnow(), DAY.date()])
        assert guardar_recomendaciones(conn, recs, dia=DAY.date(), weights_hash=weights,
                                      precios={'AAPL':200}) == 1
    st.cache_data.clear()
    page = _pintar()
    assert any('Patrimonio neto comunicado' in m.label for m in page.metric)
    assert any(m.label == 'Importe' and m.value != '—' for m in page.metric)
    assert any('AAPL' in str(m.value) and 'Ampliar' in str(m.value) for m in page.markdown)
    assert any('Exposición bruta (EUR)' in frame.value.columns for frame in page.dataframe)
    assert not any('Sin importe ni títulos' in str(w.value) for w in page.warning)
    script = almacen.warehouse_path.parent / 'connection_panel.py'
    script.write_text('''
from stocks_tracker.app.components.snaptrade_panel import render_snapshot
from stocks_tracker.core import snaptrade_portfolio as snap
from stocks_tracker.core.db import connect
with connect(read_only=True) as conn:
    current = snap.state(conn)
render_snapshot(current)
''', encoding='utf-8')
    connection = AppTest.from_file(str(script)).run(timeout=30)
    assert not connection.exception
    mapping = next(f.value for f in connection.dataframe if 'Serie para análisis' in f.value.columns)
    cfds = mapping[mapping['Símbolo del bróker'].isin(['CIBR', 'ITA'])]
    assert len(cfds) == 2 and cfds['Estado'].str.contains('Incluido en el riesgo').all()
    assert not any('Análisis parcial' in str(w.value) for w in connection.warning)


def test_cibr_pending_position_keeps_partial_advisor_recalculate_enabled(almacen, monkeypatch):
    from datetime import UTC, datetime

    from stocks_tracker.app import research_actions
    from stocks_tracker.core import snaptrade_portfolio as snap

    now = datetime.now(UTC).isoformat()
    snapshot = {'account_id':'test-account', 'as_of':now, 'cash_as_of':now,
                'balances':[{'currency':'EUR', 'cash':100}], 'positions':[
                    {'id':'cibr-cfd', 'symbol':'CIBR', 'kind':'cfd', 'listing_currency':'USD',
                     'exchange':'XNAS', 'qty':1., 'avg_cost':100., 'currency':'USD',
                     'price':100., 'cash_equivalent':False}]}
    with db.connect() as conn:
        snap.store_snapshot(conn, snapshot)
        snap.enable(conn, True)
    calls = []
    monkeypatch.setattr(research_actions, 'refresh_research', lambda **kwargs: calls.append(kwargs))
    page = _pintar()
    text = ' '.join(str(w.value) for w in page.warning)
    assert 'CIBR' in text and 'parcial' in text
    assert 'Asesor bloqueado' not in text
    recalculate = next(b for b in page.button if b.label == 'Recalcular asesor con los datos guardados')
    assert not recalculate.disabled
    recalculate.click().run()
    assert not page.exception and calls == [{'cash':100., 'download':False}]


def test_live_positions_and_cfd_analysis_visible_without_any_actionable_order(almacen, monkeypatch):
    from types import SimpleNamespace

    from stocks_tracker.core import holding_analysis
    from tests.test_holding_analysis import DAY, seed

    monkeypatch.setattr(holding_analysis, 'date', SimpleNamespace(today=lambda: DAY.date()))
    with db.connect() as conn:
        seed(conn)
        assert conn.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0
    page = _pintar()
    headings = [e.value for e in page.markdown]
    assert {e.value for e in page.subheader} >= {'AAPL', 'CIBR', 'ITA'}
    assert 'Mantener en vigilancia' in headings
    assert 'Subyacente favorable; vigilar el CFD' in headings
    assert 'Subyacente débil; revisar el CFD' in headings
    assert any('no hay propuestas operables' in str(e.value).lower() for e in page.info)
    assert any('CFD · referencia ETF' in str(e.value) for e in page.caption)
    assert sum('Retorno ajustado' in e.label for e in page.metric) == 3
    blocked = [d.value for d in page.dataframe if 'Motivo' in d.value.columns
               and 'Símbolo' in d.value.columns]
    assert blocked and {'CIBR', 'ITA'}.issubset(set(blocked[0]['Símbolo']))
    assert all('cfd' in reason.lower() for reason in blocked[0].set_index('Símbolo').loc[['CIBR','ITA'], 'Motivo'])
    with db.connect(read_only=True) as conn:
        assert conn.execute('SELECT count(*) FROM recommendations').fetchone()[0] == 0


def test_una_posicion_deteriorada_recibe_un_veredicto(almacen):
    """Si esto sale como MANTENER, el asesor esta callando con el margen
    cayendo del 24 % al 8 % y la deuda pasando de 0,9x a 5x.

    Se ejecuta el paso REAL que calcula y guarda, no un doble: es la unica
    forma de comprobar que la cadena entera —almacen, motor, escritura,
    pantalla— esta conectada.
    """
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    _sembrar_cartera()
    calcular_y_guardar()
    st.cache_data.clear()

    prueba = _pintar()
    texto = " ".join(str(m.value) for m in prueba.markdown)

    assert "AAA" in texto
    assert ("Vender" in texto or "Reducir" in texto), (
        "una posicion con la tesis rota no esta recibiendo veredicto"
    )


def test_lo_que_se_ensena_es_lo_que_quedo_guardado(almacen):
    """LA PROPIEDAD QUE HACE QUE EL MARCADOR SIGNIFIQUE ALGO.

    La pantalla lee lo escrito; no recalcula. Si recalculara al vuelo y el
    marcador puntuara lo guardado, bastaria un cambio de precio entre una cosa
    y otra para que el marcador estuviera puntuando consejos que nadie vio.
    """
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    _sembrar_cartera()
    calcular_y_guardar()
    st.cache_data.clear()

    with db.connect(read_only=True) as conn:
        guardados = {f[0] for f in conn.execute(
            "SELECT ticker FROM recommendations").fetchall()}

    prueba = _pintar()
    texto = " ".join(str(m.value) for m in prueba.markdown)

    assert guardados, "el paso de calculo no ha guardado nada"
    for ticker in guardados:
        assert ticker in texto, (
            f"{ticker} esta guardado pero la pantalla no lo ensena"
        )


def test_sin_calcular_la_pagina_dice_que_hacer(almacen):
    """El estado del primer dia. Una pagina vacia sin explicacion parece rota,
    y la explicacion tiene que decir el comando exacto."""
    prueba = _pintar()

    texto = " ".join(str(e.value) for e in prueba.info)
    assert "botones superiores" in texto
    assert any(b.label == "Actualizar datos y análisis" for b in prueba.button)


def test_el_efectivo_se_pide_al_calcular_y_no_se_inventa():
    """El programa no habla con el banco ni con el broker, y el extracto solo
    trae posiciones. Suponer que hay caja produciria compras que no se pueden
    ejecutar, que es la forma mas rapida de que una pantalla deje de usarse.

    El dato entra por el comando (`--caja`) y no por la pantalla: la pantalla
    solo lee lo ya calculado, asi que un efectivo tecleado alli no cambiaria
    nada de lo guardado.
    """
    import inspect

    from stocks_tracker.compute import run_advice

    firma = inspect.signature(run_advice.calcular_y_guardar)

    assert "caja" in firma.parameters
    assert firma.parameters["caja"].default == 0.0, (
        "el efectivo por defecto no puede ser mayor que cero: seria inventarlo"
    )
    assert "--caja" in inspect.getsource(run_advice.main)


def test_sin_efectivo_declarado_no_se_recomienda_comprar_nada(almacen):
    """El caso por defecto. Con caja a cero, `size_by_atr` no puede dimensionar
    y las compras salen vetadas: es lo correcto, y lo contrario seria
    recomendar comprar con dinero que no existe."""
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "close": 50.0, "atr_pct": 2.0},
        ]), keys=["ticker", "date"])
        from stocks_tracker.core.scoring import preset_hash
        db.upsert_df(conn, "factor_scores", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "weights_hash": preset_hash("balanced"),
             "composite": 2.0, "composite_pctile": 0.98, "coverage": 0.9},
        ]), keys=["ticker", "date", "weights_hash"])

    calcular_y_guardar()

    with db.connect(read_only=True) as conn:
        veredictos = [f[0] for f in conn.execute(
            "SELECT veredicto FROM recommendations WHERE ticker = 'BBB'"
        ).fetchall()]

    assert "comprar" not in veredictos, (
        "se esta recomendando comprar sin efectivo declarado"
    )


def test_efectivo_y_ranking_no_bastan_sin_evidencia(almacen):
    """Tener efectivo no demuestra ventaja fuera de muestra."""
    from stocks_tracker.compute.run_advice import calcular_y_guardar

    with db.connect() as conn:
        db.upsert_df(conn, "instruments", pd.DataFrame([
            {"ticker": "BBB", "name": "Beta", "asset_class": "equity",
             "currency": "EUR", "gics_sector": "Banca", "is_active": True},
        ]), keys=["ticker"])
        db.upsert_df(conn, "indicators_daily", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "close": 50.0, "atr_pct": 2.0},
        ]), keys=["ticker", "date"])
        from stocks_tracker.core.scoring import preset_hash
        db.upsert_df(conn, "factor_scores", pd.DataFrame([
            {"ticker": "BBB", "date": HOY, "weights_hash": preset_hash("balanced"),
             "composite": 2.0, "composite_pctile": 0.98, "coverage": 0.9},
        ]), keys=["ticker", "date", "weights_hash"])

    calcular_y_guardar(caja=10_000.0)

    with db.connect(read_only=True) as conn:
        fila = conn.execute(
            "SELECT veredicto, importe_eur, stop FROM recommendations "
            "WHERE ticker = 'BBB'").fetchone()

    assert fila is None
    with db.connect(read_only=True) as conn:
        reason = conn.execute("SELECT reason FROM advisor_diagnostics WHERE ticker='BBB'").fetchone()[0]
    assert "Confirma" in reason


def test_la_calibracion_sale_en_pantalla_y_no_promete_de_mas(almacen):
    """La calibracion mide hacia ATRAS y solo la mitad de precio; el marcador
    mide hacia DELANTE y el asesor entero. Van en bloques separados porque no
    son la misma clase de prueba, y juntarlas las haria parecerlo.

    Sin ranking historico —el caso del primer dia— la pantalla tiene que decir
    como generarlo, no quedarse en blanco.
    """
    prueba = _pintar()

    texto = " ".join(str(m.value) for m in prueba.markdown)
    assert "--history" in texto or "historico" in texto


def test_la_calibracion_se_niega_con_un_perfil_de_fundamentales(almacen):
    """LA NEGATIVA QUE MAS IMPORTA, comprobada desde el comando real.

    `balanced` es el perfil por defecto y lleva valor, crecimiento, calidad y
    dividendo. De esos no hay serie punto-en-el-tiempo, asi que un t-stat sobre
    ellos mide supervivencia, no la estrategia.
    """
    from stocks_tracker.compute.run_advice import calibrar

    resultado = calibrar(preset="balanced")

    assert not resultado.solo_precio
    assert not resultado.concluyente


def test_se_puede_preguntar_por_que_sin_abrir_la_base_de_datos(almacen, capsys):
    """POR QUE EXISTE ESTE COMANDO.

    Cuando el usuario reporto que MSFT y NVDA le salian como REDUCIR, la unica
    forma de diagnosticarlo era teclear SQL con comillas anidadas dentro de
    `cmd`. No funciono, y se perdio un intercambio entero peleando con el
    escapado en vez de con el problema.

    Una pantalla que da consejos tiene que poder explicar cualquiera de ellos.
    """
    from stocks_tracker.compute.run_advice import por_que

    _sembrar_cartera()

    assert por_que("aaa") == 0, "no encuentra la posicion (¿distingue mayusculas?)"

    salida = capsys.readouterr().out
    assert "profit_margin" in salida, "no ensena los numeros crudos"
    assert "8" in salida and "24" in salida, "no ensena el antes y el despues"
    assert "margen" in salida.lower(), "no ensena el motivo"


def test_preguntar_por_algo_que_no_tienes_lo_dice(almacen, capsys):
    from stocks_tracker.compute.run_advice import por_que

    assert por_que("ZZZZ") == 1
    assert "no esta en tu cartera" in capsys.readouterr().out


def test_consulta_sin_propuestas_no_reutiliza_las_anteriores(almacen, monkeypatch):
    from stocks_tracker.app import data_access as da
    from stocks_tracker.compute.run_advice import calcular_y_guardar
    from stocks_tracker.core import advice_build

    _sembrar_cartera()
    assert calcular_y_guardar() > 0
    assert not da.get_advice().empty
    monkeypatch.setattr(advice_build, "de_la_cartera", lambda *a, **kw: [])
    assert calcular_y_guardar() == 0
    st.cache_data.clear()
    assert da.get_advice().empty
    with db.connect(read_only=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0] > 0


def test_actualizar_solo_cuando_se_pulsa_un_boton(almacen, monkeypatch):
    from unittest.mock import Mock

    from stocks_tracker.app import research_actions

    refresh = Mock()
    monkeypatch.setattr(research_actions, "refresh_research", refresh)
    prueba = _pintar()
    refresh.assert_not_called()
    next(x for x in prueba.number_input if x.label == "Efectivo disponible para esta consulta (EUR)").set_value(1500.0)
    next(x for x in prueba.button if x.label == "Recalcular asesor con los datos guardados").click().run()
    assert not prueba.exception
    refresh.assert_called_once_with(cash=1500.0, download=False)
    prueba.run()
    assert not prueba.exception
    assert refresh.call_count == 1


def test_no_se_propone_comprar_el_valor_que_se_recomienda_vender(almacen, monkeypatch):
    from stocks_tracker.compute.run_advice import calcular_y_guardar
    from stocks_tracker.core import advice_build
    from stocks_tracker.core.scoring import preset_hash

    _sembrar_cartera()
    with db.connect() as conn:
        db.upsert_df(conn, "factor_scores", pd.DataFrame([{
            "ticker": "AAA", "date": HOY, "weights_hash": preset_hash("balanced"),
            "composite": 2.0, "composite_pctile": .99, "coverage": .99,
        }]), keys=["ticker", "date", "weights_hash"])
    received = []

    def candidates(ranking, **kwargs):
        received.extend(ranking["ticker"].tolist())
        return []

    monkeypatch.setattr(advice_build, "de_los_candidatos", candidates)
    assert calcular_y_guardar(caja=1500.) > 0
    assert "AAA" not in received
