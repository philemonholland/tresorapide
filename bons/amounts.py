from decimal import Decimal, InvalidOperation

MONEY_EPSILON = Decimal("0.01")
STANDARD_TPS_RATE = Decimal("0.05")
STANDARD_TVQ_RATE = Decimal("0.09975")
AMOUNT_FIELD_KEYS = (
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
)


def money(value):
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value)).quantize(MONEY_EPSILON)
    except (InvalidOperation, TypeError, ValueError):
        return None


def standard_tax_breakdown(subtotal, untaxed_extra_amount=None):
    subtotal = money(subtotal)
    if subtotal is None:
        return None
    untaxed_extra_amount = money(untaxed_extra_amount) or Decimal("0.00")
    tps = (subtotal * STANDARD_TPS_RATE).quantize(MONEY_EPSILON)
    tvq = (subtotal * STANDARD_TVQ_RATE).quantize(MONEY_EPSILON)
    total = (subtotal + tps + tvq + untaxed_extra_amount).quantize(MONEY_EPSILON)
    return {
        "subtotal": subtotal,
        "tps": tps,
        "tvq": tvq,
        "untaxed_extra_amount": untaxed_extra_amount,
        "total": total,
    }


def build_amount_consistency_warning(
    *,
    subtotal=None,
    tps=None,
    tvq=None,
    total=None,
    untaxed_extra_amount=None,
):
    subtotal = money(subtotal)
    total = money(total)
    actual_tps = money(tps)
    actual_tvq = money(tvq)
    actual_untaxed_extra = money(untaxed_extra_amount) or Decimal("0.00")
    if subtotal is None or total is None:
        return None

    expected = standard_tax_breakdown(subtotal, actual_untaxed_extra)
    if expected is None:
        return None

    entered_sum = (
        subtotal
        + (actual_tps or Decimal("0.00"))
        + (actual_tvq or Decimal("0.00"))
        + actual_untaxed_extra
    ).quantize(MONEY_EPSILON)
    sum_mismatch = abs(entered_sum - total) > MONEY_EPSILON
    taxes_should_exist = abs(expected["total"] - total) <= MONEY_EPSILON
    missing_tps = actual_tps is None and taxes_should_exist and expected["tps"] > Decimal("0.00")
    missing_tvq = actual_tvq is None and taxes_should_exist and expected["tvq"] > Decimal("0.00")
    tps_mismatch = actual_tps is not None and abs(actual_tps - expected["tps"]) > MONEY_EPSILON
    tvq_mismatch = actual_tvq is not None and abs(actual_tvq - expected["tvq"]) > MONEY_EPSILON

    if not any((sum_mismatch, missing_tps, missing_tvq, tps_mismatch, tvq_mismatch)):
        return None

    issues = []
    if sum_mismatch:
        issues.append(
            f"La somme des montants extraits donne {entered_sum:.2f} $ au lieu du total extrait {total:.2f} $."
        )
    missing_labels = []
    if missing_tps:
        missing_labels.append("TPS")
    if missing_tvq:
        missing_labels.append("TVQ")
    if missing_labels:
        issues.append(f"Taxe(s) extraite(s) manquante(s) : {', '.join(missing_labels)}.")
    mismatch_labels = []
    if tps_mismatch:
        mismatch_labels.append("TPS")
    if tvq_mismatch:
        mismatch_labels.append("TVQ")
    if mismatch_labels:
        issues.append(
            f"Taxe(s) incohérente(s) avec le sous-total : {', '.join(mismatch_labels)}."
        )

    return {
        "subtotal": f"{subtotal:.2f}",
        "entered_sum": f"{entered_sum:.2f}",
        "total": f"{total:.2f}",
        "actual_tps": f"{actual_tps:.2f}" if actual_tps is not None else "N/A",
        "expected_tps": f"{expected['tps']:.2f}",
        "actual_tvq": f"{actual_tvq:.2f}" if actual_tvq is not None else "N/A",
        "expected_tvq": f"{expected['tvq']:.2f}",
        "actual_untaxed_extra_amount": f"{actual_untaxed_extra:.2f}",
        "expected_total": f"{expected['total']:.2f}",
        "sum_mismatch": sum_mismatch,
        "missing_tps": missing_tps,
        "missing_tvq": missing_tvq,
        "tps_mismatch": tps_mismatch,
        "tvq_mismatch": tvq_mismatch,
        "issues": issues,
    }


def cap_amount_confidence_scores(scores, warning):
    if not isinstance(scores, dict):
        return {}
    if not warning:
        return dict(scores)

    updated = dict(scores)
    cap = 3 if (
        warning.get("sum_mismatch")
        or warning.get("missing_tps")
        or warning.get("missing_tvq")
    ) else 4
    for key in AMOUNT_FIELD_KEYS:
        score = updated.get(key)
        if isinstance(score, int):
            updated[key] = min(score, cap)
    return updated
