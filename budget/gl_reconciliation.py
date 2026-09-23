"""
Grand Livre reconciliation service.

Matches parsed GL entries against existing expenses, uses AI for fuzzy matching,
extracts apartment/BC numbers from GL descriptions, and performs balance checks.
"""
import hashlib
import json
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import List, Optional

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from .models import (
    BudgetYear, Expense, ExpenseSourceType, SubBudget,
    GrandLivreUpload, GrandLivreEntry, GrandLivreAdjustment, ReconciliationResult,
    GLMatchConfidence, GLUploadStatus,
)
from .gl_parser import GLTransaction, GLAccountSection, parse_grand_livre

logger = logging.getLogger(__name__)
AI_CONFIDENCE_MISSING = "NA"
GL_PARSE_CONFIDENCE_KEYS = ("bc_number", "apartment", "description_clean")

# ── Regex patterns for extracting BC/apartment from GL descriptions ──
BC_PATTERN = re.compile(r"BC\s*#?\s*(\d{4,7})", re.IGNORECASE)
APT_PATTERN = re.compile(r"#\s*(\d{3})")
RECEIPT_NUM_PATTERN = re.compile(r"^(\d{4,10})-")

GL_IMPORT_CATEGORY_RULES = (
    (11, ("peinture",)),
    (5, ("terminix", "extermin")),
    (9, ("photocopi",)),
    (8, ("transport",)),
    (7, ("nettoy", "produit menager", "entretien menager")),
    (10, ("activite sociale",)),
    (3, ("extincteur", "systeme d'alarme")),
)


def _normalize_gl_text(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    return re.sub(r"\s+", " ", text.casefold()).strip()


def _normalize_account_number(value):
    return re.sub(r"\D", "", str(value or ""))


def _gl_entry_fingerprint(entry):
    """Stable identity for the same accountant transaction across uploads."""
    payload = {
        "account": _normalize_account_number(entry.upload.account_number),
        "date": entry.date.isoformat() if entry.date else "",
        "source": _normalize_gl_text(entry.source),
        "description": _normalize_gl_text(entry.description_raw),
        "debit": str(entry.debit.quantize(Decimal("0.01"))),
        "credit": str(entry.credit.quantize(Decimal("0.01"))),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _clean_gl_import_description(entry):
    """Remove accounting identifiers while preserving the described purchase."""
    text = re.sub(r"^\s*\d{4,10}\s*-\s*", "", entry.description_raw or "")
    text = re.sub(
        r"^\s*BC\s*#?\s*\d{4,7}\s*-\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = text.replace("+", " et ").replace("charni;ere", "charnière")
    text = re.sub(r"\bpoigée\b", "poignée", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpilliers\b", "piliers", text, flags=re.IGNORECASE)
    text = re.sub(r"\bferme[ -]?porte\b", "ferme-porte", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpoignée porte\b", "poignée de porte", text, flags=re.IGNORECASE)
    text = re.sub(r"\bpeinture piliers\b", "peinture des piliers", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\bclapet et levier toilette\b",
        "clapet et levier de toilette",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\bbatterie d'urgence éclairage\b",
        "batterie d'urgence pour l'éclairage",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"contratannueldu1septau31août2027",
        "contrat annuel du 1er septembre au 31 août 2027",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*#\s*(\d{3})\b", r" - appartement \1", text)
    text = re.sub(r"\s+", " ", text).strip(" -")
    if not text:
        text = (entry.description_raw or "Dépense du Grand Livre").strip()
    return text[:1].upper() + text[1:]


def _validate_upload_account(upload):
    expected = _normalize_account_number(upload.budget_year.house.account_number)
    actual = _normalize_account_number(upload.account_number)
    if not expected or actual != expected:
        raise ValidationError(
            "Le compte du Grand Livre ne correspond pas au compte de la maison "
            f"({upload.account_number or 'absent'} != "
            f"{upload.budget_year.house.account_number or 'absent'})."
        )


def _resolve_gl_import_sub_budget(
    budget_year,
    entry,
    *,
    existing_by_bc,
    fallback_sub_budget,
):
    """Choose a category only from strong local or textual evidence."""
    if entry.extracted_apartment:
        apartment_repairs = budget_year.sub_budgets.filter(trace_code=1).first()
        if apartment_repairs:
            return apartment_repairs

    bc_key = re.sub(r"\D", "", entry.extracted_bc_number or "")
    if bc_key:
        related = existing_by_bc.get(bc_key)
        if related:
            return related.sub_budget

    evidence = _normalize_gl_text(
        f"{entry.source} {entry.description_clean} {entry.description_raw}"
    )
    for trace_code, keywords in GL_IMPORT_CATEGORY_RULES:
        if any(keyword in evidence for keyword in keywords):
            category = budget_year.sub_budgets.filter(trace_code=trace_code).first()
            if category:
                return category

    return fallback_sub_budget


def _gl_import_spent_by_label(budget_year, entry):
    """Return the apartment/contact label, or the house code when unknown."""
    apartment_code = (entry.extracted_apartment or "").strip()
    if not apartment_code:
        return budget_year.house.code

    from members.models import Apartment

    apartment = Apartment.objects.filter(
        house=budget_year.house,
        code=apartment_code,
    ).first()
    if apartment is None:
        return apartment_code

    on_date = entry.date
    if on_date is None:
        from datetime import date
        on_date = date(budget_year.year, 12, 31)
    residency = (
        apartment.residencies.active_on(on_date)
        .select_related("member")
        .order_by("-is_primary_contact", "-is_coop_member", "id")
        .first()
    )
    if residency is None:
        return apartment.code
    return f"{apartment.code} / {residency.member.display_name}"


def active_expenses_queryset(budget_year):
    """Return expenses that currently contribute to the active grid.

    Cancellation rows, originals with a reversal, and expenses owned by void
    bons are audit history rather than reconciliation candidates.
    """
    return (
        Expense.objects.filter(
            budget_year=budget_year,
            is_cancellation=False,
            reversals__isnull=True,
        )
        .filter(
            Q(bon_de_commande__isnull=True)
            | ~Q(bon_de_commande__status="VOID")
        )
        .distinct()
    )


def active_adjustments_by_entry(upload):
    return {
        adjustment.entry_id: adjustment
        for adjustment in GrandLivreAdjustment.objects.filter(
            entry__upload=upload,
            archived_at__isnull=True,
        ).select_related("entry")
    }


def effective_gl_amount(entry, adjustment=None):
    """Return source net amount after one active subtractive adjustment."""
    source_amount = entry.net_amount
    if adjustment is None:
        return source_amount
    direction = Decimal("1") if source_amount >= 0 else Decimal("-1")
    return source_amount - (direction * adjustment.amount_to_subtract)


def _extract_bc_number(description: str) -> str:
    """Extract BC number from GL description like '496578-BC 16482-scellant'."""
    m = BC_PATTERN.search(description)
    return m.group(1) if m else ""


def _extract_apartment(description: str) -> str:
    """Extract apartment from GL description like 'tuyauéchangeur#104'."""
    m = APT_PATTERN.search(description)
    return m.group(1) if m else ""


def _normalize_ai_confidence_score(value):
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


def _build_complete_ai_confidence_scores(raw_value, *, allowed_keys):
    raw_scores = raw_value if isinstance(raw_value, dict) else {}
    return {
        str(key): _normalize_ai_confidence_score(raw_scores.get(str(key)))
        for key in allowed_keys
    }


def _normalize_int_identifier(value):
    if value in (None, ""):
        return None
    try:
        return int(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _normalize_float_confidence(value):
    if value in (None, ""):
        return None
    try:
        return float(Decimal(str(value)))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _entry_ai_metadata(entry):
    metadata = entry.ai_metadata if isinstance(entry.ai_metadata, dict) else {}
    return dict(metadata)


def _sort_expense_match_candidates(candidates, entry_date=None):
    """Prefer user-entered expenses over prior GL imports when candidates tie."""
    def _key(expense):
        days_diff = 999999
        if entry_date and expense.entry_date:
            days_diff = abs((expense.entry_date - entry_date).days)
        return (
            1 if expense.source_type == ExpenseSourceType.GL_IMPORT else 0,
            1 if expense.validated_gl else 0,
            1 if not expense.bon_de_commande_id else 0,
            days_diff,
            expense.id,
        )

    return sorted(candidates, key=_key)


def _try_gpt_parse_entries(entries: List[dict]) -> List[dict] | None:
    """
    Use GPT to extract apartment, BC number, and clean description
    from GL entries. Returns enriched entries.
    """
    try:
        from openai import OpenAI
    except ImportError:
        logger.warning("openai not installed, skipping GPT enrichment")
        return entries

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        logger.warning("OPENAI_API_KEY not set, skipping GPT enrichment")
        return entries

    model = getattr(settings, "OPENAI_MODEL", "gpt-4.1")

    # Build the prompt
    items = []
    for e in entries:
        items.append({
            "row": e["row_number"],
            "description": e["description_raw"],
            "source": e["source"],
            "debit": str(e["debit"]),
        })

    prompt = """Tu es un assistant comptable pour une coopérative d'habitation du Québec.
On te donne des lignes extraites du Grand Livre comptable. Chaque ligne a une description
codée par le comptable. Tu dois extraire:
1. Le numéro de BC (bon de commande) s'il existe
2. Le numéro d'appartement s'il existe
3. Une description propre en français, reformulée en une phrase courte et claire

Format de réponse JSON (tableau) :
[{
  "row": N,
  "bc_number": "16482" ou "",
  "apartment": "104" ou "",
  "description_clean": "Scellant, mortier et truelle",
  "field_confidence_scores": {
    "bc_number": 0..9 ou "NA",
    "apartment": 0..9 ou "NA",
    "description_clean": 0..9 ou "NA"
  }
}]

Exemples de descriptions codées:
- "496578-BC 16482-scellant, mortier, truelle" → BC=16482, apt=, desc="Scellant, mortier et truelle"
- "492428-BC168377-tuyauxéchangeur#104" → BC=168377, apt=104, desc="Tuyaux pour échangeur d'air"
- "INVENTAIRE BB-204 - Cartouche-poignée bain" → BC=, apt=204, desc="Inventaire: cartouche et poignée de bain"
- "001-Tontegazonx2x60" → BC=, apt=, desc="Tonte de gazon (2x 60$)"
- "Terminix - 4528861-factannuelle-du01sept25au31août26" → BC=, apt=, desc="Terminix, facture annuelle du 1er sept. 2025 au 31 août 2026"
- "060768-inspection visuelle (extincteurs)" → BC=, apt=, desc="Inspection visuelle des extincteurs"
- "511888-BC137940-Colleplomberie#102" → BC=137940, apt=102, desc="Colle de plomberie"

Réponds UNIQUEMENT avec le JSON, sans markdown.
Si une information est absente, utilise "NA" dans field_confidence_scores plutôt que 0."""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(items, ensure_ascii=False)},
            ],
            max_completion_tokens=4000,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        row_map = {}
        if isinstance(parsed, list):
            for item in parsed:
                if not isinstance(item, dict) or "row" not in item:
                    continue
                row_number = _normalize_int_identifier(item.get("row"))
                if row_number is None:
                    continue
                normalized_item = dict(item)
                normalized_item["field_confidence_scores"] = _build_complete_ai_confidence_scores(
                    item.get("field_confidence_scores"),
                    allowed_keys=GL_PARSE_CONFIDENCE_KEYS,
                )
                row_map[row_number] = normalized_item

        for e in entries:
            gpt = row_map.get(e["row_number"], {})
            if gpt.get("bc_number"):
                e["extracted_bc_number"] = gpt["bc_number"]
            if gpt.get("apartment"):
                e["extracted_apartment"] = gpt["apartment"]
            if gpt.get("description_clean"):
                e["description_clean"] = gpt["description_clean"]
            e["ai_metadata"] = {
                "parse_field_confidence_scores": gpt.get(
                    "field_confidence_scores",
                    _build_complete_ai_confidence_scores(
                        None,
                        allowed_keys=GL_PARSE_CONFIDENCE_KEYS,
                    ),
                ),
            }
    except Exception:
        logger.exception("GPT enrichment of GL entries failed")
        return None
    return entries


def _try_gpt_batch_match(
    unmatched_gl: List[dict],
    unmatched_expenses: List[dict],
) -> List[dict]:
    """Use GPT to find matches between GL entries and expenses by semantic similarity.

    Each GL entry has: id, description, amount, bc_number
    Each expense has: id, description, amount, bon_number

    Returns list of dicts:
    [{"gl_id": int, "expense_id": int, "confidence": float, "confidence_score": 0..9|"NA"}, ...]
    """
    if not unmatched_gl or not unmatched_expenses:
        return []

    try:
        from openai import OpenAI
    except ImportError:
        return []

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return []

    model = getattr(settings, "OPENAI_MODEL", "gpt-4.1")

    prompt = """Tu es un assistant comptable pour une coopérative d'habitation québécoise.
On te donne deux listes:
- ENTRIES_GL : des transactions du Grand Livre comptable
- DEPENSES : des dépenses de la grille du trésorier

Trouve les paires qui représentent la MÊME transaction. Pour confirmer une correspondance :
1. Les montants doivent être identiques (critère obligatoire)
2. Les descriptions doivent être compatibles sémantiquement (ex: "NUBIOCAL 900ML" et "Nettoyants" sont compatibles car NUBIOCAL est un produit nettoyant)
3. Un numéro de BC dans la description GL qui correspond au bon de commande de la dépense est un fort indicateur

Ne force pas de correspondance si les montants ne sont pas identiques.
Ne force pas de correspondance si les descriptions sont clairement incompatibles (produits différents, services différents).

    Réponds UNIQUEMENT avec un tableau JSON :
    [{
      "gl_id": N,
      "expense_id": N,
      "confidence": 0.9,
      "confidence_score": 0..9 ou "NA"
    }]
    Si aucune correspondance n'est trouvée, réponds [].
    Pas de texte autour, seulement le JSON."""

    payload = json.dumps({
        "entries_gl": unmatched_gl,
        "depenses": unmatched_expenses,
    }, ensure_ascii=False)

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": payload},
            ],
            max_completion_tokens=2000,
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            normalized = []
            for m in parsed:
                if not isinstance(m, dict):
                    continue
                gl_id = _normalize_int_identifier(m.get("gl_id"))
                expense_id = _normalize_int_identifier(m.get("expense_id"))
                confidence = _normalize_float_confidence(m.get("confidence"))
                if gl_id is None or expense_id is None or confidence is None:
                    continue
                if confidence < 0.7:
                    continue
                item = dict(m)
                item["gl_id"] = gl_id
                item["expense_id"] = expense_id
                item["confidence"] = confidence
                item["confidence_score"] = _normalize_ai_confidence_score(
                    m.get("confidence_score")
                )
                normalized.append(item)
            return normalized
        return []
    except Exception:
        logger.exception("GPT batch match failed")
        return []


def _try_gpt_anomaly_analysis(
    reconciliation: ReconciliationResult,
    anomalies: list,
    gl_entries: list,
    expenses: list,
) -> list:
    """Use GPT to analyze anomalies and return structured recommendations.

    Returns a list of dicts:
    [{"type": str, "severity": str, "message": str, "confidence_score": 0..9|"NA"}, ...]
    These are appended to the existing anomalies list.
    """
    try:
        from openai import OpenAI
    except ImportError:
        return []

    api_key = getattr(settings, "OPENAI_API_KEY", "")
    if not api_key:
        return []

    model = getattr(settings, "OPENAI_MODEL", "gpt-4.1")

    context = {
        "gl_total": str(reconciliation.gl_total),
        "grille_total": str(reconciliation.grille_total),
        "difference": str(reconciliation.difference),
        "matched": reconciliation.matched_count,
        "unmatched_gl": reconciliation.unmatched_gl_count,
        "missing_from_gl": reconciliation.missing_from_gl_count,
        "anomalies": anomalies[:20],
    }

    prompt = """Analyse ce résultat de rapprochement entre le Grand Livre du comptable
et la Grille de dépenses du trésorier d'une coopérative d'habitation.

Données :
""" + json.dumps(context, ensure_ascii=False, indent=2) + """

Retourne un tableau JSON d'avertissements structurés. Chaque objet doit avoir :
- "type": catégorie courte (ex: "balance_mismatch", "missing_expense", "wrong_house", "timing_issue", "duplicate_risk", "recommendation")
- "severity": "high", "medium", ou "info"
- "message": phrase claire en français, concise, adressée au trésorier (pas de jargon comptable complexe)
- "confidence_score": score de confiance 0..9 pour cet avertissement, ou "NA" si non applicable

Concentre-toi sur :
1. Vérifications prioritaires que le trésorier devrait faire
2. Pistes de résolution pour chaque problème
3. Risques potentiels (dépense attribuée à la mauvaise maison, doublon, etc.)

Ne répète pas les anomalies déjà présentes dans les données.
Retourne UNIQUEMENT le tableau JSON, sans texte autour."""

    try:
        client = OpenAI(api_key=api_key)
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_completion_tokens=2000,
            temperature=0.2,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
            if raw.endswith("```"):
                raw = raw[:-3]
            raw = raw.strip()
        items = json.loads(raw)
        if isinstance(items, list):
            # Validate structure
            valid = []
            for item in items:
                if isinstance(item, dict) and "message" in item:
                    valid.append({
                        "type": item.get("type", "recommendation"),
                        "severity": item.get("severity", "info"),
                        "message": str(item["message"]),
                        "confidence_score": _normalize_ai_confidence_score(
                            item.get("confidence_score")
                        ),
                    })
            return valid
        return []
    except Exception:
        logger.exception("GPT anomaly analysis failed")
        return []


class GrandLivreReconciliationService:
    """Orchestrates GL upload parsing, matching, and balance checking."""

    @staticmethod
    def parse_and_store(upload: GrandLivreUpload) -> GLAccountSection:
        """Parse the uploaded Excel and store entries in the database."""
        house = upload.budget_year.house
        target_account = house.account_number  # e.g. "13-51200"

        section = parse_grand_livre(upload.uploaded_file.path, target_account)

        if not section.transactions:
            upload.status = GLUploadStatus.ERROR
            upload.error_message = (
                f"Aucune transaction trouvée pour le compte {target_account}. "
                f"Vérifiez que le fichier correspond au bon Grand Livre."
            )
            upload.save()
            return section

        # Store parsed entries
        upload.account_number = section.account_number
        upload.gl_total_debit = section.total_debit
        upload.gl_total_credit = section.total_credit
        upload.gl_solde_fin = section.solde_fin
        upload.entry_count = len(section.transactions)
        upload.period_end_date = section.period_end_date
        upload.status = GLUploadStatus.PARSED
        upload.save()

        # Preserve row identities when the same upload is reprocessed. Active
        # adjustments stay attached to their accountant source row even when
        # the dashboard is sorted or the parser is corrected.
        existing_by_row = {
            entry.row_number: entry
            for entry in upload.entries.all()
        }
        entries_to_create = []
        entries_to_update = []
        parsed_row_numbers = set()
        for tx in section.transactions:
            parsed_row_numbers.add(tx.row_number)
            entry = existing_by_row.get(tx.row_number)
            if entry is None:
                entries_to_create.append(GrandLivreEntry(
                    upload=upload,
                    row_number=tx.row_number,
                    period=tx.period,
                    date=tx.date,
                    source=tx.source,
                    description_raw=tx.description,
                    debit=tx.debit,
                    credit=tx.credit,
                    solde_fin=tx.solde_fin,
                    extracted_bc_number=_extract_bc_number(tx.description),
                    extracted_apartment=_extract_apartment(tx.description),
                ))
                continue

            entry.period = tx.period
            entry.date = tx.date
            entry.source = tx.source
            entry.description_raw = tx.description
            entry.debit = tx.debit
            entry.credit = tx.credit
            entry.solde_fin = tx.solde_fin
            entry.extracted_bc_number = _extract_bc_number(tx.description)
            entry.extracted_apartment = _extract_apartment(tx.description)
            entries_to_update.append(entry)

        GrandLivreEntry.objects.bulk_create(entries_to_create)
        if entries_to_update:
            GrandLivreEntry.objects.bulk_update(entries_to_update, [
                "period",
                "date",
                "source",
                "description_raw",
                "debit",
                "credit",
                "solde_fin",
                "extracted_bc_number",
                "extracted_apartment",
            ])
        upload.entries.exclude(row_number__in=parsed_row_numbers).delete()
        GrandLivreReconciliationService.propagate_apartment_context(upload)
        return section

    @staticmethod
    def propagate_apartment_context(upload: GrandLivreUpload):
        """Carry an explicit apartment through the rest of its GL period."""
        entries = list(upload.entries.order_by("row_number"))
        current_period = None
        current_apartment = ""
        changed = []
        for entry in entries:
            period = entry.period or (
                entry.date.strftime("%Y-%m") if entry.date else ""
            )
            if period != current_period:
                current_period = period
                current_apartment = ""

            explicit = _extract_apartment(entry.description_raw)
            if explicit:
                current_apartment = explicit
                source = "explicit"
            elif entry.extracted_apartment and not current_apartment:
                current_apartment = entry.extracted_apartment
                source = "extracted"
            elif current_apartment:
                source = "period_inherited"
            else:
                source = "none"

            target_apartment = current_apartment
            metadata = _entry_ai_metadata(entry)
            context = {
                "period": period,
                "source": source,
                "apartment": target_apartment,
            }
            if (
                entry.extracted_apartment != target_apartment
                or metadata.get("apartment_context") != context
            ):
                entry.extracted_apartment = target_apartment
                metadata["apartment_context"] = context
                entry.ai_metadata = metadata
                changed.append(entry)

        if changed:
            GrandLivreEntry.objects.bulk_update(
                changed,
                ["extracted_apartment", "ai_metadata"],
            )
        return entries

    @staticmethod
    def enrich_with_ai(upload: GrandLivreUpload):
        """Use GPT to extract clean descriptions, BC numbers, and apartments."""
        entries = list(upload.entries.all())
        if not entries:
            return

        entry_dicts = [
            {
                "row_number": e.row_number,
                "description_raw": e.description_raw,
                "source": e.source,
                "debit": str(e.debit),
            }
            for e in entries
        ]

        enriched = _try_gpt_parse_entries(entry_dicts)
        if enriched is None:
            return
        row_map = {d["row_number"]: d for d in enriched}

        for entry in entries:
            data = row_map.get(entry.row_number, {})
            if data.get("description_clean"):
                entry.description_clean = data["description_clean"]
            if data.get("extracted_bc_number") and not entry.extracted_bc_number:
                entry.extracted_bc_number = data["extracted_bc_number"]
            if data.get("extracted_apartment") and not entry.extracted_apartment:
                entry.extracted_apartment = data["extracted_apartment"]
            metadata = _entry_ai_metadata(entry)
            parse_scores = data.get(
                "ai_metadata",
                {},
            ).get("parse_field_confidence_scores")
            if parse_scores is not None:
                metadata["parse_field_confidence_scores"] = parse_scores
            entry.ai_metadata = metadata

        GrandLivreEntry.objects.bulk_update(
            entries,
            [
                "description_clean",
                "extracted_bc_number",
                "extracted_apartment",
                "ai_metadata",
            ],
        )
        GrandLivreReconciliationService.propagate_apartment_context(upload)

    @staticmethod
    def match_expenses(upload: GrandLivreUpload, *, use_ai=True):
        """Match GL entries against existing expenses."""
        upload.status = GLUploadStatus.RECONCILING
        upload.save()

        budget_year = upload.budget_year
        expenses = list(
            active_expenses_queryset(budget_year)
            .select_related("sub_budget")
        )

        # Build a lookup by amount for fast matching
        expenses_by_amount = {}
        for exp in expenses:
            key = exp.amount.quantize(Decimal("0.01"))
            expenses_by_amount.setdefault(key, []).append(exp)

        expenses_by_gl_fingerprint = {
            exp.gl_source_fingerprint: exp
            for exp in expenses
            if exp.gl_source_fingerprint
        }

        # Build by bon_number for BC matching — normalize: strip non-digits
        expenses_by_bc = {}
        for exp in expenses:
            if exp.bon_number:
                raw = exp.bon_number.strip()
                normalized = re.sub(r"\D", "", raw)
                if normalized:
                    expenses_by_bc.setdefault(normalized, []).append(exp)
                if raw and raw != normalized:
                    expenses_by_bc.setdefault(raw, []).append(exp)

        # Re-evaluate every link against current source values, active grid
        # rows and active adjustments. This prevents stale matches surviving
        # a corrected import or a newly created adjustment.
        upload.entries.update(
            matched_expense=None,
            match_confidence=GLMatchConfidence.UNMATCHED,
            match_notes="",
            needs_import=False,
        )
        gl_entries = list(upload.entries.all())
        adjustments = active_adjustments_by_entry(upload)
        matched_expense_ids = set()

        for entry in gl_entries:
            entry_amount = effective_gl_amount(
                entry,
                adjustments.get(entry.pk),
            ).quantize(Decimal("0.01"))
            if entry_amount == Decimal("0.00"):
                metadata = _entry_ai_metadata(entry)
                metadata.pop("semantic_match", None)
                entry.ai_metadata = metadata
                entry.needs_import = False
                continue

            best_match = None
            best_confidence = GLMatchConfidence.UNMATCHED
            match_note = ""

            # 0. Preserve the exact accountant-source identity across uploads.
            fingerprint = _gl_entry_fingerprint(entry)
            fingerprint_match = expenses_by_gl_fingerprint.get(fingerprint)
            if (
                fingerprint_match
                and fingerprint_match.id not in matched_expense_ids
                and fingerprint_match.amount.quantize(Decimal("0.01"))
                == entry_amount
            ):
                best_match = fingerprint_match
                best_confidence = GLMatchConfidence.EXACT
                match_note = "Même écriture source du Grand Livre"

            # 1. Try BC number match (normalized — digits only)
            if not best_match and entry.extracted_bc_number:
                amount = entry_amount
                bc_key = re.sub(r"\D", "", entry.extracted_bc_number)
                candidates = (
                    expenses_by_bc.get(bc_key, [])
                    or expenses_by_bc.get(entry.extracted_bc_number, [])
                )
                unmatched_bc = [
                    candidate
                    for candidate in candidates
                    if candidate.id not in matched_expense_ids
                    and candidate.amount.quantize(Decimal("0.01")) == amount
                ]
                if len(unmatched_bc) == 1:
                    best_match = unmatched_bc[0]
                    best_confidence = GLMatchConfidence.EXACT
                    match_note = (
                        f"BC #{entry.extracted_bc_number} + montant {amount}$"
                    )
                elif len(unmatched_bc) > 1:
                    ranked = _sort_expense_match_candidates(
                        unmatched_bc,
                        entry.date,
                    )
                    best_match = ranked[0]
                    best_confidence = GLMatchConfidence.EXACT
                    match_note = (
                        f"BC #{entry.extracted_bc_number} + montant {amount}$"
                    )

            # 2. Try exact amount match
            if not best_match:
                amount = entry_amount
                candidates = expenses_by_amount.get(
                    amount.quantize(Decimal("0.01")), []
                )
                unmatched_candidates = [
                    c for c in candidates if c.id not in matched_expense_ids
                ]

                if len(unmatched_candidates) == 1:
                    best_match = unmatched_candidates[0]
                    dates_match = bool(
                        entry.date
                        and best_match.entry_date
                        and entry.date == best_match.entry_date
                    )
                    best_confidence = (
                        GLMatchConfidence.EXACT
                        if dates_match
                        else GLMatchConfidence.PROBABLE
                    )
                    match_note = (
                        f"Montant et date exacts: {amount}$"
                        if dates_match
                        else f"Montant unique: {amount}$"
                    )
                elif len(unmatched_candidates) > 1:
                    # Multiple candidates — try date proximity
                    if entry.date:
                        sorted_cands = _sort_expense_match_candidates(
                            unmatched_candidates,
                            entry.date,
                        )
                        closest = sorted_cands[0]
                        days_diff = abs((closest.entry_date - entry.date).days)
                        if days_diff <= 60:
                            best_match = closest
                            best_confidence = GLMatchConfidence.PROBABLE
                            match_note = (
                                f"Montant {amount}$ + date proche "
                                f"(écart {days_diff}j)"
                            )

            # 3. For credit entries (refunds), try matching amount
            if not best_match and entry.credit > 0 and entry.debit == 0:
                cancelled = [
                    e for e in expenses
                    if e.is_cancellation
                    and abs(e.amount) == entry.credit
                    and e.id not in matched_expense_ids
                ]
                if len(cancelled) == 1:
                    best_match = cancelled[0]
                    best_confidence = GLMatchConfidence.EXACT
                    match_note = f"Crédit/annulation: {entry.credit}$"

            if best_match:
                entry.matched_expense = best_match
                entry.match_confidence = best_confidence
                entry.match_notes = match_note
                metadata = _entry_ai_metadata(entry)
                metadata.pop("semantic_match", None)
                entry.ai_metadata = metadata
                matched_expense_ids.add(best_match.id)
            else:
                entry.match_confidence = GLMatchConfidence.UNMATCHED
                metadata = _entry_ai_metadata(entry)
                metadata.pop("semantic_match", None)
                entry.ai_metadata = metadata
                entry.needs_import = True

        # 4. GPT semantic matching for remaining unmatched entries
        still_unmatched = [e for e in gl_entries if not e.matched_expense_id]
        unmatched_exps = [e for e in expenses if e.id not in matched_expense_ids]
        if use_ai and still_unmatched and unmatched_exps:
            gpt_gl = [
                {
                    "gl_id": e.pk,
                    "description": e.description_clean or e.description_raw,
                    "amount": str(effective_gl_amount(e, adjustments.get(e.pk))),
                    "bc_number": e.extracted_bc_number or "",
                }
                for e in still_unmatched
            ]
            gpt_exp = [
                {
                    "expense_id": e.pk,
                    "description": e.description,
                    "amount": str(e.amount),
                    "bon_number": e.bon_number or "",
                }
                for e in unmatched_exps
            ]
            exp_by_id = {e.pk: e for e in unmatched_exps}
            entry_by_id = {e.pk: e for e in still_unmatched}
            try:
                gpt_matches = _try_gpt_batch_match(gpt_gl, gpt_exp)
                for gm in gpt_matches:
                    entry = entry_by_id.get(gm["gl_id"])
                    exp = exp_by_id.get(gm["expense_id"])
                    amounts_match = bool(
                        entry
                        and exp
                        and effective_gl_amount(
                            entry,
                            adjustments.get(entry.pk),
                        ).quantize(Decimal("0.01"))
                        == exp.amount.quantize(Decimal("0.01"))
                    )
                    if (
                        entry
                        and exp
                        and exp.id not in matched_expense_ids
                        and amounts_match
                    ):
                        entry.matched_expense = exp
                        entry.match_confidence = GLMatchConfidence.PROBABLE
                        score_display = gm.get("confidence_score", AI_CONFIDENCE_MISSING)
                        entry.match_notes = (
                            "Correspondance sémantique "
                            f"(confiance {gm.get('confidence', '?')}; score IA {score_display}/9)"
                            if score_display != AI_CONFIDENCE_MISSING
                            else f"Correspondance sémantique (confiance {gm.get('confidence', '?')}; score IA NA)"
                        )
                        metadata = _entry_ai_metadata(entry)
                        metadata["semantic_match"] = {
                            "expense_id": exp.id,
                            "confidence": gm.get("confidence"),
                            "confidence_score": score_display,
                        }
                        entry.ai_metadata = metadata
                        entry.needs_import = False
                        matched_expense_ids.add(exp.id)
            except Exception:
                logger.exception("GPT semantic matching failed, continuing")

        GrandLivreEntry.objects.bulk_update(
            gl_entries,
            [
                "matched_expense_id",
                "match_confidence",
                "match_notes",
                "ai_metadata",
                "needs_import",
            ],
        )

        exact_expense_ids = {
            entry.matched_expense_id
            for entry in gl_entries
            if entry.matched_expense_id
            and entry.match_confidence == GLMatchConfidence.EXACT
        }
        if exact_expense_ids:
            Expense.objects.filter(pk__in=exact_expense_ids).update(
                validated_gl=True,
            )

        return matched_expense_ids

    @staticmethod
    def build_reconciliation(upload: GrandLivreUpload) -> ReconciliationResult:
        """Build the reconciliation result with balance check and anomaly detection."""
        budget_year = upload.budget_year

        gl_entries = list(upload.entries.all())
        adjustments = active_adjustments_by_entry(upload)
        matched = [e for e in gl_entries if e.matched_expense_id]
        unmatched_gl = [
            entry for entry in gl_entries if not entry.matched_expense_id
        ]

        # Expenses that exist in grille but not in GL
        matched_expense_ids = {e.matched_expense_id for e in matched}
        all_expenses = list(active_expenses_queryset(budget_year))
        missing_from_gl = [
            e for e in all_expenses if e.id not in matched_expense_ids
        ]

        gl_total = upload.gl_solde_fin or Decimal("0")
        adjustment_total = sum(
            (
                entry.net_amount
                - effective_gl_amount(entry, adjustments.get(entry.pk))
                for entry in gl_entries
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        adjusted_gl_total = (gl_total - adjustment_total).quantize(
            Decimal("0.01")
        )
        grille_total = sum(
            (expense.amount for expense in all_expenses),
            Decimal("0"),
        ).quantize(Decimal("0.01"))
        difference = adjusted_gl_total - grille_total

        # Anomaly detection
        anomalies = []

        # Sum of unmatched GL debits (expenses from GL not in grille)
        unmatched_gl_total = sum(
            (
                effective_gl_amount(entry, adjustments.get(entry.pk))
                for entry in unmatched_gl
            ),
            Decimal("0"),
        ).quantize(Decimal("0.01"))

        if adjustment_total != Decimal("0.00"):
            anomalies.append({
                "type": "active_adjustments",
                "severity": "warning",
                "message": (
                    f"Grand Livre source : {gl_total}$ · "
                    f"Ajustements actifs : -{adjustment_total}$ · "
                    f"Grand Livre après ajustements : {adjusted_gl_total}$."
                ),
            })

        # 1. Balance analysis with detailed breakdown
        gl_end = upload.period_end_date
        if not gl_end and gl_entries:
            gl_end = max(
                (e.date for e in gl_entries if e.date), default=None
            )

        if abs(difference) <= Decimal("0.01"):
            anomalies.append({
                "type": "balance_ok",
                "severity": "info",
                "message": (
                    f"Le compte balance parfaitement. "
                    f"Grand Livre après ajustements : {adjusted_gl_total}$ · "
                    f"Grille : {grille_total}$."
                ),
            })
        else:
            # Split missing_from_gl into "after GL end" (expected) vs
            # "before GL end" (should be in GL — potentially concerning).

            if gl_end:
                pending_after = [
                    e for e in missing_from_gl if e.entry_date > gl_end
                ]
                missing_before = [
                    e for e in missing_from_gl if e.entry_date <= gl_end
                ]
            else:
                pending_after = list(missing_from_gl)
                missing_before = []

            pending_after_total = sum(
                (e.amount for e in pending_after), Decimal("0")
            )
            missing_before_total = sum(
                (e.amount for e in missing_before), Decimal("0")
            )

            # Differences can be explained by:
            # - expenses added after the GL period ended
            # - expenses that are still not reflected in the GL, even if dated
            #   before the GL end date
            # - GL entries that exist in the accountant's file but not in the
            #   grille yet
            explained_missing_total = pending_after_total + missing_before_total
            adjusted_diff = difference + pending_after_total
            fully_adjusted = (
                difference + explained_missing_total - unmatched_gl_total
            )

            if abs(adjusted_diff) <= Decimal("0.01"):
                # Perfect: difference explained entirely by post-GL expenses
                anomalies.append({
                    "type": "balance_ok_with_pending",
                    "severity": "info",
                    "message": (
                        f"Grand Livre après ajustements : {adjusted_gl_total}$ · "
                        f"Grille : {grille_total}$ · "
                        f"Écart : {difference}$."
                    ),
                })
                anomalies.append({
                    "type": "balance_explanation",
                    "severity": "info",
                    "message": (
                        f"Le compte balance. L'écart s'explique par "
                        f"{len(pending_after)} dépense(s) ({pending_after_total}$) "
                        f"ajoutée(s) après la fin du GL ({gl_end})."
                    ),
                })
            elif abs(fully_adjusted) <= Decimal("0.01"):
                # Numbers balance once we account for not-yet-reflected grille
                # expenses and/or unmatched GL entries.
                has_attention_items = bool(unmatched_gl or missing_before)
                anom_type = (
                    "balance_check"
                    if unmatched_gl else "balance_ok_with_pending"
                )
                sev = "warning" if has_attention_items else "info"
                anomalies.append({
                    "type": anom_type,
                    "severity": sev,
                    "message": (
                        f"Grand Livre après ajustements : {adjusted_gl_total}$ · "
                        f"Grille : {grille_total}$ · "
                        f"Écart : {difference}$."
                    ),
                })
                parts = []
                if pending_after:
                    parts.append(
                        f"{len(pending_after)} dépense(s) grille "
                        f"({pending_after_total}$) postérieure(s) au GL"
                    )
                if missing_before:
                    parts.append(
                        f"{len(missing_before)} dépense(s) grille "
                        f"({missing_before_total}$) non encore reflétée(s) dans le GL"
                    )
                if unmatched_gl:
                    parts.append(
                        f"{len(unmatched_gl)} entrée(s) GL non rapprochée(s) "
                        f"({unmatched_gl_total}$)"
                    )
                msg_prefix = (
                    "L'écart s'explique arithmétiquement par : "
                    if unmatched_gl else
                    "Le compte balance. L'écart s'explique par : "
                )
                anomalies.append({
                    "type": "balance_explanation",
                    "severity": sev,
                    "message": msg_prefix + " et ".join(parts) + ".",
                })
            else:
                # Genuine mismatch — still show a breakdown
                anomalies.append({
                    "type": "balance_mismatch",
                    "severity": "warning",
                    "message": (
                        f"Grand Livre après ajustements : {adjusted_gl_total}$ · "
                        f"Grille : {grille_total}$ · "
                        f"Écart : {difference}$."
                    ),
                })
                parts = []
                if pending_after:
                    parts.append(
                        f"Dépenses grille postérieures au GL : "
                        f"{pending_after_total}$ ({len(pending_after)})"
                    )
                if missing_before:
                    parts.append(
                        f"Dépenses grille antérieures au GL non reflétées : "
                        f"{missing_before_total}$ ({len(missing_before)})"
                    )
                if unmatched_gl:
                    parts.append(
                        f"Entrées GL hors grille : "
                        f"{unmatched_gl_total}$ ({len(unmatched_gl)})"
                    )
                if parts:
                    parts.append(
                        f"Écart restant après les éléments listés : {fully_adjusted}$"
                    )
                    anomalies.append({
                        "type": "balance_explanation",
                        "severity": "warning",
                        "message": " · ".join(parts),
                    })

        # 2. Missing from GL for old dates
        if missing_from_gl and gl_end:
            old_missing = [
                e for e in missing_from_gl
                if e.entry_date <= gl_end
            ]
            if old_missing:
                anomalies.append({
                    "type": "missing_old_expenses",
                    "severity": "warning",
                    "message": (
                        f"{len(old_missing)} dépense(s) de la grille datent "
                        f"d'avant la fin du GL ({gl_end}) mais ne sont "
                        f"pas reflétées dans le Grand Livre."
                    ),
                    "expense_ids": [e.id for e in old_missing[:10]],
                })

        # 3. Unmatched GL entries with large amounts
        large_unmatched = [
            entry
            for entry in unmatched_gl
            if abs(effective_gl_amount(entry, adjustments.get(entry.pk)))
            > Decimal("500")
        ]
        if large_unmatched:
            anomalies.append({
                "type": "large_unmatched",
                "severity": "warning",
                "message": (
                    f"{len(large_unmatched)} entrée(s) du GL > 500$ n'ont pas "
                    f"de correspondance dans la grille."
                ),
            })

        # 4. Check for duplicate amounts across GL entries
        from collections import Counter
        gl_amounts = Counter(e.debit for e in gl_entries if e.debit > 0)
        duplicated = {amt: cnt for amt, cnt in gl_amounts.items() if cnt >= 3}
        if duplicated:
            for amt, cnt in duplicated.items():
                anomalies.append({
                    "type": "repeated_amount",
                    "severity": "low",
                    "message": (
                        f"Le montant {amt}$ apparaît {cnt} fois dans le GL."
                    ),
                })

        # Determine status light
        warning_anomalies = [
            a for a in anomalies
            if a["severity"] in ("warning", "high")
        ]
        if not anomalies or all(a["severity"] in ("info", "low") for a in anomalies):
            status_light = "green"
        elif warning_anomalies:
            status_light = "yellow"
        else:
            status_light = "yellow"

        # Books balance if difference is zero, or if all anomalies are info-level
        # (meaning the gap is fully explained by post-GL expenses).
        has_balance_ok = any(
            a["type"] in ("balance_ok", "balance_ok_with_pending")
            for a in anomalies
        )
        is_balanced = abs(difference) <= Decimal("0.01") or has_balance_ok

        result, _ = ReconciliationResult.objects.update_or_create(
            upload=upload,
            defaults={
                "gl_total": gl_total,
                "adjustment_total": adjustment_total,
                "adjusted_gl_total": adjusted_gl_total,
                "grille_total": grille_total,
                "difference": difference,
                "matched_count": len(matched),
                "unmatched_gl_count": len(unmatched_gl),
                "missing_from_gl_count": len(missing_from_gl),
                "is_balanced": is_balanced,
                "anomalies": anomalies,
                "status_light": status_light,
            },
        )

        upload.status = GLUploadStatus.RECONCILED
        upload.save()

        return result

    @staticmethod
    def analyze_with_ai(upload: GrandLivreUpload):
        """Run GPT analysis and merge results into structured anomalies."""
        try:
            result = upload.reconciliation
        except ReconciliationResult.DoesNotExist:
            return

        gl_entries = list(upload.entries.values(
            "description_raw", "debit", "credit", "match_confidence",
        )[:50])
        expenses = list(
            active_expenses_queryset(upload.budget_year)
            .values("description", "amount", "entry_date")[:50]
        )

        extra_anomalies = _try_gpt_anomaly_analysis(
            result,
            result.anomalies,
            gl_entries,
            expenses,
        )
        if extra_anomalies:
            merged = list(result.anomalies or []) + extra_anomalies
            result.anomalies = merged
            result.save()

    @staticmethod
    @transaction.atomic
    def import_validated_entries(upload: GrandLivreUpload, entry_ids: list):
        """Import validated GL entries as new expenses in the grille."""
        _validate_upload_account(upload)
        entries = list(upload.entries.filter(
            id__in=entry_ids,
            is_validated=True,
            needs_import=True,
            matched_expense__isnull=True,
        ).select_related("upload").order_by("row_number"))
        budget_year = upload.budget_year
        adjustments = active_adjustments_by_entry(upload)

        # Pre-build lookup of existing expenses to prevent duplicates
        existing_expenses = list(active_expenses_queryset(budget_year))
        existing_by_bc = {}
        for exp in existing_expenses:
            if exp.bon_number and exp.bon_number.strip() not in ("", "n/a"):
                norm = re.sub(r"\D", "", exp.bon_number)
                if norm:
                    existing_by_bc[norm] = exp
        existing_by_amount = {}
        for exp in existing_expenses:
            key = exp.amount.quantize(Decimal("0.01"))
            existing_by_amount.setdefault(key, []).append(exp)
        existing_by_fingerprint = {
            exp.gl_source_fingerprint: exp
            for exp in existing_expenses
            if exp.gl_source_fingerprint
        }

        # Unclassified accountant entries must not consume the contingency.
        default_sub, _ = SubBudget.objects.get_or_create(
            budget_year=budget_year,
            trace_code=99,
            defaults={
                "name": "Autre dépenses",
                "sort_order": 99,
                "is_contingency": False,
                "planned_amount": Decimal("0.00"),
            },
        )

        created = []
        skipped = 0
        for entry in entries:
            amount = effective_gl_amount(
                entry,
                adjustments.get(entry.pk),
            ).quantize(Decimal("0.01"))
            # ── Duplicate guard: check if this entry already has an equivalent ──
            fingerprint = _gl_entry_fingerprint(entry)
            duplicate = existing_by_fingerprint.get(fingerprint)
            if not duplicate and entry.extracted_bc_number:
                bc_key = re.sub(r"\D", "", entry.extracted_bc_number)
                bc_candidate = existing_by_bc.get(bc_key)
                if (
                    bc_candidate
                    and bc_candidate.amount.quantize(Decimal("0.01")) == amount
                ):
                    duplicate = bc_candidate

            if not duplicate:
                amt_key = amount.quantize(Decimal("0.01"))
                same_amount = existing_by_amount.get(amt_key, [])
                if entry.date:
                    for cand in same_amount:
                        if abs((cand.entry_date - entry.date).days) <= 30:
                            duplicate = cand
                            break

            if duplicate:
                # Link the GL entry to the existing expense instead of creating
                entry.matched_expense = duplicate
                entry.match_confidence = GLMatchConfidence.EXACT
                entry.match_notes = f"Doublon évité — lié à la dépense existante #{duplicate.pk}"
                entry.needs_import = False
                entry.save()
                skipped += 1
                continue

            # ── Create new expense ──
            sub_budget = _resolve_gl_import_sub_budget(
                budget_year,
                entry,
                existing_by_bc=existing_by_bc,
                fallback_sub_budget=default_sub,
            )
            desc = entry.description_clean or _clean_gl_import_description(entry)
            expense = Expense(
                budget_year=budget_year,
                sub_budget=sub_budget,
                entry_date=entry.date or budget_year.created_at.date(),
                description=desc[:255],
                bon_number=entry.extracted_bc_number or "n/a",
                validated_gl=True,
                supplier_name=entry.source,
                spent_by_label=_gl_import_spent_by_label(budget_year, entry),
                amount=amount,
                source_type=ExpenseSourceType.GL_IMPORT,
                gl_source_fingerprint=fingerprint,
                entered_by=upload.uploaded_by,
                is_cancellation=False,
                notes=(
                    f"Import automatique du Grand Livre {upload.account_number}, "
                    f"téléversement #{upload.pk}, ligne {entry.row_number}."
                ),
            )
            expense.save()
            entry.matched_expense = expense
            entry.match_confidence = GLMatchConfidence.EXACT
            entry.needs_import = False
            entry.save()
            from audits.services import create_audit_log_entry
            create_audit_log_entry(
                action="grand_livre.entry_materialized",
                target=expense,
                summary=(
                    f"Écriture GL {upload.account_number} ligne "
                    f"{entry.row_number} matérialisée automatiquement"
                ),
                actor=upload.uploaded_by,
                payload={
                    "upload_id": upload.pk,
                    "gl_entry_id": entry.pk,
                    "row_number": entry.row_number,
                    "account_number": upload.account_number,
                    "source_date": entry.date.isoformat() if entry.date else None,
                    "source_description": entry.description_raw,
                    "source_debit": str(entry.debit),
                    "source_credit": str(entry.credit),
                    "effective_amount": str(amount),
                    "source_fingerprint": fingerprint,
                    "sub_budget_trace": sub_budget.trace_code,
                },
            )
            created.append(expense)
            existing_expenses.append(expense)
            existing_by_fingerprint[fingerprint] = expense
            existing_by_amount.setdefault(amount, []).append(expense)
            if entry.extracted_bc_number:
                bc_key = re.sub(r"\D", "", entry.extracted_bc_number)
                if bc_key:
                    existing_by_bc[bc_key] = expense

        return created, skipped

    @staticmethod
    @transaction.atomic
    def materialize_unmatched_entries(upload: GrandLivreUpload):
        """Automatically add every unmatched row from the house account."""
        _validate_upload_account(upload)
        pending_ids = list(
            upload.entries.filter(
                needs_import=True,
                matched_expense__isnull=True,
            ).values_list("pk", flat=True)
        )
        if not pending_ids:
            return [], 0
        upload.entries.filter(pk__in=pending_ids).update(is_validated=True)
        return GrandLivreReconciliationService.import_validated_entries(
            upload,
            pending_ids,
        )

    @staticmethod
    @transaction.atomic
    def sync_materialized_import_context(upload: GrandLivreUpload):
        """Update only GL-import attribution from current source context."""
        apartment_repairs = upload.budget_year.sub_budgets.filter(
            trace_code=1
        ).first()
        entries = list(
            upload.entries.filter(
                matched_expense__source_type=ExpenseSourceType.GL_IMPORT,
            ).select_related(
                "matched_expense",
                "matched_expense__sub_budget",
            ).order_by("row_number")
        )
        changed_count = 0
        for entry in entries:
            expense = entry.matched_expense
            desired_spent_by = (
                expense.spent_by_label
                if expense.gl_spent_by_override
                else _gl_import_spent_by_label(upload.budget_year, entry)
            )
            old_values = {
                "spent_by_label": expense.spent_by_label,
                "sub_budget_trace": expense.sub_budget.trace_code,
            }
            update_fields = []
            if expense.spent_by_label != desired_spent_by:
                expense.spent_by_label = desired_spent_by
                update_fields.append("spent_by_label")
            if (
                entry.extracted_apartment
                and apartment_repairs
                and expense.sub_budget_id != apartment_repairs.pk
            ):
                expense.sub_budget = apartment_repairs
                update_fields.append("sub_budget")
            if not update_fields:
                continue

            expense.save(update_fields=[*update_fields, "updated_at"])
            from audits.services import create_audit_log_entry
            create_audit_log_entry(
                action="grand_livre.import_context_synchronized",
                target=expense,
                summary=(
                    f"Attribution GL ligne {entry.row_number} synchronisée "
                    f"pour {upload.account_number}"
                ),
                actor=upload.uploaded_by,
                payload={
                    "upload_id": upload.pk,
                    "gl_entry_id": entry.pk,
                    "row_number": entry.row_number,
                    "period": entry.period,
                    "apartment": entry.extracted_apartment,
                    "apartment_context": _entry_ai_metadata(entry).get(
                        "apartment_context",
                        {},
                    ),
                    "before": old_values,
                    "after": {
                        "spent_by_label": expense.spent_by_label,
                        "sub_budget_trace": expense.sub_budget.trace_code,
                    },
                },
            )
            changed_count += 1
        return changed_count

    @staticmethod
    def full_reconciliation(upload: GrandLivreUpload):
        """Run the complete reconciliation pipeline."""
        # Step 1: Parse
        section = GrandLivreReconciliationService.parse_and_store(upload)
        if upload.status == GLUploadStatus.ERROR:
            return

        # Step 2: AI enrichment (optional, non-blocking)
        try:
            GrandLivreReconciliationService.enrich_with_ai(upload)
        except Exception:
            logger.exception("AI enrichment failed, continuing")

        # Step 3: Match
        GrandLivreReconciliationService.match_expenses(upload)

        # Step 4: Apply apartment/house context to prior GL imports.
        GrandLivreReconciliationService.sync_materialized_import_context(upload)

        # Step 5: Materialize every remaining row from the house's account.
        GrandLivreReconciliationService.materialize_unmatched_entries(upload)

        # Step 6: Build reconciliation result
        result = GrandLivreReconciliationService.build_reconciliation(upload)

        # Step 7: AI analysis (optional, non-blocking)
        try:
            GrandLivreReconciliationService.analyze_with_ai(upload)
        except Exception:
            logger.exception("AI analysis failed, continuing")

        return result
