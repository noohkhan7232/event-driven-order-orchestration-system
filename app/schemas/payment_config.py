from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class PaymentConfigWrite(BaseModel):
    model_config = ConfigDict(hide_input_in_errors=True, extra="forbid")
    key_id: str = Field(min_length=8, max_length=120, pattern=r"^[A-Za-z0-9_]+$")
    key_secret: SecretStr = Field(min_length=1, max_length=2048)
    webhook_secret: SecretStr = Field(min_length=1, max_length=2048)
    mode: Literal["test", "live"]


class PaymentConfigRead(BaseModel):
    key_id: str
    webhook_url: str
    webhook_public_id: str
    mode: str
    active: bool
