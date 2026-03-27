from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP

EXPORT_NUMBER_FORMAT_POINT = "point"
EXPORT_NUMBER_FORMAT_COMMA = "comma"
DEFAULT_EXPORT_NUMBER_FORMAT = EXPORT_NUMBER_FORMAT_POINT

EXPORT_NUMBER_FORMAT_CHOICES = [
    (EXPORT_NUMBER_FORMAT_POINT, "Point décimal (1234.56)"),
    (EXPORT_NUMBER_FORMAT_COMMA, "Virgule décimale (1234,56)"),
]


def normalize_export_number_format(value: str | None) -> str:
    if value == EXPORT_NUMBER_FORMAT_COMMA:
        return EXPORT_NUMBER_FORMAT_COMMA
    return DEFAULT_EXPORT_NUMBER_FORMAT


def format_money_text(value, *, number_format: str = DEFAULT_EXPORT_NUMBER_FORMAT) -> str:
    if value is None:
        return ""
    decimal_value = value if isinstance(value, Decimal) else Decimal(str(value))
    decimal_value = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    rendered = f"{decimal_value:.2f}"
    if normalize_export_number_format(number_format) == EXPORT_NUMBER_FORMAT_COMMA:
        rendered = rendered.replace(".", ",")
    return f"{rendered} $"
