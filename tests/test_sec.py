"""SEC EDGAR. Lo unico que se puede probar sin red, y la funcion que importa.

TRES ADVERTENCIAS QUE VALEN PARA TODO EL FICHERO

1. La SEC solo cubre EE. UU. BBVA.MC, SAB.MC, UNI.MC y todo el IBEX no estan
   aqui y no van a estar.
2. Nada de esto esta verificado contra la API real: se escribio sin salida a
   internet.
3. Y por eso NO alimenta el ranking. Un dato malo de Yahoo suele ser evidente
   —un margen del 900 %—. Un dato malo de aqui seria una etiqueta XBRL mal
   mapeada: `Revenues` y `RevenueFromContractWithCustomerExcludingAssessedTax`
   dan cifras distintas y las dos plausibles. Un numero creible y equivocado
   alimentando el ranking es justo el fallo que este proyecto existe para
   evitar.

Lo que si aporta, y no lo da nadie mas, es la FECHA DE PUBLICACION.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from stocks_tracker.providers.sec_provider import (
    CONCEPTOS,
    SecProvider,
    interpretar_concepto,
    lo_sabido_en,
)

# Forma real de una respuesta de `companyconcept`, recortada.
CONCEPTO = {
    "cik": 320193, "taxonomy": "us-gaap", "tag": "NetIncomeLoss",
    "units": {
        "USD": [
            {"start": "2023-01-01", "end": "2023-12-31", "val": 96995000000,
             "accn": "0000320193-24-000006", "fy": 2023, "fp": "FY",
             "form": "10-K", "filed": "2024-02-02"},
            {"start": "2024-01-01", "end": "2024-12-31", "val": 93736000000,
             "accn": "0000320193-25-000008", "fy": 2024, "fp": "FY",
             "form": "10-K", "filed": "2025-01-31"},
        ]
    },
}


# ---------------------------------------------------------------------------
# Interpretar
# ---------------------------------------------------------------------------

def test_se_sacan_los_hechos_con_su_fecha_de_publicacion():
    filas = interpretar_concepto(CONCEPTO, "AAPL", "beneficio_neto", "NetIncomeLoss")

    assert len(filas) == 2
    assert filas[0]["publicado"] == date(2024, 2, 2)
    assert filas[0]["fin_periodo"] == date(2023, 12, 31)
    assert filas[0]["valor"] == pytest.approx(96_995_000_000)


def test_un_hecho_sin_fecha_de_publicacion_se_descarta():
    """Es lo unico que hace especial a esta fuente. Guardarlo con la fecha de
    hoy seria inventarsela."""
    sin_filed = {"units": {"USD": [{"end": "2023-12-31", "val": 1000}]}}

    assert interpretar_concepto(sin_filed, "AAPL", "x", "Y") == []


def test_las_unidades_raras_se_descartan():
    """XBRL mezcla magnitudes distintas bajo el mismo concepto: USD, USD por
    accion, porcentajes. Juntarlas daria una serie sin sentido."""
    mezclado = {"units": {
        "USD": [{"end": "2023-12-31", "val": 100, "filed": "2024-02-02"}],
        "USD/shares": [{"end": "2023-12-31", "val": 6.1, "filed": "2024-02-02"}],
    }}

    filas = interpretar_concepto(mezclado, "AAPL", "x", "Y")

    assert len(filas) == 1
    assert filas[0]["valor"] == 100


def test_una_respuesta_vacia_no_revienta():
    assert interpretar_concepto({}, "AAPL", "x", "Y") == []
    assert interpretar_concepto({"units": {}}, "AAPL", "x", "Y") == []


# ---------------------------------------------------------------------------
# LA funcion: que se sabia en una fecha
# ---------------------------------------------------------------------------

def hechos() -> pd.DataFrame:
    return pd.DataFrame(interpretar_concepto(CONCEPTO, "AAPL", "beneficio_neto",
                                             "NetIncomeLoss"))


def test_se_filtra_por_fecha_de_publicacion_y_no_por_fin_de_periodo():
    """EL fallo clasico del backtest de fundamentales.

    El cierre de 2023 termina el 31 de diciembre y no se publica hasta el 2 de
    febrero de 2024. Filtrando por el fin del periodo, un backtest usaria en
    enero un balance que nadie conocia hasta seis semanas despues.
    """
    en_enero = lo_sabido_en(hechos(), date(2024, 1, 15))

    assert en_enero.empty, (
        "en enero de 2024 se esta usando el cierre de 2023, que no se publico "
        "hasta febrero"
    )


def test_despues_de_publicarse_ya_se_puede_usar():
    """El contrario, para que el de arriba no pase por el motivo equivocado."""
    en_marzo = lo_sabido_en(hechos(), date(2024, 3, 1))

    assert len(en_marzo) == 1
    assert en_marzo.iloc[0]["valor"] == pytest.approx(96_995_000_000)


def test_se_coge_la_publicacion_mas_reciente_de_cada_campo():
    """Lo que un inversor tendria delante ese dia."""
    en_2025 = lo_sabido_en(hechos(), date(2025, 6, 1))

    assert len(en_2025) == 1
    assert en_2025.iloc[0]["valor"] == pytest.approx(93_736_000_000)


def test_sin_hechos_no_se_inventa_nada():
    assert lo_sabido_en(pd.DataFrame(), date(2025, 1, 1)).empty


# ---------------------------------------------------------------------------
# Los limites de la fuente
# ---------------------------------------------------------------------------

def test_no_se_pregunta_por_empresas_europeas(monkeypatch):
    """Gastaria una peticion para recibir un 404. La SEC regula a las empresas
    que cotizan en EE. UU.; el IBEX no esta y no va a estar."""
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "yo@ejemplo.com")
    p = SecProvider()

    assert p.supports("AAPL")
    assert not p.supports("BBVA.MC")
    assert not p.supports("SAB.MC")
    assert not p.supports("EURUSD=X")


def test_sin_correo_de_contacto_no_se_pregunta_nada(monkeypatch):
    """La SEC bloquea por IP a quien no se identifica. No es una sugerencia de
    su documentacion: es la condicion de uso."""
    monkeypatch.delenv("SEC_CONTACT_EMAIL", raising=False)
    p = SecProvider()

    assert not p.configurado
    assert not p.supports("AAPL")


def test_el_user_agent_lleva_el_contacto(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "yo@ejemplo.com")

    cabeceras = SecProvider()._cabeceras()

    assert "yo@ejemplo.com" in cabeceras["User-Agent"]
    assert "stocks_tracker" in cabeceras["User-Agent"]


def test_cada_campo_tiene_alternativas_en_orden():
    """La misma magnitud tiene etiquetas distintas segun el sector y el ano.
    Coger la equivocada da una cifra plausible y falsa."""
    assert len(CONCEPTOS["ingresos"]) > 1
    assert CONCEPTOS["ingresos"][0].startswith("RevenueFromContract")


# ---------------------------------------------------------------------------
# Y NO alimenta el ranking
# ---------------------------------------------------------------------------

def test_la_sec_no_esta_conectada_al_calculo_del_ranking():
    """Guardarrail de la decision de alcance, y es deliberada.

    Lo obvio seria que la fuente oficial sustituyera a Yahoo. El problema es
    COMO falla cada una: un dato malo de Yahoo suele ser evidente; una etiqueta
    XBRL mal mapeada da una cifra plausible que ninguna comprobacion de rango
    distingue.

    Conectarla se hace DESPUES de verificar el mapeo contra cuentas anuales
    reales, a mano. Este test esta para que ese paso no se salte por descuido.
    """
    from stocks_tracker.core.config import project_root

    calculo = (project_root()
               / "src/stocks_tracker/compute/run_compute.py").read_text("utf-8")

    assert "sec_provider" not in calculo
    assert "'sec'" not in calculo and '"sec"' not in calculo


def test_el_proveedor_no_se_declara_comprobado_por_estar_configurado(monkeypatch):
    monkeypatch.setenv("SEC_CONTACT_EMAIL", "yo@ejemplo.com")
    p = SecProvider()

    assert p.configurado
    assert not p.ha_respondido
