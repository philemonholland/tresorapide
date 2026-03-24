"""Helpers for AI field confidence scores in bon OCR and review flows."""
from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable


AI_CONFIDENCE_MISSING = "NA"
AI_CONFIDENCE_LABELS = {
    0: "Pas du tout confiant",
    1: "Très faible confiance",
    2: "Faible confiance",
    3: "Confiance plutôt faible",
    4: "Confiance mitigée",
    5: "Confiance modérée",
    6: "Assez confiant",
    7: "Confiant",
    8: "Très confiant",
    9: "Confiance maximale",
}
AI_CONFIDENCE_MISSING_TOOLTIP = "NA - Information absente du document."

OCR_FIELD_CONFIDENCE_KEYS = (
    "document_type",
    "bc_number",
    "associated_bc_number",
    "supplier_name",
    "supplier_address",
    "reimburse_to",
    "expense_member_name",
    "expense_apartment",
    "validator_member_name",
    "validator_apartment",
    "signer_roles_ambiguous",
    "member_name",
    "apartment_number",
    "merchant",
    "purchase_date",
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
    "summary",
)

DUPLICATE_FIELD_CONFIDENCE_KEYS = (
    "is_same_purchase",
    "confidence",
    "reasoning",
)

REVIEW_FIELD_SOURCE_KEYS = {
    "document_type": ("document_type",),
    "bc_number": ("bc_number",),
    "associated_bc_number": ("associated_bc_number",),
    "supplier_name": ("supplier_name",),
    "supplier_address": ("supplier_address",),
    "reimburse_to": ("reimburse_to",),
    "expense_member_name": ("expense_member_name",),
    "expense_apartment": ("expense_apartment",),
    "validator_member_name": ("validator_member_name",),
    "validator_apartment": ("validator_apartment",),
    "signer_roles_ambiguous": ("signer_roles_ambiguous",),
    "member_name_raw": ("member_name",),
    "apartment_number": ("apartment_number",),
    "merchant_name": ("merchant", "supplier_name"),
    "purchase_date": ("purchase_date",),
    "subtotal": ("subtotal",),
    "tps": ("tps",),
    "tvq": ("tvq",),
    "untaxed_extra_amount": ("untaxed_extra_amount",),
    "total": ("total",),
    "summary": ("summary",),
}

REVIEW_FIELD_LABELS = {
    "document_type": "Type de document",
    "bc_number": "N° bon de commande",
    "associated_bc_number": "N° BC associé",
    "supplier_name": "Fournisseur",
    "supplier_address": "Adresse du fournisseur",
    "reimburse_to": "Rembourser",
    "expense_member_name": "Dépense effectuée par",
    "expense_apartment": "Appartement du signataire",
    "validator_member_name": "Validé par",
    "validator_apartment": "Appartement du validateur",
    "signer_roles_ambiguous": "Attribution ambiguë",
    "member_name_raw": "Nom extrait (IA)",
    "apartment_number": "Appartement",
    "merchant_name": "Marchand",
    "purchase_date": "Date d'achat",
    "subtotal": "Sous-total",
    "tps": "TPS",
    "tvq": "TVQ",
    "untaxed_extra_amount": "Frais non taxables",
    "total": "Total",
    "summary": "Résumé des achats",
}

REVIEW_FIELD_ORDER = (
    "document_type",
    "bc_number",
    "associated_bc_number",
    "supplier_name",
    "supplier_address",
    "reimburse_to",
    "expense_member_name",
    "expense_apartment",
    "validator_member_name",
    "validator_apartment",
    "signer_roles_ambiguous",
    "member_name_raw",
    "apartment_number",
    "merchant_name",
    "purchase_date",
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
    "summary",
)

SUMMARY_FIELD_ORDER = (
    "document_type",
    "bc_number",
    "associated_bc_number",
    "supplier_name",
    "supplier_address",
    "reimburse_to",
    "expense_member_name",
    "expense_apartment",
    "validator_member_name",
    "validator_apartment",
    "signer_roles_ambiguous",
    "apartment_number",
    "merchant_name",
    "purchase_date",
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
    "summary",
)

INVOICE_PREFERRED_FIELDS = {
    "supplier_name",
    "supplier_address",
    "merchant_name",
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
}

DOCUMENT_TYPE_LABELS = {
    "receipt": "Reçu",
    "paper_bc": "Bon de commande papier",
    "invoice": "Facture",
}

REIMBURSE_TO_LABELS = {
    "member": "Membre",
    "supplier": "Fournisseur",
}

MONEY_FIELDS = {"subtotal", "tps", "tvq", "untaxed_extra_amount", "total"}
TEXT_FIELDS = {
    "document_type",
    "bc_number",
    "associated_bc_number",
    "supplier_name",
    "supplier_address",
    "reimburse_to",
    "expense_member_name",
    "expense_apartment",
    "validator_member_name",
    "validator_apartment",
    "member_name_raw",
    "apartment_number",
    "merchant_name",
    "summary",
}


def normalize_ai_confidence_score(value: Any) -> int | str:
    if value in (None, ""):
        return AI_CONFIDENCE_MISSING
    if isinstance(value, str) and value.strip().upper() == AI_CONFIDENCE_MISSING:
        return AI_CONFIDENCE_MISSING
    try:
        score = Decimal(str(value)).quantize(
            Decimal("1"),
            rounding=ROUND_HALF_UP,
        )
    except (InvalidOperation, TypeError, ValueError):
        return AI_CONFIDENCE_MISSING
    score = min(max(score, Decimal("0")), Decimal("9"))
    return int(score)


def normalize_ai_confidence_scores(
    raw_value: Any,
    *,
    allowed_keys: Iterable[str] | None = None,
) -> dict[str, int | str]:
    if not isinstance(raw_value, dict):
        return {}
    allowed = {str(key) for key in allowed_keys} if allowed_keys is not None else None
    normalized: dict[str, int | str] = {}
    for raw_key, raw_score in raw_value.items():
        key = str(raw_key)
        if allowed is not None and key not in allowed:
            continue
        normalized[key] = normalize_ai_confidence_score(raw_score)
    return normalized


def build_complete_ai_confidence_scores(
    raw_value: Any,
    *,
    allowed_keys: Iterable[str],
) -> dict[str, int | str]:
    normalized = normalize_ai_confidence_scores(raw_value, allowed_keys=allowed_keys)
    return {
        str(key): normalized.get(str(key), AI_CONFIDENCE_MISSING)
        for key in allowed_keys
    }


def ai_confidence_tooltip(score: int | str | None) -> str:
    normalized = normalize_ai_confidence_score(score)
    if normalized == AI_CONFIDENCE_MISSING:
        return AI_CONFIDENCE_MISSING_TOOLTIP
    return f"{normalized}/9 - {AI_CONFIDENCE_LABELS.get(normalized, 'Confiance IA')}"


def build_ai_confidence_badge(score: int | str | None) -> dict[str, str] | None:
    if score is None:
        return None
    normalized = normalize_ai_confidence_score(score)
    if normalized == AI_CONFIDENCE_MISSING:
        return {
            "display": AI_CONFIDENCE_MISSING,
            "tooltip": AI_CONFIDENCE_MISSING_TOOLTIP,
            "css_class": "ai-confidence-missing",
        }
    return {
        "display": str(normalized),
        "tooltip": ai_confidence_tooltip(normalized),
        "css_class": f"ai-confidence-{normalized}",
    }


def build_ai_confidence_badges(
    score_map: dict[str, int | str] | None,
) -> dict[str, dict[str, str]]:
    if not isinstance(score_map, dict):
        return {}
    badges: dict[str, dict[str, str]] = {}
    for key, score in score_map.items():
        badge = build_ai_confidence_badge(score)
        if badge:
            badges[str(key)] = badge
    return badges


def _normalize_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    try:
        return datetime.strptime(str(value), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _normalize_bool(value: Any) -> bool | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "oui", "on"}


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _normalize_match_value(field_name: str, value: Any) -> Any:
    if field_name in MONEY_FIELDS:
        return _normalize_decimal(value)
    if field_name == "purchase_date":
        return _normalize_date(value)
    if field_name == "signer_roles_ambiguous":
        return _normalize_bool(value)
    text_value = _normalize_text(value)
    if field_name in {"document_type", "reimburse_to"}:
        return text_value.lower()
    return text_value


def _is_empty_match_value(field_name: str, value: Any) -> bool:
    normalized = _normalize_match_value(field_name, value)
    if field_name == "signer_roles_ambiguous":
        return normalized is None
    return normalized in (None, "")


def _review_field_key_order(field_name: str, document_type: str) -> tuple[str, ...]:
    source_keys = REVIEW_FIELD_SOURCE_KEYS.get(field_name, ())
    if document_type == "paper_bc" and field_name in INVOICE_PREFERRED_FIELDS:
        return source_keys
    return source_keys


def parse_receipt_ai_documents(raw_value: Any) -> list[dict[str, Any]]:
    data = raw_value
    if isinstance(raw_value, str):
        try:
            data = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return []
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return []
    documents: list[dict[str, Any]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        documents.append(
            {
                **item,
                "field_confidence_scores": normalize_ai_confidence_scores(
                    item.get("field_confidence_scores"),
                    allowed_keys=OCR_FIELD_CONFIDENCE_KEYS,
                ),
            }
        )
    return documents


def _ordered_receipt_documents(
    documents: list[dict[str, Any]],
    *,
    field_name: str,
    document_type: str,
) -> list[dict[str, Any]]:
    preferred_types: list[str] = []
    if document_type == "paper_bc" and field_name in INVOICE_PREFERRED_FIELDS:
        preferred_types.append("invoice")
    if document_type:
        preferred_types.append(document_type)

    def sort_key(document: dict[str, Any]) -> tuple[int, int]:
        doc_type = _normalize_text(document.get("document_type")).lower()
        try:
            preferred_index = preferred_types.index(doc_type)
        except ValueError:
            preferred_index = len(preferred_types)
        has_scores = 0 if document.get("field_confidence_scores") else 1
        return preferred_index, has_scores

    return sorted(documents, key=sort_key)


def _find_review_field_confidence_score(
    documents: list[dict[str, Any]],
    *,
    field_name: str,
    current_value: Any,
    document_type: str,
) -> int | str | None:
    if field_name not in REVIEW_FIELD_SOURCE_KEYS:
        return None

    ordered_documents = _ordered_receipt_documents(
        documents,
        field_name=field_name,
        document_type=document_type,
    )
    current_is_empty = _is_empty_match_value(field_name, current_value)
    key_order = _review_field_key_order(field_name, document_type)

    for document in ordered_documents:
        scores = document.get("field_confidence_scores") or {}
        if not scores:
            continue

        for source_key in key_order:
            if source_key not in scores:
                continue

            document_value = document.get(source_key)
            if current_is_empty:
                if _is_empty_match_value(field_name, document_value):
                    return scores[source_key]
                continue

            if _normalize_match_value(field_name, current_value) == _normalize_match_value(
                field_name,
                document_value,
            ):
                return scores[source_key]

    return None


def build_receipt_review_confidence_scores(
    receipt,
    values: dict[str, Any],
    *,
    document_type: str = "",
) -> dict[str, int | str]:
    documents = parse_receipt_ai_documents(getattr(receipt, "ocr_raw_text", ""))
    score_map: dict[str, int | str] = {}
    for field_name in REVIEW_FIELD_ORDER:
        if field_name not in values:
            continue
        score = _find_review_field_confidence_score(
            documents,
            field_name=field_name,
            current_value=values.get(field_name),
            document_type=document_type,
        )
        if score is not None:
            score_map[field_name] = score
    return score_map


def build_receipt_review_confidence_badges(
    receipt,
    values: dict[str, Any],
    *,
    document_type: str = "",
) -> dict[str, dict[str, str]]:
    return build_ai_confidence_badges(
        build_receipt_review_confidence_scores(
            receipt,
            values,
            document_type=document_type,
        )
    )


def _format_summary_value(field_name: str, value: Any) -> str:
    if _is_empty_match_value(field_name, value):
        return "—"
    if field_name in MONEY_FIELDS:
        normalized = _normalize_decimal(value)
        return f"{normalized:.2f} $" if normalized is not None else "—"
    if field_name == "purchase_date":
        normalized_date = _normalize_date(value)
        return normalized_date.isoformat() if normalized_date else "—"
    if field_name == "document_type":
        return DOCUMENT_TYPE_LABELS.get(str(value), str(value))
    if field_name == "reimburse_to":
        return REIMBURSE_TO_LABELS.get(str(value), str(value))
    if field_name == "signer_roles_ambiguous":
        normalized_bool = _normalize_bool(value)
        if normalized_bool is None:
            return "—"
        return "Oui" if normalized_bool else "Non"
    return _normalize_text(value) or "—"


def _receipt_summary_values(extracted_fields) -> dict[str, Any]:
    return {
        "document_type": extracted_fields.final_document_type or extracted_fields.document_type_candidate,
        "bc_number": extracted_fields.final_bc_number or extracted_fields.bc_number_candidate,
        "associated_bc_number": extracted_fields.final_associated_bc_number or extracted_fields.associated_bc_number_candidate,
        "supplier_name": extracted_fields.final_supplier_name or extracted_fields.supplier_name_candidate,
        "supplier_address": extracted_fields.final_supplier_address or extracted_fields.supplier_address_candidate,
        "reimburse_to": extracted_fields.final_reimburse_to or extracted_fields.reimburse_to_candidate,
        "expense_member_name": extracted_fields.final_expense_member_name or extracted_fields.expense_member_name_candidate,
        "expense_apartment": extracted_fields.final_expense_apartment or extracted_fields.expense_apartment_candidate,
        "validator_member_name": extracted_fields.final_validator_member_name or extracted_fields.validator_member_name_candidate,
        "validator_apartment": extracted_fields.final_validator_apartment or extracted_fields.validator_apartment_candidate,
        "signer_roles_ambiguous": extracted_fields.signer_roles_ambiguous_final,
        "apartment_number": extracted_fields.final_apartment_number or extracted_fields.apartment_number_candidate,
        "merchant_name": extracted_fields.final_merchant or extracted_fields.merchant_candidate,
        "purchase_date": extracted_fields.final_purchase_date or extracted_fields.purchase_date_candidate,
        "subtotal": extracted_fields.final_subtotal if extracted_fields.final_subtotal is not None else extracted_fields.subtotal_candidate,
        "tps": extracted_fields.final_tps if extracted_fields.final_tps is not None else extracted_fields.tps_candidate,
        "tvq": extracted_fields.final_tvq if extracted_fields.final_tvq is not None else extracted_fields.tvq_candidate,
        "untaxed_extra_amount": extracted_fields.final_untaxed_extra_amount
        if extracted_fields.final_untaxed_extra_amount is not None
        else extracted_fields.untaxed_extra_amount_candidate,
        "total": extracted_fields.final_total if extracted_fields.final_total is not None else extracted_fields.total_candidate,
        "summary": extracted_fields.final_summary or extracted_fields.summary_candidate,
    }


def build_receipt_confidence_summary_rows(receipt) -> list[dict[str, Any]]:
    try:
        extracted_fields = receipt.extracted_fields
    except Exception:
        return []

    final_confidence_scores = extracted_fields.final_confidence_scores or {}
    if not isinstance(final_confidence_scores, dict) or not final_confidence_scores:
        return []

    values = _receipt_summary_values(extracted_fields)
    rows: list[dict[str, Any]] = []
    for field_name in SUMMARY_FIELD_ORDER:
        if field_name not in final_confidence_scores:
            continue
        badge = build_ai_confidence_badge(final_confidence_scores.get(field_name))
        if not badge:
            continue
        rows.append(
            {
                "field_name": field_name,
                "label": REVIEW_FIELD_LABELS.get(field_name, field_name),
                "value": _format_summary_value(field_name, values.get(field_name)),
                "confidence": badge,
            }
        )
    return rows
