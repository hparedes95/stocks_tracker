"""Tests de las credenciales.

El mas importante no es que se lean, sino que NO se impriman. Una clave de API
se revoca; una clave privada de wallet no. Si aparece en una traza o en un
fichero de registro, lo unico que se puede hacer es mover los fondos.
"""

from __future__ import annotations

import pytest

from stocks_tracker.core import secrets


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Ninguna credencial real, ni el .env del proyecto."""
    for cred in secrets.CREDENTIALS:
        monkeypatch.delenv(cred.env, raising=False)
    monkeypatch.setattr(secrets, "project_root", lambda: tmp_path)
    secrets.load_env.cache_clear()
    yield
    secrets.load_env.cache_clear()


# ---------------------------------------------------------------------------
# Que el .env se lea
# ---------------------------------------------------------------------------
def test_the_env_file_is_actually_read(tmp_path, monkeypatch):
    """`python-dotenv` estaba declarado como dependencia y no se llamaba en
    ningun sitio. Se podia rellenar el .env entero y no pasaba nada: la clave
    de FRED y el token de Telegram nunca llegaron al proceso."""
    (tmp_path / ".env").write_text("FRED_API_KEY=abc123def456\n", encoding="utf-8")
    secrets.load_env.cache_clear()

    assert secrets.get("FRED_API_KEY") == "abc123def456"


def test_the_environment_wins_over_the_file(tmp_path, monkeypatch):
    """Permite ejecutar algo puntualmente con otra credencial sin editar el
    fichero ni arriesgarse a dejarla escrita."""
    (tmp_path / ".env").write_text("FRED_API_KEY=del_fichero\n", encoding="utf-8")
    monkeypatch.setenv("FRED_API_KEY", "del_entorno")
    secrets.load_env.cache_clear()

    assert secrets.get("FRED_API_KEY") == "del_entorno"


def test_a_missing_env_file_is_not_an_error():
    """El dashboard funciona sin ninguna credencial."""
    assert secrets.load_env() is None
    assert secrets.get("FRED_API_KEY", required=False) == ""


# ---------------------------------------------------------------------------
# Que no se escapen
# ---------------------------------------------------------------------------
def test_a_secret_never_appears_in_an_error_message(monkeypatch):
    monkeypatch.setenv("KRAKEN_API_SECRET", "clave-secretisima-de-kraken")
    with pytest.raises(secrets.MissingCredential) as exc:
        secrets.get("KRAKEN_API_KEY")

    assert "clave-secretisima" not in str(exc.value)
    # Y el mensaje si dice como arreglarlo.
    assert "Kraken > Settings > API" in str(exc.value)


def test_redaction_covers_every_declared_credential(monkeypatch):
    """El fallo del diseno anterior: una lista de tres nombres escrita a mano,
    que se quedo atras en cuanto se anadieron credenciales nuevas. Es lo que
    siempre pasa con las listas escritas a mano."""
    for i, cred in enumerate(secrets.CREDENTIALS):
        monkeypatch.setenv(cred.env, f"valor-secreto-numero-{i}-largo")
    secrets.load_env.cache_clear()

    texto = " ".join(f"error con {c.value}" for c in secrets.CREDENTIALS)
    limpio = secrets.redact(texto)

    for cred in secrets.CREDENTIALS:
        assert cred.value not in limpio, f"{cred.env} se ha colado en el texto"
        assert f"<{cred.env}>" in limpio


def test_the_alert_channel_uses_the_shared_redaction(monkeypatch):
    """notify.py registra errores de envio; un token dentro seria permanente."""
    from stocks_tracker.alerts import notify

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:token-de-telegram-real")
    secrets.load_env.cache_clear()

    limpio = notify._redact("fallo al enviar con 123456:token-de-telegram-real")
    assert "token-de-telegram-real" not in limpio


def test_short_values_are_not_substituted(monkeypatch):
    """Reemplazar una cadena de tres caracteres destrozaria el mensaje sin
    proteger nada: eso no es un secreto."""
    monkeypatch.setenv("FRED_API_KEY", "abc")
    secrets.load_env.cache_clear()
    assert secrets.redact("el abecedario empieza por abc") == "el abecedario empieza por abc"


# ---------------------------------------------------------------------------
# Diagnostico
# ---------------------------------------------------------------------------
def test_a_venue_without_credentials_is_not_ready():
    listo, faltan = secrets.venue_ready("kraken")
    assert not listo
    assert set(faltan) == {"KRAKEN_API_KEY", "KRAKEN_API_SECRET"}


def test_a_venue_with_credentials_is_ready(monkeypatch):
    monkeypatch.setenv("KRAKEN_API_KEY", "una-clave-larga")
    monkeypatch.setenv("KRAKEN_API_SECRET", "un-secreto-largo")
    secrets.load_env.cache_clear()

    listo, faltan = secrets.venue_ready("kraken")
    assert listo and not faltan


def test_optional_credentials_do_not_block_a_venue(monkeypatch):
    """La direccion delegada de Polymarket es opcional: exigirla dejaria el
    venue bloqueado sin motivo."""
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "0xclaveprivadalarga")
    secrets.load_env.cache_clear()

    listo, faltan = secrets.venue_ready("polymarket")
    assert listo, f"bloqueado por {faltan}"


def test_the_dangerous_ones_carry_their_warning():
    """La casilla 'Withdraw Funds' y la clave privada son los dos sitios donde
    un descuido cuesta el dinero entero."""
    kraken = secrets.BY_ENV["KRAKEN_API_KEY"]
    assert "Withdraw Funds" in kraken.danger

    poly = secrets.BY_ENV["POLYMARKET_PRIVATE_KEY"]
    assert "wallet NUEVA" in poly.danger
    assert "rotarla no sirve" in poly.danger


def test_the_report_never_prints_a_value(monkeypatch, capsys):
    monkeypatch.setenv("KRAKEN_API_KEY", "valor-que-no-debe-salir")
    secrets.load_env.cache_clear()

    secrets.main()
    salida = capsys.readouterr().out
    assert "valor-que-no-debe-salir" not in salida
    assert "KRAKEN_API_KEY" in salida
