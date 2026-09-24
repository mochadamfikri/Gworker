from datetime import datetime, timezone
from enum import StrEnum
from decimal import Decimal
import re
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class Status(StrEnum):
    queued = "queued"
    running = "running"
    attention = "need_attention"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"
    interrupted = "interrupted"


TERMINAL = {Status.completed, Status.failed, Status.cancelled, Status.interrupted}


class Account(StrictModel):
    email: str = Field(min_length=3, max_length=254)
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
            raise ValueError("Email tidak valid")
        return value


class Card(StrictModel):
    number: SecretStr
    expiry: str
    cvv: SecretStr
    holder: SecretStr = Field(min_length=1, max_length=200)

    @field_validator("number")
    @classmethod
    def validate_number(cls, value):
        number = re.sub(r"[ -]", "", value.get_secret_value())
        if not re.fullmatch(r"4\d{12}(?:\d{3})?(?:\d{3})?", number):
            raise ValueError("Nomor Visa tidak valid")
        digits = [int(c) for c in number[::-1]]
        if sum(d if i % 2 == 0 else (d * 2 - 9 if d >= 5 else d * 2)
               for i, d in enumerate(digits)) % 10:
            raise ValueError("Nomor Visa tidak valid")
        return SecretStr(number)

    @field_validator("cvv")
    @classmethod
    def validate_cvv(cls, value):
        if not re.fullmatch(r"\d{3}", value.get_secret_value()):
            raise ValueError("CVV harus 3 angka")
        return value

    @field_validator("expiry")
    @classmethod
    def validate_expiry(cls, value):
        if not re.fullmatch(r"(0[1-9]|1[0-2])/\d{2}", value):
            raise ValueError("Gunakan format BB/TT")
        month, year = map(int, value.split("/"))
        now = datetime.now(timezone.utc)
        if (2000 + year, month) < (now.year, now.month):
            raise ValueError("Kartu kedaluwarsa")
        return value


class Identity(StrictModel):
    full_name: SecretStr = Field(min_length=1, max_length=200)
    organization: SecretStr = Field(min_length=1, max_length=200)
    address: SecretStr = Field(min_length=1, max_length=300)
    city: SecretStr = Field(min_length=1, max_length=100)
    region: SecretStr = Field(default=SecretStr(""), max_length=100)
    postal_code: SecretStr = Field(min_length=1, max_length=30)
    country: str = Field(pattern="^[A-Z]{2}$")


class SavedAddress(StrictModel):
    address: str = Field(min_length=1, max_length=300)
    city: str = Field(min_length=1, max_length=100)
    region: str = Field(default="", max_length=100)
    postal_code: str = Field(min_length=1, max_length=30)
    country: str = Field(pattern="^[A-Z]{2}$")


class BatchInput(StrictModel):
    request_id: UUID
    accounts: list[Account] = Field(min_length=1, max_length=100)
    card: Card
    concurrency: int = Field(default=3, ge=1, le=8)
    identity: Identity
    amount_usd: Decimal = Field(gt=0, le=10000, max_digits=7, decimal_places=2)
    total_limit_usd: Decimal = Field(gt=0, le=1000000, max_digits=9, decimal_places=2)

    @model_validator(mode="after")
    def unique_accounts(self):
        if len({a.email for a in self.accounts}) != len(self.accounts):
            raise ValueError("Email duplikat dalam batch")
        if self.amount_usd * len(self.accounts) > self.total_limit_usd:
            raise ValueError("Total pembelian melebihi batas batch")
        return self


class Login(StrictModel):
    password: SecretStr = Field(min_length=1, max_length=1024)


class Control(StrictModel):
    kind: str = Field(pattern="^(click|text|key|scroll|tab)$")
    x: int = Field(default=0, ge=0, le=1280)
    y: int = Field(default=0, ge=0, le=800)
    text: SecretStr = Field(default=SecretStr(""), max_length=2048)
    key: str = Field(default="", max_length=30)
    delta: int = Field(default=0, ge=-1600, le=1600)
    tab: int = Field(default=0, ge=0, le=30)
