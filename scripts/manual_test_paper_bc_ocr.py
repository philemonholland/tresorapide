"""Test GPT Vision paper BC detection with example PDFs."""
import os
import sys
import base64
import io
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

import django
django.setup()

from django.conf import settings
from pdf2image import convert_from_path
from PIL import Image, ImageDraw, ImageFont
from openai import OpenAI

TARGET_WIDTH = 1200
BANNER_HEIGHT = 40
PADDING = 10

TEST_PROMPT = """\
Tu es un assistant spécialisé dans l'extraction de données de documents \
financiers pour une coopérative d'habitation au Québec.

L'image contient les pages d'un ou plusieurs documents. Chaque page est \
précédée d'un bandeau avec le nom du fichier et le numéro de page.

Pour CHAQUE document distinct dans l'image, classifie-le et extrais les données.

Types de documents:
1. "paper_bc" — Bon de commande papier officiel de la coopérative. Se reconnaît par:
   - Le logo ou nom de la coopérative en haut à gauche
   - "Notre numéro de commande" avec un numéro en haut à droite
   - Des lignes pour description, quantité, prix
   - Nom du fournisseur ou personne à rembourser
2. "invoice" — Facture d'un fournisseur (soumission, facture détaillée, reçu \
de travaux). Souvent associée à un bon de commande papier.
3. "receipt" — Reçu de caisse d'un magasin (ex: quincaillerie, épicerie). \
Achat fait par un membre qui se fait rembourser.

Retourne UNIQUEMENT un tableau JSON. Chaque élément correspond à UN document:
[
  {
    "filename": "BC16011.pdf - Page 1",
    "document_type": "paper_bc",
    "bc_number": "16011",
    "associated_bc_number": "",
    "supplier_name": "Nom du fournisseur",
    "supplier_address": "Adresse",
    "member_name": "",
    "apartment_number": "",
    "merchant": "",
    "purchase_date": "YYYY-MM-DD",
    "subtotal": 0.00,
    "tps": 0.00,
    "tvq": 0.00,
    "total": 0.00,
    "summary": "Description des travaux/achats",
    "line_items": [{"description": "...", "quantity": 1, "unit_price": 0.00, "total": 0.00}]
  }
]

Règles:
- document_type: "paper_bc", "invoice", ou "receipt"
- bc_number: numéro du bon de commande (sous "Notre numéro de commande"). \
Seulement pour paper_bc.
- associated_bc_number: si c'est une invoice liée à un paper_bc, mettre le \
numéro du BC. Si pas de BC associé, laisser vide.
- supplier_name: pour paper_bc et invoice, le nom du fournisseur.
- member_name/apartment_number: seulement pour receipt (achat par un membre).
- Si un document s'étend sur plusieurs pages consécutives, combiner en UNE \
entrée JSON (utiliser le filename de la première page).
- line_items: les lignes détaillées du bon de commande ou facture. Peut être [].
- Les montants en nombre décimal (pas de $).
- Retourne UNIQUEMENT le JSON, sans texte additionnel.
"""


def pdf_to_composite(pdf_path):
    """Convert PDF pages to a labeled composite image."""
    images = convert_from_path(pdf_path, dpi=200)
    basename = os.path.basename(pdf_path)
    panels = []

    for i, img in enumerate(images):
        if img.mode != "RGB":
            img = img.convert("RGB")
        ratio = TARGET_WIDTH / img.width
        new_h = int(img.height * ratio)
        img = img.resize((TARGET_WIDTH, new_h), Image.LANCZOS)

        banner = Image.new("RGB", (TARGET_WIDTH, BANNER_HEIGHT), (40, 40, 40))
        draw = ImageDraw.Draw(banner)
        try:
            font = ImageFont.truetype(
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22
            )
        except (OSError, IOError):
            font = ImageFont.load_default()
        label = f"{basename} - Page {i + 1}"
        draw.text((10, 8), label, fill=(255, 255, 255), font=font)
        panels.append(banner)
        panels.append(img)

    total_height = sum(p.height for p in panels) + PADDING * (len(panels) - 1)
    composite = Image.new("RGB", (TARGET_WIDTH, total_height), (255, 255, 255))
    y = 0
    for panel in panels:
        composite.paste(panel, (0, y))
        y += panel.height + PADDING

    buf = io.BytesIO()
    composite.save(buf, format="PNG", optimize=True)
    page_labels = [f"{basename} - Page {i + 1}" for i in range(len(images))]
    return buf.getvalue(), page_labels


def test_pdf(pdf_path):
    print(f"\n{'='*60}")
    print(f"Testing: {pdf_path}")
    print(f"File size: {os.path.getsize(pdf_path)} bytes")

    composite_bytes, page_labels = pdf_to_composite(pdf_path)
    image_b64 = base64.b64encode(composite_bytes).decode("utf-8")
    print(f"Composite: {len(composite_bytes)} bytes, pages: {page_labels}")

    client = OpenAI(api_key=settings.OPENAI_API_KEY)
    model = "gpt-5.4"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": TEST_PROMPT},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            f"Cette image contient {len(page_labels)} pages du fichier "
                            f"{os.path.basename(pdf_path)}. "
                            "Analyse chaque page et classifie les documents."
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                    },
                ],
            },
        ],
        max_completion_tokens=3000,
        temperature=0,
    )

    raw = response.choices[0].message.content.strip()
    print(f"\n--- GPT Response ---")
    print(raw)

    # Parse and validate
    text = raw
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    try:
        data = json.loads(text)
        if isinstance(data, dict):
            data = [data]
        print(f"\n--- Parsed: {len(data)} document(s) ---")
        for d in data:
            print(f"  Type: {d.get('document_type')}")
            print(f"  BC#: {d.get('bc_number', '')}")
            print(f"  Associated BC#: {d.get('associated_bc_number', '')}")
            print(f"  Supplier: {d.get('supplier_name', '')}")
            print(f"  Merchant: {d.get('merchant', '')}")
            print(f"  Total: {d.get('total')}")
            print(f"  Summary: {d.get('summary', '')}")
            print()
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}")


if __name__ == "__main__":
    test_pdf("budget_example_spreadsheet/BC16011.pdf")
    test_pdf("budget_example_spreadsheet/BC16739.pdf")
    print("\nDone!")
