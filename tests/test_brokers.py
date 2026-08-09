"""Tests de la importacion de carteras.

El fallo peligroso aqui no es que la importacion falle —eso se ve— sino que
funcione y meta numeros equivocados: un "1.234,56" leido como 1,23 te deja una
cartera con un coste medio inventado y todo lo que se calcula encima queda mal
sin que nada avise.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from stocks_tracker.core import brokers


def csv_bytes(text: str, encoding: str = "utf-8") -> bytes:
    return text.encode(encoding)


# ---------------------------------------------------------------------------
# Numeros: formato europeo frente a anglosajon
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1234.56", 1234.56),
        ("1,234.56", 1234.56),      # anglosajon
        ("1.234,56", 1234.56),      # europeo
        ("1234,56", 1234.56),
        ("€ 1.234,56", 1234.56),
        ("$1,234.56", 1234.56),
        ("-45,20", -45.20),
        ("0,5", 0.5),
        (12.5, 12.5),
        (3, 3.0),
    ],
)
def test_number_parsing_handles_both_conventions(raw, expected):
    assert brokers._to_number(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", [None, "", "  ", "-", "n/a", float("nan")])
def test_unparseable_numbers_return_none(raw):
    assert brokers._to_number(raw) is None


@pytest.mark.parametrize("raw", ["1.234", "1,234", "12.345", "-9,876"])
def test_thousand_separator_ambiguity_is_flagged(raw):
    """«1.234» son 1234 para un europeo y 1,234 para un anglosajon.

    No se puede resolver mirando el valor: se lee como decimal y se avisa,
    porque equivocarse multiplica la cartera por mil.
    """
    assert brokers.is_ambiguous_number(raw)


@pytest.mark.parametrize("raw", ["1.23", "1.2345", "1.234,56", "1,234.56", "150.50"])
def test_unambiguous_numbers_are_not_flagged(raw):
    assert not brokers.is_ambiguous_number(raw)


def test_ambiguous_price_produces_a_warning():
    frame = pd.DataFrame({"Symbol": ["AAPL"], "Quantity": [10], "Price": ["1.234"]})
    result = brokers.parse_positions(frame)
    assert any("ambiguos" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Lectura del fichero
# ---------------------------------------------------------------------------
def test_reads_semicolon_separated_latin1():
    """Los extractos europeos llegan asi mas veces de lo razonable."""
    data = csv_bytes("Nombre;Cantidad;Precio medio\nTelefónica;10;3,95\n", "latin-1")
    frame = brokers.read_table(data, "extracto.csv")
    assert list(frame.columns) == ["Nombre", "Cantidad", "Precio medio"]
    assert len(frame) == 1


def test_reads_plain_comma_csv():
    data = csv_bytes("Symbol,Quantity,Average Price\nAAPL,10,150.5\n")
    frame = brokers.read_table(data, "positions.csv")
    assert len(frame) == 1


def test_unreadable_file_returns_empty_not_an_exception():
    assert brokers.read_table(b"\x00\x01\x02", "roto.csv").empty


# ---------------------------------------------------------------------------
# Deteccion de columnas
# ---------------------------------------------------------------------------
def test_exact_match_wins_over_substring():
    """'price' es subcadena de 'average price'. Sin prioridad al exacto, el
    precio actual le ganaria al precio medio de compra, que es el que importa.
    """
    mapping = brokers.guess_column_map(["Average Price", "Price", "Quantity"])
    assert mapping["avg_cost"] == "Average Price"


def test_recognises_spanish_headers():
    mapping = brokers.guess_column_map(
        ["Nombre", "Cantidad", "Precio medio", "Divisa", "ISIN"]
    )
    assert mapping["name"] == "Nombre"
    assert mapping["qty"] == "Cantidad"
    assert mapping["avg_cost"] == "Precio medio"
    assert mapping["currency"] == "Divisa"
    assert mapping["isin"] == "ISIN"


def test_recognises_accented_headers():
    assert brokers.guess_column_map(["Descripción"])["name"] == "Descripción"


def test_a_column_is_not_assigned_twice():
    mapping = brokers.guess_column_map(["Price", "Quantity"])
    assert len(set(mapping.values())) == len(mapping)


def test_detects_etoro_by_its_characteristic_column():
    assert brokers.detect_broker(["Symbol", "Average Open Rate", "Units"]) == "eToro"


def test_detects_trade_republic_by_isin():
    assert brokers.detect_broker(["ISIN", "Cantidad", "Precio"]) == "Trade Republic"


# ---------------------------------------------------------------------------
# ISIN
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("value", ["US0378331005", "ES0113900J37", "IE00B4L5Y983"])
def test_valid_isins_are_recognised(value):
    assert brokers.looks_like_isin(value)


@pytest.mark.parametrize("value", ["AAPL", "", None, "US03783310", "1234567890AB"])
def test_non_isins_are_rejected(value):
    assert not brokers.looks_like_isin(value)


def test_shipped_isin_map_is_well_formed():
    mapping = brokers.isin_map()
    assert mapping, "config/isin_map.yaml no trae ninguna equivalencia"
    for isin, ticker in mapping.items():
        assert brokers.looks_like_isin(isin), f"ISIN invalido en el YAML: {isin}"
        assert ticker and " " not in ticker, f"Ticker invalido para {isin}"


def test_isin_is_resolved_to_a_ticker():
    frame = pd.DataFrame(
        {"ISIN": ["US0378331005"], "Cantidad": [10], "Precio medio": ["150,50"]}
    )
    result = brokers.parse_positions(frame)
    assert result.positions["ticker"].tolist() == ["AAPL"]


def test_unknown_isin_is_reported_not_silently_dropped():
    frame = pd.DataFrame(
        {"ISIN": ["XX0000000000"], "Cantidad": [10], "Precio medio": ["1,00"]}
    )
    result = brokers.parse_positions(frame)
    assert result.positions.empty
    assert len(result.unresolved) == 1
    assert any("sin equivalencia" in w for w in result.warnings)


def test_isin_in_the_symbol_column_is_detected():
    """Algunos extractos meten el ISIN donde deberia ir el simbolo."""
    frame = pd.DataFrame(
        {"Symbol": ["US0378331005"], "Quantity": [5], "Average Price": [100.0]}
    )
    result = brokers.parse_positions(frame)
    assert result.positions["ticker"].tolist() == ["AAPL"]


# ---------------------------------------------------------------------------
# Agregacion de lotes
# ---------------------------------------------------------------------------
def test_lots_of_the_same_stock_merge_with_weighted_cost():
    """Los brokers listan cada compra por separado. La media simple mentiria."""
    frame = pd.DataFrame(
        {
            "Symbol": ["AAPL", "AAPL"],
            "Quantity": [10, 30],
            "Average Price": [100.0, 200.0],
        }
    )
    result = brokers.parse_positions(frame)

    assert len(result.positions) == 1
    row = result.positions.iloc[0]
    assert row["qty"] == 40
    # (10x100 + 30x200) / 40 = 175, no la media simple de 150.
    assert row["avg_cost"] == pytest.approx(175.0)


def test_rows_without_quantity_or_price_are_skipped():
    frame = pd.DataFrame(
        {
            "Symbol": ["AAPL", "MSFT", "NVDA"],
            "Quantity": [10, 0, 5],
            "Average Price": [100.0, 50.0, None],
        }
    )
    result = brokers.parse_positions(frame)
    assert result.positions["ticker"].tolist() == ["AAPL"]


def test_negative_quantity_is_skipped():
    """Una posicion corta no se representa aqui; meterla como larga mentiria."""
    frame = pd.DataFrame(
        {"Symbol": ["AAPL"], "Quantity": [-10], "Average Price": [100.0]}
    )
    assert brokers.parse_positions(frame).positions.empty


# ---------------------------------------------------------------------------
# Extractos completos
# ---------------------------------------------------------------------------
def test_etoro_like_export():
    data = csv_bytes(
        "Instrument,Symbol,Units,Average Open Rate,Currency\n"
        "Apple Inc,AAPL,12.5,178.30,USD\n"
        "Tesla,TSLA,4,241.10,USD\n"
    )
    frame = brokers.read_table(data, "etoro.csv")
    result = brokers.parse_positions(frame)

    assert result.broker == "eToro"
    assert sorted(result.positions["ticker"]) == ["AAPL", "TSLA"]
    assert result.positions.set_index("ticker").loc["AAPL", "qty"] == 12.5


def test_trade_republic_like_export():
    data = csv_bytes(
        "ISIN;Nombre;Cantidad;Precio medio;Divisa\n"
        "US0378331005;Apple;3;168,42;EUR\n"
        "IE00B4L5Y983;iShares Core MSCI World;25;89,15;EUR\n",
        "latin-1",
    )
    frame = brokers.read_table(data, "traderepublic.csv")
    result = brokers.parse_positions(frame, default_currency="EUR")

    assert result.broker == "Trade Republic"
    assert sorted(result.positions["ticker"]) == ["AAPL", "IWDA.AS"]
    assert result.positions["currency"].unique().tolist() == ["EUR"]
    assert result.positions.set_index("ticker").loc["AAPL", "avg_cost"] == pytest.approx(
        168.42
    )


def test_xlsx_export_is_read():
    frame = pd.DataFrame(
        {"Symbol": ["AAPL"], "Units": [10], "Average Open Rate": [150.0]}
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, sheet_name="Positions")

    parsed = brokers.read_table(buffer.getvalue(), "extracto.xlsx")
    assert brokers.parse_positions(parsed).positions["ticker"].tolist() == ["AAPL"]


def test_closed_positions_sheet_is_not_preferred():
    """eToro reparte el extracto en hojas. La cartera es la de posiciones
    abiertas, no la de cerradas."""
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer) as writer:
        pd.DataFrame(
            {"Symbol": ["OLD"], "Units": [1], "Average Open Rate": [10.0]}
        ).to_excel(writer, index=False, sheet_name="Closed Positions")
        pd.DataFrame(
            {"Symbol": ["AAPL"], "Units": [5], "Average Open Rate": [150.0]}
        ).to_excel(writer, index=False, sheet_name="Positions")

    parsed = brokers.read_table(buffer.getvalue(), "etoro.xlsx")
    assert parsed["Symbol"].tolist() == ["AAPL"]


# ---------------------------------------------------------------------------
# Degradacion
# ---------------------------------------------------------------------------
def test_empty_file_explains_itself():
    result = brokers.parse_positions(pd.DataFrame())
    assert not result.ok
    assert result.warnings


def test_missing_quantity_column_says_which_one():
    frame = pd.DataFrame({"Symbol": ["AAPL"], "Average Price": [100.0]})
    result = brokers.parse_positions(frame)
    assert not result.ok
    assert any("cantidad" in w for w in result.warnings)


def test_file_without_ticker_or_isin_is_rejected():
    frame = pd.DataFrame({"Cantidad": [10], "Precio medio": [100.0]})
    result = brokers.parse_positions(frame)
    assert not result.ok
    assert any("ticker ni ISIN" in w for w in result.warnings)


def test_transaction_history_instead_of_positions_is_explained():
    """Un error facil de cometer: exportar operaciones en vez de la cartera."""
    frame = pd.DataFrame({"Symbol": ["AAPL"], "Quantity": [0], "Price": [0]})
    result = brokers.parse_positions(frame)
    assert any("historial de operaciones" in w for w in result.warnings)


def test_manual_column_map_overrides_the_guess():
    frame = pd.DataFrame(
        {"col_a": ["AAPL"], "col_b": [10], "col_c": [150.0]}
    )
    result = brokers.parse_positions(
        frame, column_map={"ticker": "col_a", "qty": "col_b", "avg_cost": "col_c"}
    )
    assert result.positions["ticker"].tolist() == ["AAPL"]


def test_broker_symbol_outside_our_universe_falls_back_to_isin():
    """eToro usa nombres propios para algunos productos."""
    frame = pd.DataFrame(
        {
            "Symbol": ["APPLE.CFD"],
            "ISIN": ["US0378331005"],
            "Quantity": [10],
            "Average Price": [150.0],
        }
    )
    result = brokers.parse_positions(frame, known_tickers={"AAPL", "MSFT"})
    assert result.positions["ticker"].tolist() == ["AAPL"]
