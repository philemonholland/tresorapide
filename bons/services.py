from django.db.models import Max


def generate_bon_number(house, year):
    """
    Generate the next bon de commande number for a house and year.
    Format: HHYYNNNN (e.g., BB260001)
    - HH: 2-char house code
    - YY: last 2 digits of year
    - NNNN: 4-digit sequential, resets to 0001 each year
    """
    from bons.models import BonDeCommande

    prefix = f"{house.code}{str(year)[-2:]}"
    last_bon = BonDeCommande.objects.filter(
        house=house, number__startswith=prefix
    ).order_by("-number").first()

    if last_bon:
        seq = int(last_bon.number[4:]) + 1
    else:
        seq = 1

    if seq > 9999:
        raise ValueError(f"Bon de commande sequence overflow for {prefix} (max 9999)")

    return f"{prefix}{seq:04d}"
