import pytest
from pydantic import ValidationError
from antwork.models import BatchInput


def test_valid_secrets_hidden(payload):
    value = BatchInput(**payload)
    assert value.accounts[0].password.get_secret_value() == "test:password"
    assert "test:password" not in repr(value)
    assert "4242424242424242" not in repr(value)


@pytest.mark.parametrize("field,value",[("cvv","12"),("number","4242424242424241"),("expiry","13/39"),("expiry","01/20")])
def test_invalid_card(payload,field,value):
    payload["card"][field] = value
    with pytest.raises(ValidationError): BatchInput(**payload)


def test_duplicate_and_spending_limit(payload):
    payload["total_limit_usd"] = "19.99"
    with pytest.raises(ValidationError): BatchInput(**payload)
    payload["total_limit_usd"] = "20.00"
    payload["accounts"][1]["email"] = payload["accounts"][0]["email"].upper()
    with pytest.raises(ValidationError): BatchInput(**payload)
