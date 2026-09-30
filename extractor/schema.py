"""Estructura que el modelo debe devolver para cada artículo."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


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


class Extraction(BaseModel):
    people: list[Person] = Field(description="ONLY people arrested, charged or identified as suspects")
    incident_date: Optional[str] = Field(None, description="YYYY-MM-DD date the crime happened")
    incident_date_evidence: Optional[str] = Field(
        None, description="Verbatim words the date was derived from (e.g. 'Friday around 6 p.m.')")
    arrest_date: Optional[str] = Field(None, description="YYYY-MM-DD date of the arrest, if stated or clearly implied")
    city: Optional[str] = None
    county: Optional[str] = Field(None, description="County of the incident, e.g. 'Orange'")
    state: Optional[str] = Field(None, description="Two-letter state code, e.g. 'FL'")
    agency: Optional[str] = Field(None, description="Law enforcement agency that made the arrest")
    charges: list[str] = Field(default_factory=list, description="Each charge as written in the article")
    summary: Optional[str] = Field(None, description="One sentence describing the crime")
