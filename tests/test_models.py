import pytest
from pydantic import ValidationError
from antwork.models import BatchInput, SavedCardTopup
from uuid import uuid4


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


def test_linked_card_topup_requires_amount_and_limit_but_no_card():
    data = {"request_id":str(uuid4()),"amount_usd":"5.00","limit_usd":"6.00"}
    assert SavedCardTopup(**data).amount_usd == 5
    with pytest.raises(ValidationError):
        SavedCardTopup(**{**data,"limit_usd":"4.99"})
    with pytest.raises(ValidationError):
        SavedCardTopup(**data,cvv="123")
