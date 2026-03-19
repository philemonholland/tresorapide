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

# ── Prompt for batch analysis (multiple receipts in one composite image) ─────

RECEIPT_BATCH_PROMPT = """\
Tu es un assistant spécialisé dans l'extraction de données de reçus et factures \
pour une coopérative d'habitation au Québec.

L'image contient un ou plusieurs reçus/factures, chacun précédé d'un bandeau \
avec le nom du fichier original (ex: «TestRecu1.png»).

IMPORTANT: Un même reçu peut s'étendre sur PLUSIEURS sous-images consécutives \
(par exemple si le reçu a été photographié en deux parties, ou si c'est une \
facture de plusieurs pages). Dans ce cas, les sous-images du même reçu auront \
des noms de fichier similaires ou consécutifs. Combine les informations de \
toutes les sous-images qui font partie du MÊME reçu en UNE SEULE entrée JSON. \
Utilise le nom de fichier de la PREMIÈRE sous-image comme "filename".

Pour CHAQUE reçu dans l'image, un membre de la coopérative a fait l'achat, \
signé le document et écrit son numéro d'appartement à la main.

Retourne UNIQUEMENT un tableau JSON. Chaque élément correspond à un reçu:
[
  {
    "filename": "TestRecu1.png",
    "member_name": "Nom manuscrit du membre",
    "apartment_number": "207",
    "merchant": "Nom du marchand",
    "purchase_date": "YYYY-MM-DD",
    "subtotal": 0.00,
    "tps": 0.00,
    "tvq": 0.00,
    "total": 0.00
  }
]

Règles:
- filename: reproduis EXACTEMENT le nom affiché dans le bandeau au-dessus du reçu. \
Si un reçu s'étend sur plusieurs sous-images, utilise le nom de la première.
- member_name: le nom écrit à la main par le membre. Si complètement illisible, \
utilise "ILLISIBLE". Essaie quand même de lire, même partiellement.
- apartment_number: le numéro d'appartement écrit à la main (souvent 3 chiffres: \
101, 202, 307). Cherche dans les annotations manuscrites ou la signature.
- merchant: le nom du commerce tel qu'imprimé sur le reçu.
- purchase_date: date d'achat au format ISO YYYY-MM-DD. Si absente, null.
- subtotal: sous-total AVANT taxes. Si absent, null (NE PAS calculer).
- tps: TPS (taxe fédérale, ~5%). Si absente, null.
- tvq: TVQ (taxe provinciale, ~9.975%). Si absente, null.
- total: montant TOTAL payé. Si absent, null.
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
    def _build_composite_image(file_map: dict[str, str]) -> bytes:
        """
        Stitch multiple receipt images into one vertical composite.
        Each sub-image gets a filename label banner above it.

        Args:
            file_map: {original_filename: file_path}

        Returns:
            PNG bytes of the composite image.
        """
        BANNER_HEIGHT = 40
        TARGET_WIDTH = 1200
        PADDING = 10

        panels = []
        for filename, file_path in file_map.items():
            try:
                img = Image.open(file_path)
                if img.mode != "RGB":
                    img = img.convert("RGB")
            except Exception:
                logger.warning("Cannot open image %s, skipping", file_path)
                continue

            # Resize to target width
            ratio = TARGET_WIDTH / img.width
            new_h = int(img.height * ratio)
            img = img.resize((TARGET_WIDTH, new_h), Image.LANCZOS)

            # Create banner with filename
            banner = Image.new("RGB", (TARGET_WIDTH, BANNER_HEIGHT), (40, 40, 40))
            draw = ImageDraw.Draw(banner)
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 22)
            except (OSError, IOError):
                font = ImageFont.load_default()
            draw.text((PADDING, 8), f"📄 {filename}", fill=(255, 255, 255), font=font)

            panels.append(banner)
            panels.append(img)

        if not panels:
            raise ValueError("Aucune image valide à analyser.")

        # Stack vertically
        total_height = sum(p.height for p in panels) + PADDING * (len(panels) - 1)
        composite = Image.new("RGB", (TARGET_WIDTH, total_height), (255, 255, 255))
        y = 0
        for panel in panels:
            composite.paste(panel, (0, y))
            y += panel.height + PADDING

        buf = io.BytesIO()
        composite.save(buf, format="PNG", optimize=True)
        return buf.getvalue()

    # ── API call ─────────────────────────────────────────────────────────

    @classmethod
    def analyze_batch(cls, file_map: dict[str, str]) -> list[dict]:
        """
        Send a composite image of all receipts to OpenAI in a single API call.
        Returns a list of result dicts, one per receipt, keyed by 'filename'.
        """
        if not cls.is_available():
            raise RuntimeError("OpenAI API non configurée.")

        composite_bytes = cls._build_composite_image(file_map)
        image_b64 = base64.b64encode(composite_bytes).decode("utf-8")

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
                                f"Cette image contient {len(file_map)} reçu(s). "
                                "Extrais les informations de chacun en JSON."
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
            max_completion_tokens=300 * max(len(file_map), 1),
            temperature=0,
        )

        raw = response.choices[0].message.content.strip()
        logger.info("GPT batch analysis (model=%s, %d receipts): %s",
                     model, len(file_map), raw)
        return cls._parse_batch_response(raw, list(file_map.keys()))

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
        """Parse a single receipt entry from the GPT JSON response."""
        return {
            "filename": str(data.get("filename") or ""),
            "member_name": str(data.get("member_name") or "").strip(),
            "apartment_number": str(data.get("apartment_number") or "").strip(),
            "merchant": str(data.get("merchant") or ""),
            "purchase_date": cls._safe_date(data.get("purchase_date")),
            "subtotal": cls._safe_decimal(data.get("subtotal")),
            "tps": cls._safe_decimal(data.get("tps")),
            "tvq": cls._safe_decimal(data.get("tvq")),
            "total": cls._safe_decimal(data.get("total")),
        }

    @classmethod
    def _parse_batch_response(cls, raw_text: str, filenames: list[str]) -> list[dict]:
        """Parse a JSON array response from batch analysis."""
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

        # Ensure we have a result for every filename (match by filename)
        result_map = {}
        for r in results:
            result_map[r["filename"]] = r

        ordered = []
        for fn in filenames:
            if fn in result_map:
                ordered.append(result_map[fn])
            else:
                # Try fuzzy match (GPT might slightly alter filename)
                matched = False
                for key, val in result_map.items():
                    if key and fn and (key in fn or fn in key):
                        ordered.append(val)
                        matched = True
                        break
                if not matched:
                    ordered.append(cls._empty_result(fn))

        return ordered

    @staticmethod
    def _empty_result(filename: str = "") -> dict:
        return {
            "filename": filename,
            "member_name": "",
            "apartment_number": "",
            "merchant": "",
            "purchase_date": None,
            "subtotal": None,
            "tps": None,
            "tvq": None,
            "total": None,
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

        # Build file map: filename → file path (images only)
        file_map = {}
        for r in receipt_file_objs:
            r.ocr_status = OcrStatus.PENDING
            r.save(update_fields=["ocr_status"])
            if r.content_type and r.content_type.startswith("image/"):
                file_map[r.original_filename] = r.file.path
            # PDFs skipped from composite for now

        if not file_map:
            msg = "Aucune image à analyser (seuls les fichiers image sont supportés)."
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])
            return empties, msg

        try:
            results = cls.analyze_batch(file_map)

            # Map results back to receipt objects by filename
            result_by_name = {r["filename"]: r for r in results}
            model = _get_openai_model()

            for receipt_obj in receipt_file_objs:
                fn = receipt_obj.original_filename
                result = result_by_name.get(fn, cls._empty_result(fn))

                receipt_obj.ocr_raw_text = json.dumps(result, default=str)
                receipt_obj.ocr_status = OcrStatus.EXTRACTED
                receipt_obj.save(update_fields=["ocr_raw_text", "ocr_status"])

                ReceiptOcrResult.objects.create(
                    receipt_file=receipt_obj,
                    engine_name=f"openai-{model}",
                    raw_text=json.dumps(result, default=str),
                )

                ReceiptExtractedFields.objects.update_or_create(
                    receipt_file=receipt_obj,
                    defaults={
                        "member_name_candidate": result.get("member_name", ""),
                        "apartment_number_candidate": result.get("apartment_number", ""),
                        "merchant_candidate": result.get("merchant", ""),
                        "purchase_date_candidate": result.get("purchase_date"),
                        "subtotal_candidate": result.get("subtotal"),
                        "tps_candidate": result.get("tps"),
                        "tvq_candidate": result.get("tvq"),
                        "total_candidate": result.get("total"),
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
