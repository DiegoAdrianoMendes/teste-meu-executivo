"""S29: exceção do SQLAlchemy não despeja parâmetros (CPF, telefone) no log."""

from sqlalchemy.exc import DBAPIError

import app.db.session as session_module


def test_engines_hide_parameters():
    assert session_module.engine.sync_engine.hide_parameters is True
    assert session_module._worker_engine.sync_engine.hide_parameters is True


def test_dbapi_error_message_hides_parameters():
    err = DBAPIError.instance(
        "INSERT INTO clients (cpf) VALUES (?)",
        ("123.456.789-00",),
        Exception("duplicate"),
        Exception,
        hide_parameters=True,
    )
    assert "123.456.789-00" not in str(err)
    assert "hidden" in str(err)
