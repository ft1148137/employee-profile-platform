"""Transient results returned only by the successful apply response."""

from typing import Annotated, Literal

from pydantic import BaseModel, Field

from app.domains.account.schemas import InitialPassword


class PasswordOutput(InitialPassword):
    type: Literal["initial_password"] = "initial_password"


class AccountsOutput(BaseModel):
    type: Literal["accounts_file"] = "accounts_file"
    filename: str
    content_base64: str
    count: int


OneTimeOutput = Annotated[PasswordOutput | AccountsOutput, Field(discriminator="type")]
