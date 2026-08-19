"""Trazabilidad: de donde salio cada numero.

Un numero guardado sin decir con que codigo, que configuracion y que datos se
calculo es una afirmacion sin autor. Y no se puede reconstruir despues: el
commit de hoy se sabe hoy.

Lo que estos tests vigilan no es que el sello sea bonito, sino que sea ESTABLE
cuando nada cambia y DISTINTO cuando algo cambia. Un hash que baila solo
—porque un diccionario se reordeno o porque 0,1 + 0,2 no da 0,3— convierte la
trazabilidad en ruido: todo aparece como modificado y nadie mira la columna.
"""

from __future__ import annotations

import pandas as pd
import pytest

from stocks_tracker.core import lineage as ln


# ---------------------------------------------------------------------------
# El hash de configuracion
# ---------------------------------------------------------------------------
def test_the_same_configuration_gives_the_same_hash():
    a = {"coste_bps": 10.0, "horizontes": [5, 10, 21]}
    assert ln.config_hash(a) == ln.config_hash(dict(a))


def test_reordering_the_keys_does_not_change_the_hash():
    """Un YAML reordenado es la misma configuracion. Sin `sort_keys`, mover una
    linea de sitio haria que todos los resultados guardados parecieran
    obsoletos."""
    a = {"coste_bps": 10.0, "ambito": "equity_us", "fdr_q": 0.10}
    b = {"fdr_q": 0.10, "coste_bps": 10.0, "ambito": "equity_us"}
    assert ln.config_hash(a) == ln.config_hash(b)


def test_floating_point_noise_does_not_change_the_hash():
    """0,1 + 0,2 no da exactamente 0,3. Sin redondear, el hash cambiaria solo
    entre dos ejecuciones con la misma configuracion."""
    assert ln.config_hash({"peso": 0.1 + 0.2}) == ln.config_hash({"peso": 0.3})


def test_changing_a_value_changes_the_hash():
    """La contraprueba de los tres anteriores: si el hash fuera constante,
    todos pasarian y no serviria para nada."""
    assert ln.config_hash({"coste_bps": 10.0}) != ln.config_hash({"coste_bps": 12.0})


def test_a_tiny_but_real_change_still_changes_the_hash():
    """El redondeo es a nueve decimales: tiene que absorber el ruido del coma
    flotante sin tragarse una diferencia de verdad."""
    assert ln.config_hash({"q": 0.10}) != ln.config_hash({"q": 0.1000001})


def test_adding_a_key_changes_the_hash():
    assert ln.config_hash({"a": 1}) != ln.config_hash({"a": 1, "b": 2})


def test_nested_configuration_is_normalised_too():
    """Los limites de riesgo llegan anidados por venue. Si la normalizacion se
    quedara en el primer nivel, cambiar un limite de cripto no cambiaria el
    hash y el sello mentiria justo donde importa."""
    a = {"venues": {"kraken": {"max_positions": 4, "max_drawdown_pct": 20.0}}}
    b = {"venues": {"kraken": {"max_drawdown_pct": 20.0, "max_positions": 4}}}
    c = {"venues": {"kraken": {"max_drawdown_pct": 25.0, "max_positions": 4}}}
    assert ln.config_hash(a) == ln.config_hash(b)
    assert ln.config_hash(a) != ln.config_hash(c)


def test_true_and_one_are_not_the_same_configuration():
    """`True == 1` para Python. Para una configuracion no lo son.

    Lo garantiza `json.dumps`, que escribe `true` y `1`, no la normalizacion:
    ahi habia una guarda explicita para booleanos y era codigo muerto, porque
    `isinstance(True, float)` es False y nunca entraban en el redondeo.
    """
    assert ln.config_hash({"activo": True}) != ln.config_hash({"activo": 1})


# ---------------------------------------------------------------------------
# El commit
# ---------------------------------------------------------------------------
def test_the_commit_is_a_short_hash_or_says_it_does_not_know():
    commit = ln.git_commit()
    assert commit
    if commit != ln.SIN_GIT:
        base = commit.removesuffix("-sucio")
        assert len(base) == 12
        assert all(c in "0123456789abcdef" for c in base)


def test_a_dirty_tree_is_marked_as_such():
    """Con cambios sin confirmar, el commit no describe el codigo que corrio.
    Decirlo es mas util que dar un identificador que no corresponde, y mientras
    se desarrolla es el caso normal."""
    import subprocess

    from stocks_tracker.core.lineage import git_commit

    git_commit.cache_clear()
    sucio = subprocess.run(["git", "status", "--porcelain"],
                           capture_output=True, text=True).stdout.strip()
    commit = git_commit()
    if commit != ln.SIN_GIT:
        assert commit.endswith("-sucio") == bool(sucio)


def test_the_absence_of_git_does_not_crash(monkeypatch):
    """Una instalacion desde un zip no tiene git. Un programa que no arranca
    porque no puede saber su version es peor que uno que no la sabe."""
    import subprocess

    def explota(*a, **k):
        raise OSError("git no existe")

    ln.git_commit.cache_clear()
    monkeypatch.setattr(subprocess, "run", explota)
    assert ln.git_commit() == ln.SIN_GIT
    ln.git_commit.cache_clear()


# ---------------------------------------------------------------------------
# El sello completo
# ---------------------------------------------------------------------------
def test_the_stamp_carries_everything_needed_to_reproduce():
    s = ln.sellar({"coste_bps": 10.0},
                  data_from=pd.Timestamp("2016-08-12").date(),
                  data_to=pd.Timestamp("2026-08-11").date(),
                  n_rows=530_737)
    d = s.as_dict()
    assert set(d) == {"git_commit", "config_hash", "data_from", "data_to", "n_rows"}
    assert d["data_from"] == "2016-08-12"
    assert d["n_rows"] == 530_737


def test_the_dates_are_stored_as_text():
    """Van a una columna VARCHAR junto al resto. Una fecha convertida por tres
    capas distintas acaba con tres formatos distintos en la misma columna."""
    s = ln.sellar({}, data_from=pd.Timestamp("2020-01-01"), data_to=None)
    assert isinstance(s.data_from, str)
    assert s.data_to is None


def test_a_stamp_without_dates_does_not_invent_them():
    s = ln.sellar({})
    assert s.data_from is None and s.data_to is None and s.n_rows == 0


def test_the_description_is_readable():
    s = ln.sellar({"a": 1}, data_from="2020-01-01", data_to="2024-01-01",
                  n_rows=1234)
    texto = ln.describir(s)
    assert "2020-01-01 a 2024-01-01" in texto
    assert "1.234" in texto


def test_the_description_of_a_stamp_without_a_period_does_not_lie():
    assert "sin periodo" in ln.describir(ln.sellar({"a": 1}))


# ---------------------------------------------------------------------------
# Que llegue a la tabla
# ---------------------------------------------------------------------------
@pytest.fixture
def almacen(tmp_path, monkeypatch):
    from stocks_tracker.core import db

    class Stub:
        warehouse_path = tmp_path / "test.duckdb"

    monkeypatch.setattr(db, "get_settings", lambda: Stub())
    db.migrate()
    return db


def test_signal_evidence_has_room_for_the_stamp(almacen):
    """Sin las columnas, `upsert_df` recorta el payload en silencio y el sello
    se perderia sin dar ningun error, que es la peor forma de perderlo."""
    with almacen.connect(read_only=True) as conn:
        columnas = {r[1] for r in
                    conn.execute("PRAGMA table_info('signal_evidence')").fetchall()}
    assert {"git_commit", "config_hash", "data_from", "data_to", "n_rows"} <= columnas


def test_gate_reports_has_room_for_the_stamp(almacen):
    with almacen.connect(read_only=True) as conn:
        columnas = {r[1] for r in
                    conn.execute("PRAGMA table_info('gate_reports')").fetchall()}
    assert {"git_commit", "config_hash", "n_rows"} <= columnas


def test_the_stamp_survives_a_round_trip_through_the_warehouse(almacen):
    sello = ln.sellar({"coste_bps": 10.0}, data_from="2016-08-12",
                      data_to="2026-08-11", n_rows=530_737)
    fila = pd.DataFrame([{
        "signal_id": "TEST", "scope": "equity_us", "horizon_days": 21,
        "evidence": "validada", **sello.as_dict(),
    }])
    with almacen.connect() as conn:
        almacen.upsert_df(conn, "signal_evidence", fila,
                          keys=["signal_id", "scope", "horizon_days"])
        leido = conn.execute(
            "SELECT git_commit, config_hash, data_from, data_to, n_rows "
            "FROM signal_evidence WHERE signal_id = 'TEST'"
        ).fetchone()
    assert leido == (sello.git_commit, sello.config_hash, "2016-08-12",
                     "2026-08-11", 530_737)
