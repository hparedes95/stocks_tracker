from __future__ import annotations

from datetime import date

import pytest

from stocks_tracker.ingest.run_audit import _contrastes_disponibles
from stocks_tracker.providers.alpha_vantage_provider import (
    AlphaVantageProvider,
    interpretar,
)
from stocks_tracker.providers.base import ProviderError, RateLimitError

PAYLOAD = {
    "Meta Data": {"2. Symbol": "AAPL"},
    "Time Series (Daily)": {
        "2026-09-18": {
            "1. open": "230.10", "2. high": "232.40", "3. low": "229.55",
            "4. close": "231.50", "5. volume": "48213900",
        },
        "2026-09-17": {
            "1. open": "228.00", "2. high": "230.90", "3. low": "227.80",
            "4. close": "230.05", "5. volume": "39112400",
        },
    },
}


def test_interpreta_y_limita_el_intervalo_solicitado():
    frame = interpretar(PAYLOAD, "AAPL", date(2026, 9, 18), date(2026, 9, 18))

    assert len(frame) == 1
    assert frame.iloc[0]["close"] == "231.50"
    assert frame.iloc[0]["adj_close"] == "231.50"


def test_los_mensajes_de_cuota_no_se_confunden_con_un_vacio():
    with pytest.raises(RateLimitError):
        interpretar({"Information": "25 requests per day"}, "AAPL",
                    date(2026, 9, 1), date(2026, 9, 20))


def test_un_simbolo_invalido_es_un_error_del_proveedor():
    with pytest.raises(ProviderError):
        interpretar({"Error Message": "Invalid API call"}, "XXX",
                    date(2026, 9, 1), date(2026, 9, 20))


def test_sin_clave_no_declara_cobertura(monkeypatch):
    monkeypatch.delenv("ALPHA_VANTAGE_API_KEY", raising=False)
    assert not AlphaVantageProvider().supports("AAPL")


def test_la_version_gratuita_se_reserva_para_simbolos_us_simples(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "prueba")
    provider = AlphaVantageProvider()

    assert provider.supports("AAPL")
    assert not provider.supports("SAN.MC")
    assert not provider.supports("EURUSD=X")
    assert not provider.supports("BTC-USD")


def test_con_clave_entra_sola_en_la_auditoria(monkeypatch):
    monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "prueba")

    assert _contrastes_disponibles({"providers": ["stooq"]}) == [
        "stooq", "alpha_vantage"
    ]


def test_no_se_duplica_si_ya_estaba_configurada(monkeypatch):
    monkeypatch.setenv("ALPHA_VANTAGE_API_KEY", "prueba")

    assert _contrastes_disponibles({"providers": ["alpha_vantage"]}) == [
        "alpha_vantage"
    ]
