"""Estructura que el modelo debe devolver para cada artículo."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class Person(BaseModel):
    first_name: str = Field(description="First name exactly as written in the article")
    middle_name: Optional[str] = Field(None, description="Middle name or initial, only if written")
    last_name: str = Field(description="Last name exactly as written (keep compound surnames whole)")
    suffix: Optional[str] = Field(None, description="Jr., Sr., II, III... only if written")
    age: Optional[int] = Field(None, description="Age stated in the article")
    role: Literal["arrested", "charged", "suspect"] = Field(
        description="arrested = taken into custody; charged = formally charged; "
                    "suspect = identified by police but not arrested yet")
    evidence: str = Field(description="Short verbatim quote from the article naming this person and their role")

    @field_validator("evidence", mode="before")
    @classmethod
    def _evidence_may_be_missing(cls, value):
        # Un modelo local sin formato obligado puede mandar null; la frase solo es
        # referencia y repair la completa desde el texto (Bonilla, 22/10/2026).
        return "" if value is None else value


class Extraction(BaseModel):
    people: list[Person] = Field(description="ONLY people arrested, charged or identified as suspects")
    incident_date: Optional[str] = Field(None, description="YYYY-MM-DD date the crime happened")
    incident_date_evidence: Optional[str] = Field(
        None, description="Verbatim words the date was derived from (e.g. 'Friday around 6 p.m.')")
    incident_date_precision: Optional[Literal["day", "month", "year"]] = Field(
        None, description="'day' if the article gives the exact day; 'month' or 'year' if it only gives that")
    arrest_date: Optional[str] = Field(None, description="YYYY-MM-DD date of the arrest, if stated or clearly implied")
    city: Optional[str] = None
    county: Optional[str] = Field(None, description="County of the incident, e.g. 'Orange'")
    state: Optional[str] = Field(None, description="Two-letter state code, e.g. 'FL'")
    agency: Optional[str] = Field(None, description="Law enforcement agency that made the arrest")
    charges: list[str] = Field(default_factory=list, description="Each charge as written in the article")
    summary: Optional[str] = Field(None, description="One sentence describing the crime")
