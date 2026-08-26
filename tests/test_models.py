from __future__ import annotations

from datetime import date

import pytest

from app.email_content import DEFAULT_SALUTATION, FEMALE_SALUTATION, MALE_SALUTATION
from app.models import Client, resolve_salutation


@pytest.mark.parametrize(
    "gender",
    ["Mujer", "Femenino", "F", "Female"],
)
def test_resolve_salutation_female_values_map_to_estimada(gender: str) -> None:
    assert resolve_salutation(gender) == FEMALE_SALUTATION


@pytest.mark.parametrize(
    "gender",
    ["Hombre", "Masculino", "M", "Male"],
)
def test_resolve_salutation_male_values_map_to_estimado(gender: str) -> None:
    assert resolve_salutation(gender) == MALE_SALUTATION


def test_resolve_salutation_missing_gender_defaults_to_estimado_a() -> None:
    assert resolve_salutation(None) == DEFAULT_SALUTATION


def test_resolve_salutation_empty_gender_defaults_to_estimado_a() -> None:
    assert resolve_salutation("") == DEFAULT_SALUTATION


def test_resolve_salutation_unknown_gender_defaults_to_estimado_a() -> None:
    assert resolve_salutation("Nonbinary") == DEFAULT_SALUTATION


@pytest.mark.parametrize(
    ("gender", "expected"),
    [
        ("mujer", FEMALE_SALUTATION),
        ("MUJER", FEMALE_SALUTATION),
        ("MuJeR", FEMALE_SALUTATION),
        ("male", MALE_SALUTATION),
        ("MALE", MALE_SALUTATION),
        ("MaLe", MALE_SALUTATION),
    ],
)
def test_resolve_salutation_is_case_insensitive(gender: str, expected: str) -> None:
    assert resolve_salutation(gender) == expected


@pytest.mark.parametrize(
    ("gender", "expected"),
    [
        ("  Mujer  ", FEMALE_SALUTATION),
        ("\tFemale\n", FEMALE_SALUTATION),
        ("  Hombre  ", MALE_SALUTATION),
        ("\tMale\n", MALE_SALUTATION),
        ("   ", DEFAULT_SALUTATION),
    ],
)
def test_resolve_salutation_strips_surrounding_whitespace(
    gender: str, expected: str
) -> None:
    assert resolve_salutation(gender) == expected


def test_client_salutation_property_reflects_gender() -> None:
    client = Client(
        name="Test",
        email="test@example.com",
        birthday=date(2000, 1, 1),
        row_index=2,
        gender="Femenino",
    )

    assert client.salutation == FEMALE_SALUTATION


def test_client_salutation_property_defaults_when_gender_missing() -> None:
    client = Client(
        name="Test",
        email="test@example.com",
        birthday=date(2000, 1, 1),
        row_index=2,
    )

    assert client.salutation == DEFAULT_SALUTATION
