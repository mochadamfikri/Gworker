"""Birth-date and local validation tests. Dates are never silently modified."""

import pytest

from familylink.models import ValidationError
from familylink.validation import validate_birth_date


@pytest.mark.parametrize("bad", ["2020-02-30", "not-a-date", "2020-13-01", "", "2020/01/01", "15-04-2015"])
def test_invalid_dates_rejected(bad):
    with pytest.raises(ValidationError):
        validate_birth_date(bad)


def test_future_date_rejected():
    with pytest.raises(ValidationError):
        validate_birth_date("2999-01-01")


def test_absurd_year_rejected():
    with pytest.raises(ValidationError):
        validate_birth_date("1800-01-01")


def test_valid_date_is_preserved():
    assert validate_birth_date("2015-04-23") == "2015-04-23"
    # No silent modification: input already canonical stays identical.
    assert validate_birth_date(" 2010-12-01 ") == "2010-12-01"
