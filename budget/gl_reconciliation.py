"""
Grand Livre reconciliation service.

Matches parsed GL entries against existing expenses, uses AI for fuzzy matching,
extracts apartment/BC numbers from GL descriptions, and performs balance checks.
"""
import json
import logging
import re
from decimal import Decimal
from typing import List, Optional

from django.conf import settings
from django.db import transaction
from django.db.models import Sum

from .models import (
    BudgetYear, Expense, ExpenseSourceType, SubBudget,
    GrandLivreUpload, GrandLivreEntry, ReconciliationResult,
    GLMatchConfidence, GLUploadStatus,
)
from .gl_parser import GLTransaction, GLAccountSection, parse_grand_livre
from .services import BudgetCalculationService

logger = logging.getLogger(__name__)
CHCE_LABEL = "CHCE"

# ── Regex patterns for extracting BC/apartment from GL descriptions ──
BC_PATTERN = re.compile(r"BC\s*#?\s*(\d{4,7})", re.IGNORECASE)
APT_PATTERN = re.compile(r"#\s*(\d{3})")
RECEIPT_NUM_PATTERN = re.compile(r"^(\d{4,10})-")


def _extract_bc_number(description: str) -> str:
    """Extract BC number from GL description like '496578-BC 16482-scellant'."""
    m = BC_PATTERN.search(description)
    return m.group(1) if m else ""


def _extract_apartment(description: str) -> str:
    """Extract apartment from GL description like 'tuyauéchangeur#104'."""
    m = APT_PATTERN.search(description)
    return m.group(1) if m else ""


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


def _try_gpt_parse_entries(entries: List[dict]) -> List[dict]:
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
[{"row": N, "bc_number": "16482" ou "", "apartment": "104" ou "", "description_clean": "Scellant, mortier et truelle"}]

Exemples de descriptions codées:
- "496578-BC 16482-scellant, mortier, truelle" → BC=16482, apt=, desc="Scellant, mortier et truelle"
- "492428-BC168377-tuyauxéchangeur#104" → BC=168377, apt=104, desc="Tuyaux pour échangeur d'air"
- "INVENTAIRE BB-204 - Cartouche-poignée bain" → BC=, apt=204, desc="Inventaire: cartouche et poignée de bain"
- "001-Tontegazonx2x60" → BC=, apt=, desc="Tonte de gazon (2x 60$)"
- "Terminix - 4528861-factannuelle-du01sept25au31août26" → BC=, apt=, desc="Terminix, facture annuelle du 1er sept. 2025 au 31 août 2026"
- "060768-inspection visuelle (extincteurs)" → BC=, apt=, desc="Inspection visuelle des extincteurs"
- "511888-BC137940-Colleplomberie#102" → BC=137940, apt=102, desc="Colle de plomberie"

Réponds UNIQUEMENT avec le JSON, sans markdown."""

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
        row_map = {item["row"]: item for item in parsed}

        for e in entries:
            gpt = row_map.get(e["row_number"], {})
            if gpt.get("bc_number"):
                e["extracted_bc_number"] = gpt["bc_number"]
            if gpt.get("apartment"):
                e["extracted_apartment"] = gpt["apartment"]
            if gpt.get("description_clean"):
                e["description_clean"] = gpt["description_clean"]
    except Exception:
        logger.exception("GPT enrichment of GL entries failed")

    return entries


def _try_gpt_batch_match(
    unmatched_gl: List[dict],
    unmatched_expenses: List[dict],
) -> List[dict]:
    """Use GPT to find matches between GL entries and expenses by semantic similarity.

    Each GL entry has: id, description, amount, bc_number
    Each expense has: id, description, amount, bon_number

    Returns list of dicts: [{"gl_id": int, "expense_id": int, "confidence": float}, ...]
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
[{"gl_id": N, "expense_id": N, "confidence": 0.9}]
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
            return [
                m for m in parsed
                if isinstance(m, dict)
                and "gl_id" in m and "expense_id" in m
                and m.get("confidence", 0) >= 0.7
            ]
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

    Returns a list of dicts: [{"type": str, "severity": str, "message": str}, ...]
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

        entries_to_create = []
        for tx in section.transactions:
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

        GrandLivreEntry.objects.bulk_create(entries_to_create)
        return section

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
        row_map = {d["row_number"]: d for d in enriched}

        for entry in entries:
            data = row_map.get(entry.row_number, {})
            if data.get("description_clean"):
                entry.description_clean = data["description_clean"]
            if data.get("extracted_bc_number") and not entry.extracted_bc_number:
                entry.extracted_bc_number = data["extracted_bc_number"]
            if data.get("extracted_apartment") and not entry.extracted_apartment:
                entry.extracted_apartment = data["extracted_apartment"]

        GrandLivreEntry.objects.bulk_update(
            entries,
            ["description_clean", "extracted_bc_number", "extracted_apartment"],
        )

    @staticmethod
    def match_expenses(upload: GrandLivreUpload):
        """Match GL entries against existing expenses."""
        upload.status = GLUploadStatus.RECONCILING
        upload.save()

        budget_year = upload.budget_year
        expenses = list(
            Expense.objects.filter(budget_year=budget_year)
            .exclude(is_cancellation=True)
            .select_related("sub_budget")
        )

        # Build a lookup by amount for fast matching
        expenses_by_amount = {}
        for exp in expenses:
            key = exp.amount.quantize(Decimal("0.01"))
            expenses_by_amount.setdefault(key, []).append(exp)

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

        gl_entries = list(upload.entries.all())
        matched_expense_ids = set()

        for entry in gl_entries:
            if entry.matched_expense_id:
                matched_expense_ids.add(entry.matched_expense_id)
                continue

            best_match = None
            best_confidence = GLMatchConfidence.UNMATCHED
            match_note = ""

            # 1. Try BC number match (normalized — digits only)
            if entry.extracted_bc_number:
                bc_key = re.sub(r"\D", "", entry.extracted_bc_number)
                candidates = (
                    expenses_by_bc.get(bc_key, [])
                    or expenses_by_bc.get(entry.extracted_bc_number, [])
                )
                unmatched_bc = [c for c in candidates if c.id not in matched_expense_ids]
                if len(unmatched_bc) == 1:
                    best_match = unmatched_bc[0]
                    best_confidence = GLMatchConfidence.EXACT
                    match_note = f"BC #{entry.extracted_bc_number} match"
                elif len(unmatched_bc) > 1:
                    # Tiebreak by amount
                    amount = entry.debit if entry.debit else entry.credit
                    amt_match = [
                        c for c in unmatched_bc
                        if c.amount.quantize(Decimal("0.01")) == amount.quantize(Decimal("0.01"))
                    ]
                    if amt_match:
                        ranked = _sort_expense_match_candidates(
                            amt_match,
                            entry.date,
                        )
                        best_match = ranked[0]
                        best_confidence = GLMatchConfidence.EXACT
                        match_note = (
                            f"BC #{entry.extracted_bc_number} + montant {amount}$"
                        )

            # 2. Try exact amount match
            if not best_match:
                amount = entry.debit if entry.debit else entry.credit
                candidates = expenses_by_amount.get(
                    amount.quantize(Decimal("0.01")), []
                )
                unmatched_candidates = [
                    c for c in candidates if c.id not in matched_expense_ids
                ]

                if len(unmatched_candidates) == 1:
                    best_match = unmatched_candidates[0]
                    best_confidence = GLMatchConfidence.EXACT
                    match_note = f"Montant unique: {amount}$"
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
                matched_expense_ids.add(best_match.id)
            else:
                entry.match_confidence = GLMatchConfidence.UNMATCHED
                entry.needs_import = True

        # 4. GPT semantic matching for remaining unmatched entries
        still_unmatched = [e for e in gl_entries if not e.matched_expense_id]
        unmatched_exps = [e for e in expenses if e.id not in matched_expense_ids]
        if still_unmatched and unmatched_exps:
            gpt_gl = [
                {
                    "gl_id": e.pk,
                    "description": e.description_clean or e.description_raw,
                    "amount": str(e.debit if e.debit else e.credit),
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
                    if entry and exp and exp.id not in matched_expense_ids:
                        entry.matched_expense = exp
                        entry.match_confidence = GLMatchConfidence.PROBABLE
                        entry.match_notes = f"Correspondance sémantique (confiance {gm.get('confidence', '?')})"
                        entry.needs_import = False
                        matched_expense_ids.add(exp.id)
            except Exception:
                logger.exception("GPT semantic matching failed, continuing")

        GrandLivreEntry.objects.bulk_update(
            gl_entries,
            ["matched_expense_id", "match_confidence", "match_notes", "needs_import"],
        )

        return matched_expense_ids

    @staticmethod
    def build_reconciliation(upload: GrandLivreUpload) -> ReconciliationResult:
        """Build the reconciliation result with balance check and anomaly detection."""
        budget_year = upload.budget_year
        base = BudgetCalculationService.base_values(budget_year)

        gl_entries = list(upload.entries.all())
        matched = [e for e in gl_entries if e.matched_expense_id]
        unmatched_gl = [e for e in gl_entries if not e.matched_expense_id]

        # Expenses that exist in grille but not in GL
        matched_expense_ids = {e.matched_expense_id for e in matched}
        all_expenses = Expense.objects.filter(
            budget_year=budget_year
        ).exclude(is_cancellation=True)
        missing_from_gl = [
            e for e in all_expenses if e.id not in matched_expense_ids
        ]

        gl_total = upload.gl_solde_fin or Decimal("0")
        grille_total = base["expenses_to_date"]
        difference = gl_total - grille_total

        # Anomaly detection
        anomalies = []

        # Sum of unmatched GL debits (expenses from GL not in grille)
        unmatched_gl_total = sum(
            (e.debit for e in unmatched_gl if e.debit > 0), Decimal("0")
        )

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
                    f"Solde GL : {gl_total}$ · Grille : {grille_total}$."
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
                        f"Solde GL : {gl_total}$ · Grille : {grille_total}$ · "
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
                        f"Solde GL : {gl_total}$ · Grille : {grille_total}$ · "
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
                        f"Solde GL : {gl_total}$ · Grille : {grille_total}$ · "
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
                        f"Écart résiduel inexpliqué : {fully_adjusted}$"
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
            e for e in unmatched_gl if e.debit > Decimal("500")
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
        expenses = list(Expense.objects.filter(
            budget_year=upload.budget_year,
        ).values("description", "amount", "entry_date")[:50])

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
        entries = upload.entries.filter(
            id__in=entry_ids,
            is_validated=True,
            needs_import=True,
            matched_expense__isnull=True,
        )
        budget_year = upload.budget_year

        # Pre-build lookup of existing expenses to prevent duplicates
        existing_expenses = list(
            Expense.objects.filter(budget_year=budget_year)
            .exclude(is_cancellation=True)
        )
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

        # Get or create a default sub-budget for GL imports
        default_sub, _ = SubBudget.objects.get_or_create(
            budget_year=budget_year,
            trace_code=0,
            defaults={
                "name": "Imprévues",
                "is_contingency": True,
                "planned_amount": budget_year.imprevues_amount,
            },
        )

        created = []
        skipped = 0
        for entry in entries:
            # ── Duplicate guard: check if this entry already has an equivalent ──
            duplicate = None
            if entry.extracted_bc_number:
                bc_key = re.sub(r"\D", "", entry.extracted_bc_number)
                duplicate = existing_by_bc.get(bc_key)

            if not duplicate:
                amount = entry.debit if entry.debit else entry.credit
                amt_key = amount.quantize(Decimal("0.01"))
                same_amount = existing_by_amount.get(amt_key, [])
                if len(same_amount) == 1:
                    duplicate = same_amount[0]
                elif len(same_amount) > 1 and entry.date:
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
            sub_budget = default_sub
            if entry.extracted_apartment:
                apt_sub = SubBudget.objects.filter(
                    budget_year=budget_year, trace_code=1,
                ).first()
                if apt_sub:
                    sub_budget = apt_sub

            desc = entry.description_clean or entry.description_raw
            expense = Expense(
                budget_year=budget_year,
                sub_budget=sub_budget,
                entry_date=entry.date or budget_year.created_at.date(),
                description=desc[:255],
                bon_number=entry.extracted_bc_number or "n/a",
                validated_gl=True,
                supplier_name=entry.source,
                spent_by_label=CHCE_LABEL,
                amount=entry.debit if entry.debit else -entry.credit,
                source_type=ExpenseSourceType.GL_IMPORT,
                is_cancellation=entry.credit > 0 and entry.debit == 0,
            )
            expense.save()
            entry.matched_expense = expense
            entry.match_confidence = GLMatchConfidence.EXACT
            entry.needs_import = False
            entry.save()
            created.append(expense)

        return created, skipped

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

        # Step 4: Build reconciliation result
        result = GrandLivreReconciliationService.build_reconciliation(upload)

        # Step 5: AI analysis (optional, non-blocking)
        try:
            GrandLivreReconciliationService.analyze_with_ai(upload)
        except Exception:
            logger.exception("AI analysis failed, continuing")

        return result
