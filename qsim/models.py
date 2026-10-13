from typing import Literal, Annotated
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .qasm_adapter import SUPPORTED_COMBINATIONS


class Submission(BaseModel):
    engine: Literal["clifft", "qsim"] = "clifft"
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(default="Untitled circuit", min_length=1, max_length=80)
    circuit: str = Field(min_length=1, max_length=65536)
    shots: int = Field(default=1000, ge=1, le=10000)
    seed: int = Field(default=42, ge=0, le=2**63-1)
    input_format: Literal["stim", "qasm2"] = "stim"
    operation: Literal["sample", "basis_probabilities"] = "sample"
    bitstrings: list[Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[01]+$")]] | None = Field(default=None, min_length=1, max_length=256)
    bit_order: Literal["big", "little"] = "big"

    @model_validator(mode="after")
    def valid_operation(self):
        if self.engine == "qsim" and self.input_format != "qasm2":
            raise ValueError("qsim supports OpenQASM 2 requests")
        if self.operation == "basis_probabilities":
            if not self.bitstrings:
                raise ValueError("basis_probabilities requires bitstrings")
            if len({len(value) for value in self.bitstrings}) != 1:
                raise ValueError("bitstrings must have equal lengths")
        elif self.bitstrings is not None:
            raise ValueError("bitstrings is only valid for basis_probabilities")
        if self.input_format == "qasm2" and self.operation == "sample":
            if self.model_fields_set & {"bitstrings", "bit_order"}:
                raise ValueError("QASM sampling does not accept bitstrings or bit_order")
            if self.operation not in SUPPORTED_COMBINATIONS.get(self.engine, {}).get(self.input_format, ()):
                raise ValueError("QASM sampling requires a supported engine; select engine=qsim")
        return self


class StimSubmission(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(default="Untitled circuit", min_length=1, max_length=80)
    circuit: str = Field(min_length=1, max_length=65536)
    shots: int = Field(default=1000, ge=1, le=10000)
    seed: int = Field(default=42, ge=0, le=2**63-1)


class QasmSubmission(BaseModel):
    engine: Literal["clifft", "qsim"] = "clifft"
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(default="Untitled circuit", min_length=1, max_length=80)
    circuit: str = Field(min_length=1, max_length=65536)
    operation: Literal["sample", "basis_probabilities"] = "basis_probabilities"
    shots: int = Field(default=1000, ge=1, le=10000)
    seed: int = Field(default=42, ge=0, le=2**63-1)
    bitstrings: list[Annotated[str, Field(min_length=1, max_length=4096, pattern=r"^[01]+$")]] | None = Field(default=None, min_length=1, max_length=256)
    bit_order: Literal["big", "little"] = "big"

    @model_validator(mode="after")
    def valid_operation(self):
        if self.operation == "basis_probabilities" and self.model_fields_set & {"shots", "seed"}:
            raise ValueError("Probability requests do not accept shots or seed")
        Submission(**{**self.model_dump(exclude_unset=True), "operation": self.operation,
                      "input_format": "qasm2"})
        return self


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["RUNNING", "SUCCEEDED", "FAILED"]
    error: str | None = Field(default=None, max_length=2000)
    summary: dict | None = None
    artifact: str | None = Field(default=None, max_length=12 * 1024 * 1024)


class KeyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]+$")
    purpose: str = Field(min_length=10, max_length=2000)

    @field_validator("name", "email", "purpose", mode="before")
    @classmethod
    def trim_fields(cls, value):
        return value.strip() if isinstance(value, str) else value
