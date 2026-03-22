"""Receipt analysis service using OpenAI Vision API.

Consolidates multiple receipt images into a single composite image
to minimize API costs (1 call instead of N).
"""
import base64
import io
import json
import logging
import tempfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from PIL import Image, ImageDraw, ImageFont
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# ── Prompt for batch analysis (multiple receipts in one composite image) ─────

MAX_PAGES_PER_BATCH = 4  # Max pages per API call to preserve accuracy

RECEIPT_BATCH_PROMPT = """\
Tu es un assistant spécialisé dans l'extraction de données de documents \
financiers pour une coopérative d'habitation au Québec.

L'image contient un ou plusieurs documents (reçus, factures, bons de commande \
papier), chacun précédé d'un bandeau avec le nom du fichier original et le \
numéro de page (ex: «BC16011.pdf - Page 1» ou «TestRecu1.png»).

IMPORTANT: Un même document peut s'étendre sur PLUSIEURS sous-images \
consécutives (ex: facture de plusieurs pages, reçu photographié en 2 parties). \
Dans ce cas, combine les informations en UNE SEULE entrée JSON et utilise le \
nom de fichier de la PREMIÈRE sous-image comme "filename".

Types de documents — classifie chaque document:
1. "paper_bc" — Bon de commande PAPIER officiel de la coopérative. Se reconnaît \
par: le logo ou nom de la coopérative en haut, "Notre numéro de commande" avec \
un numéro, des lignes description/quantité/prix, et un champ "Fournisseur ou \
personne à rembourser".
2. "invoice" — Facture ou soumission d'un fournisseur. Souvent associée à un \
bon de commande papier dans le même lot.
3. "receipt" — Reçu de caisse (magasin, quincaillerie, épicerie). Achat fait \
par un MEMBRE de la coopérative qui se fait rembourser. Contient souvent un \
nom manuscrit et un numéro d'appartement.

Retourne UNIQUEMENT un tableau JSON. Chaque élément = un document distinct:
[
  {
    "filename": "BC16011.pdf - Page 1",
    "document_type": "paper_bc",
    "bc_number": "16011",
    "associated_bc_number": "",
    "supplier_name": "Nom du fournisseur",
    "supplier_address": "Adresse du fournisseur",
    "expense_member_name": "Nom de la personne ayant effectué la dépense",
    "expense_apartment": "202",
    "member_name": "",
    "apartment_number": "",
    "merchant": "",
    "purchase_date": "YYYY-MM-DD",
    "subtotal": 0.00,
    "tps": 0.00,
    "tvq": 0.00,
    "total": 0.00,
    "summary": "Courte description des achats"
  }
]

Règles:
- filename: reproduis EXACTEMENT le nom affiché dans le bandeau. Si un document \
s'étend sur plusieurs sous-images, utilise le nom de la première.
- document_type: "paper_bc", "invoice", ou "receipt".
- bc_number: pour "paper_bc" seulement — le numéro sous "Notre numéro de \
commande". Laisser "" pour les autres types.
- associated_bc_number: si c'est une "invoice" liée à un "paper_bc" du même \
lot, mettre le numéro du BC. Utilise le contexte (montants, fournisseur, \
description) pour associer. Laisser "" si pas de BC associé.
- supplier_name: pour "paper_bc" et "invoice" — le nom du fournisseur ou la \
personne à rembourser tel qu'inscrit sur le document.
- supplier_address: adresse du fournisseur si visible.
- expense_member_name: pour "paper_bc" — le NOM de la personne qui a signé \
le bon de commande, inscrit au-dessus de "NOM EN LETTRES MOULÉES" dans la \
section signature. C'est la personne qui a effectué la dépense. Laisser "" si \
non visible ou pour les autres types.
- expense_apartment: pour "paper_bc" — le numéro d'appartement de la personne \
ayant effectué la dépense, s'il est visible sur le bon. Laisser "" si absent.
- member_name: pour "receipt" seulement — le nom écrit à la main par le membre. \
Si complètement illisible, utilise "ILLISIBLE".
- apartment_number: pour "receipt" seulement — le numéro d'appartement manuscrit \
(souvent 3 chiffres: 101, 202, 307).
- merchant: pour "receipt" — le nom du commerce sur le reçu.
- purchase_date: date au format ISO YYYY-MM-DD. Si absente, null.
- subtotal: sous-total AVANT taxes. Si absent, null (NE PAS calculer).
- tps: TPS (taxe fédérale, ~5%). Si absente, null.
- tvq: TVQ (taxe provinciale, ~9.975%). Si absente, null.
- total: montant TOTAL TTC. Si absent, null. ATTENTION: extraire le montant \
EXACT tel qu'affiché sur le document, ne pas recalculer.
- summary: courte description du contenu du document.
- Les montants en nombre décimal (pas de $).
- Retourne UNIQUEMENT le JSON, sans texte additionnel.
"""


def _get_openai_model() -> str:
    """Return the configured model name."""
    return getattr(settings, "OPENAI_MODEL", "") or "gpt-5.4"


class ReceiptOcrService:
    """Analyze receipt images using OpenAI Vision API."""

    @staticmethod
    def is_available() -> bool:
        if not OPENAI_AVAILABLE:
            return False
        return bool(getattr(settings, "OPENAI_API_KEY", ""))

    # ── Composite image builder ──────────────────────────────────────────

    @staticmethod
    def _build_composite_image(file_map: dict[str, str]) -> tuple[bytes, list[str]]:
        """
        Stitch multiple receipt images into one vertical composite.
        Each sub-image gets a filename label banner above it.
        Supports images (JPEG/PNG) and PDFs (converted to images).

        Args:
            file_map: {label: file_path} — label is used in the banner

        Returns:
            (PNG bytes of the composite image, list of page labels in order)
        """
        BANNER_HEIGHT = 40
        TARGET_WIDTH = 1200
        PADDING = 10

        panels = []
        page_labels = []

        for label, file_path in file_map.items():
            lower_path = file_path.lower()
            page_images = []

            if lower_path.endswith(".pdf") and PDF2IMAGE_AVAILABLE:
                try:
                    pdf_pages = convert_from_path(file_path, dpi=200)
                    for i, page_img in enumerate(pdf_pages):
                        page_label = f"{label} - Page {i + 1}" if len(pdf_pages) > 1 else label
                        page_images.append((page_label, page_img))
                except Exception:
                    logger.warning("Cannot convert PDF %s, skipping", file_path)
                    continue
            else:
                try:
                    img = Image.open(file_path)
                    page_images.append((label, img))
                except Exception:
                    logger.warning("Cannot open image %s, skipping", file_path)
                    continue

            for page_label, img in page_images:
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
                draw.text((PADDING, 8), f"📄 {page_label}", fill=(255, 255, 255), font=font)

                panels.append(banner)
                panels.append(img)
                page_labels.append(page_label)

        if not panels:
            raise ValueError("Aucune image valide à analyser.")

        total_height = sum(p.height for p in panels) + PADDING * (len(panels) - 1)
        composite = Image.new("RGB", (TARGET_WIDTH, total_height), (255, 255, 255))
        y = 0
        for panel in panels:
            composite.paste(panel, (0, y))
            y += panel.height + PADDING

        buf = io.BytesIO()
        composite.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), page_labels

    # ── API call ─────────────────────────────────────────────────────────

    @classmethod
    def _count_pages(cls, file_map: dict[str, str]) -> dict[str, int]:
        """Count how many composite pages each file will produce."""
        counts = {}
        for label, file_path in file_map.items():
            lower_path = file_path.lower()
            if lower_path.endswith(".pdf") and PDF2IMAGE_AVAILABLE:
                try:
                    pdf_pages = convert_from_path(file_path, dpi=72, first_page=1, last_page=1)
                    # Quick count via pdfinfo
                    from pdf2image.pdf2image import pdfinfo_from_path
                    info = pdfinfo_from_path(file_path)
                    counts[label] = info.get("Pages", 1)
                except Exception:
                    counts[label] = 1
            else:
                counts[label] = 1
        return counts

    @classmethod
    def _split_file_map(cls, file_map: dict[str, str]) -> list[dict[str, str]]:
        """Split file_map into sub-batches of at most MAX_PAGES_PER_BATCH pages."""
        page_counts = cls._count_pages(file_map)
        batches = []
        current_batch = {}
        current_pages = 0

        for label, path in file_map.items():
            file_pages = page_counts.get(label, 1)
            # If a single file exceeds the limit, give it its own batch
            if current_pages + file_pages > MAX_PAGES_PER_BATCH and current_batch:
                batches.append(current_batch)
                current_batch = {}
                current_pages = 0
            current_batch[label] = path
            current_pages += file_pages

        if current_batch:
            batches.append(current_batch)

        return batches

    @classmethod
    def _analyze_single_batch(cls, file_map: dict[str, str]) -> list[dict]:
        """Send one composite image to OpenAI and return parsed results."""
        composite_bytes, page_labels = cls._build_composite_image(file_map)
        image_b64 = base64.b64encode(composite_bytes).decode("utf-8")
        num_pages = len(page_labels)

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        model = _get_openai_model()

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": RECEIPT_BATCH_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Cette image contient {num_pages} page(s) provenant "
                                f"de {len(file_map)} fichier(s). "
                                "Analyse chaque page, classifie les documents et "
                                "extrais les informations en JSON."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{image_b64}",
                            },
                        },
                    ],
                },
            ],
            max_completion_tokens=16000,
            temperature=0,
        )

        raw = response.choices[0].message.content.strip()
        logger.info("GPT batch analysis (model=%s, %d pages): %s",
                     model, num_pages, raw)
        return cls._parse_batch_response(raw, list(file_map.keys()))

    @classmethod
    def analyze_batch(cls, file_map: dict[str, str]) -> list[dict]:
        """
        Analyze receipts/PDFs via OpenAI Vision API.
        Automatically splits into sub-batches of MAX_PAGES_PER_BATCH pages
        to preserve extraction accuracy.
        """
        if not cls.is_available():
            raise RuntimeError("OpenAI API non configurée.")

        batches = cls._split_file_map(file_map)
        all_results = []

        for sub_map in batches:
            results = cls._analyze_single_batch(sub_map)
            all_results.extend(results)

        # Ensure every input filename has at least one result
        seen_sources = {r.get("source_filename", r["filename"]) for r in all_results}
        for fn in file_map:
            if fn not in seen_sources:
                empty = cls._empty_result(fn)
                empty["source_filename"] = fn
                all_results.append(empty)

        return all_results

    # ── Parsing helpers ──────────────────────────────────────────────────

    @staticmethod
    def _safe_decimal(value) -> Decimal | None:
        if value is None:
            return None
        try:
            return Decimal(str(value)).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _safe_date(value) -> date | None:
        if not value:
            return None
        try:
            from datetime import datetime
            return datetime.strptime(str(value), "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @classmethod
    def _parse_one(cls, data: dict) -> dict:
        """Parse a single document entry from the GPT JSON response."""
        return {
            "filename": str(data.get("filename") or ""),
            "document_type": str(data.get("document_type") or "receipt").strip(),
            "bc_number": str(data.get("bc_number") or "").strip(),
            "associated_bc_number": str(data.get("associated_bc_number") or "").strip(),
            "supplier_name": str(data.get("supplier_name") or "").strip(),
            "supplier_address": str(data.get("supplier_address") or "").strip(),
            "expense_member_name": str(data.get("expense_member_name") or "").strip(),
            "expense_apartment": str(data.get("expense_apartment") or "").strip(),
            "member_name": str(data.get("member_name") or "").strip(),
            "apartment_number": str(data.get("apartment_number") or "").strip(),
            "merchant": str(data.get("merchant") or ""),
            "purchase_date": cls._safe_date(data.get("purchase_date")),
            "subtotal": cls._safe_decimal(data.get("subtotal")),
            "tps": cls._safe_decimal(data.get("tps")),
            "tvq": cls._safe_decimal(data.get("tvq")),
            "total": cls._safe_decimal(data.get("total")),
            "summary": str(data.get("summary") or "").strip(),
        }

    @classmethod
    def _parse_batch_response(cls, raw_text: str, filenames: list[str]) -> list[dict]:
        """Parse a JSON array response from batch analysis.

        GPT may return more results than input files (e.g., a PDF with a paper
        BC on page 1 and an invoice on page 2 produces two entries).  Each
        result is tagged with ``source_filename`` — the original upload name it
        belongs to — in addition to the GPT-provided ``filename`` (which may be
        a page label like ``"BC16011.pdf - Page 2"``).
        """
        text = raw_text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning("Impossible de parser la réponse GPT batch: %s", raw_text)
            return [cls._empty_result(fn) for fn in filenames]

        # GPT might return a single object instead of array for 1 receipt
        if isinstance(data, dict):
            data = [data]

        if not isinstance(data, list):
            return [cls._empty_result(fn) for fn in filenames]

        # Parse each entry
        results = [cls._parse_one(item) for item in data]

        # Tag each result with its source_filename (the original upload name).
        # GPT filenames may be page labels like "X.pdf - Page 2"; map them back.
        for r in results:
            gpt_fn = r["filename"]
            # Exact match
            if gpt_fn in filenames:
                r["source_filename"] = gpt_fn
                continue
            # Fuzzy: GPT filename contains or is contained by an original name
            matched = False
            for fn in filenames:
                if fn and gpt_fn and (fn in gpt_fn or gpt_fn in fn):
                    r["source_filename"] = fn
                    matched = True
                    break
            if not matched:
                # Fallback: assign to first filename (single-file uploads)
                r["source_filename"] = filenames[0] if filenames else ""

        # Ensure every input filename has at least one result
        seen_sources = {r["source_filename"] for r in results}
        for fn in filenames:
            if fn not in seen_sources:
                empty = cls._empty_result(fn)
                empty["source_filename"] = fn
                results.append(empty)

        return results

    @staticmethod
    def _empty_result(filename: str = "") -> dict:
        return {
            "filename": filename,
            "document_type": "receipt",
            "bc_number": "",
            "associated_bc_number": "",
            "supplier_name": "",
            "supplier_address": "",
            "expense_member_name": "",
            "expense_apartment": "",
            "member_name": "",
            "apartment_number": "",
            "merchant": "",
            "purchase_date": None,
            "subtotal": None,
            "tps": None,
            "tvq": None,
            "total": None,
            "summary": "",
        }

    # ── Batch processing pipeline ────────────────────────────────────────

    @classmethod
    def process_receipts_batch(cls, receipt_file_objs: list) -> tuple[list[dict], str]:
        """
        Process multiple ReceiptFile objects in a single API call.
        Returns (list_of_results, error_message).
        """
        from .models import ReceiptOcrResult, ReceiptExtractedFields, OcrStatus

        empties = [cls._empty_result(r.original_filename) for r in receipt_file_objs]

        if not cls.is_available():
            msg = "Clé API OpenAI non configurée. Ajoutez OPENAI_API_KEY dans .env."
            logger.warning(msg)
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])
            return empties, msg

        # Build file map: filename → file path (images AND PDFs)
        file_map = {}
        for r in receipt_file_objs:
            r.ocr_status = OcrStatus.PENDING
            r.save(update_fields=["ocr_status"])
            ct = (r.content_type or "").lower()
            if ct.startswith("image/") or ct == "application/pdf":
                file_map[r.original_filename] = r.file.path

        if not file_map:
            msg = "Aucun fichier analysable (images et PDF supportés)."
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])
            return empties, msg

        try:
            results = cls.analyze_batch(file_map)

            # Group results by source_filename (one file may produce multiple docs)
            from collections import defaultdict
            results_by_source = defaultdict(list)
            for r in results:
                results_by_source[r.get("source_filename", r["filename"])].append(r)

            model = _get_openai_model()

            for receipt_obj in receipt_file_objs:
                fn = receipt_obj.original_filename
                file_results = results_by_source.get(fn, [cls._empty_result(fn)])

                # Store ALL results from this file as raw JSON
                receipt_obj.ocr_raw_text = json.dumps(file_results, default=str)
                receipt_obj.ocr_status = OcrStatus.EXTRACTED
                receipt_obj.save(update_fields=["ocr_raw_text", "ocr_status"])

                ReceiptOcrResult.objects.create(
                    receipt_file=receipt_obj,
                    engine_name=f"openai-{model}",
                    raw_text=json.dumps(file_results, default=str),
                )

                # Primary extracted fields: use the first result (paper_bc if
                # present, otherwise the first document)
                primary = file_results[0]
                for r in file_results:
                    if r.get("document_type") == "paper_bc":
                        primary = r
                        break

                ReceiptExtractedFields.objects.update_or_create(
                    receipt_file=receipt_obj,
                    defaults={
                        "document_type_candidate": primary.get("document_type", "receipt"),
                        "bc_number_candidate": primary.get("bc_number", ""),
                        "associated_bc_number_candidate": primary.get("associated_bc_number", ""),
                        "supplier_name_candidate": primary.get("supplier_name", ""),
                        "supplier_address_candidate": primary.get("supplier_address", ""),
                        "expense_member_name_candidate": primary.get("expense_member_name", ""),
                        "expense_apartment_candidate": primary.get("expense_apartment", ""),
                        "member_name_candidate": primary.get("member_name", ""),
                        "apartment_number_candidate": primary.get("apartment_number", ""),
                        "merchant_candidate": primary.get("merchant", ""),
                        "purchase_date_candidate": primary.get("purchase_date"),
                        "subtotal_candidate": primary.get("subtotal"),
                        "tps_candidate": primary.get("tps"),
                        "tvq_candidate": primary.get("tvq"),
                        "total_candidate": primary.get("total"),
                        "summary_candidate": primary.get("summary", ""),
                    },
                )

            return results, ""

        except Exception as e:
            error_str = str(e)
            if "insufficient_quota" in error_str or "429" in error_str:
                msg = "Quota OpenAI épuisé. Vérifiez votre forfait sur platform.openai.com."
            elif "401" in error_str or "invalid_api_key" in error_str:
                msg = "Clé API OpenAI invalide. Vérifiez OPENAI_API_KEY dans .env."
            elif "model_not_found" in error_str or "404" in error_str:
                msg = f"Modèle OpenAI introuvable. Vérifiez OPENAI_MODEL (actuel: {_get_openai_model()})."
            else:
                msg = f"Erreur API OpenAI: {error_str[:200]}"

            logger.exception("Analyse GPT batch échouée: %s", e)
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])

            return empties, msg
