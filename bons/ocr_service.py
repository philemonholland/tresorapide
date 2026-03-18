import logging
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image
    TESSERACT_AVAILABLE = True
except ImportError:
    TESSERACT_AVAILABLE = False


class ReceiptOcrService:
    """Extract structured data from receipt images using Tesseract."""

    @staticmethod
    def is_available() -> bool:
        """Check if Tesseract OCR is installed and usable."""
        if not TESSERACT_AVAILABLE:
            return False
        try:
            pytesseract.get_tesseract_version()
            return True
        except Exception:
            return False

    @staticmethod
    def extract_text(image_path: str) -> str:
        """Run Tesseract OCR on an image file. Returns raw text."""
        if not TESSERACT_AVAILABLE:
            raise RuntimeError("pytesseract n'est pas installé.")
        try:
            img = Image.open(image_path)
            # Use French + English for Quebec receipts
            text = pytesseract.image_to_string(img, lang="fra+eng")
            return text
        except Exception as e:
            logger.warning("Erreur OCR pour %s: %s", image_path, e)
            raise

    @staticmethod
    def _parse_amount(text: str) -> Decimal | None:
        """Parse a dollar amount string into Decimal."""
        if not text:
            return None
        cleaned = text.replace("$", "").replace(",", ".").replace(" ", "").strip()
        # Handle negative amounts
        negative = cleaned.startswith("-") or cleaned.startswith("(")
        cleaned = cleaned.strip("-() ")
        try:
            val = Decimal(cleaned)
            return -val if negative else val
        except (InvalidOperation, ValueError):
            return None

    @staticmethod
    def _parse_date(text: str) -> date | None:
        """Try various date formats common on Canadian receipts."""
        if not text:
            return None
        text = text.strip()
        formats = [
            "%Y-%m-%d", "%Y/%m/%d",
            "%d-%m-%Y", "%d/%m/%Y",
            "%m-%d-%Y", "%m/%d/%Y",
            "%d %b %Y", "%d %B %Y",
            "%Y%m%d",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    @classmethod
    def parse_receipt_text(cls, raw_text: str) -> dict:
        """
        Parse OCR text to extract structured fields.
        Returns dict with: merchant, purchase_date, subtotal, tps, tvq, total.
        """
        result = {
            "merchant": "",
            "purchase_date": None,
            "subtotal": None,
            "tps": None,
            "tvq": None,
            "total": None,
        }
        if not raw_text:
            return result

        lines = raw_text.strip().splitlines()

        # --- Merchant: usually the first non-empty, non-numeric line ---
        for line in lines[:5]:
            stripped = line.strip()
            if stripped and not re.match(r'^[\d\s\-/.$,]+$', stripped) and len(stripped) > 2:
                result["merchant"] = stripped
                break

        # --- Amount pattern ---
        amount_re = re.compile(r'\$?\s*(\d{1,6}[.,]\d{2})\b')

        # --- Date: look for common date patterns anywhere ---
        date_patterns = [
            re.compile(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})'),
            re.compile(r'(\d{1,2}[-/]\d{1,2}[-/]\d{4})'),
            re.compile(r'(\d{8})'),  # YYYYMMDD
        ]
        for pat in date_patterns:
            for line in lines:
                m = pat.search(line)
                if m:
                    parsed = cls._parse_date(m.group(1))
                    if parsed and date(2000, 1, 1) <= parsed <= date(2099, 12, 31):
                        result["purchase_date"] = parsed
                        break
            if result["purchase_date"]:
                break

        # --- Amounts: search for labelled amounts ---
        text_upper = raw_text.upper()
        lines_upper = [l.upper() for l in lines]

        def find_amount(keywords, lines_to_search):
            """Find amount on the same line as a keyword, or the line after."""
            for i, line in enumerate(lines_to_search):
                for kw in keywords:
                    if kw in line:
                        m = amount_re.search(line)
                        if m:
                            return cls._parse_amount(m.group(1))
                        # Check next line
                        if i + 1 < len(lines_to_search):
                            m = amount_re.search(lines_to_search[i + 1])
                            if m:
                                return cls._parse_amount(m.group(1))
            return None

        result["subtotal"] = find_amount(
            ["SOUS-TOTAL", "SUBTOTAL", "SOUS TOTAL", "S/TOTAL"],
            lines_upper
        )
        result["tps"] = find_amount(
            ["TPS", "GST", "T.P.S"],
            lines_upper
        )
        result["tvq"] = find_amount(
            ["TVQ", "QST", "T.V.Q"],
            lines_upper
        )
        # Total: look for TOTAL but not SOUS-TOTAL or SUBTOTAL
        for i, line in enumerate(lines_upper):
            if "TOTAL" in line and "SOUS" not in line and "SUB" not in line:
                m = amount_re.search(line)
                if m:
                    result["total"] = cls._parse_amount(m.group(1))
                    break
                if i + 1 < len(lines_upper):
                    m = amount_re.search(lines_upper[i + 1])
                    if m:
                        result["total"] = cls._parse_amount(m.group(1))
                        break

        return result

    @classmethod
    def process_receipt(cls, receipt_file_obj) -> dict:
        """
        Full pipeline: OCR + parse for a ReceiptFile instance.
        Updates the ReceiptFile and creates/updates related records.
        Returns extracted fields dict.
        """
        from .models import ReceiptOcrResult, ReceiptExtractedFields, OcrStatus

        result = {
            "merchant": "",
            "purchase_date": None,
            "subtotal": None,
            "tps": None,
            "tvq": None,
            "total": None,
        }

        if not cls.is_available():
            logger.warning("Tesseract OCR n'est pas disponible.")
            receipt_file_obj.ocr_status = OcrStatus.FAILED
            receipt_file_obj.save(update_fields=["ocr_status"])
            return result

        try:
            receipt_file_obj.ocr_status = OcrStatus.PENDING
            receipt_file_obj.save(update_fields=["ocr_status"])

            raw_text = cls.extract_text(receipt_file_obj.file.path)
            receipt_file_obj.ocr_raw_text = raw_text
            receipt_file_obj.ocr_status = OcrStatus.EXTRACTED
            receipt_file_obj.save(update_fields=["ocr_raw_text", "ocr_status"])

            # Save OCR result
            ReceiptOcrResult.objects.create(
                receipt_file=receipt_file_obj,
                engine_name="tesseract",
                raw_text=raw_text,
            )

            # Parse text
            result = cls.parse_receipt_text(raw_text)

            # Save/update extracted fields
            ReceiptExtractedFields.objects.update_or_create(
                receipt_file=receipt_file_obj,
                defaults={
                    "merchant_candidate": result.get("merchant", ""),
                    "purchase_date_candidate": result.get("purchase_date"),
                    "subtotal_candidate": result.get("subtotal"),
                    "tps_candidate": result.get("tps"),
                    "tvq_candidate": result.get("tvq"),
                    "total_candidate": result.get("total"),
                },
            )

        except Exception as e:
            logger.exception("OCR échoué pour le reçu %s: %s", receipt_file_obj.pk, e)
            receipt_file_obj.ocr_status = OcrStatus.FAILED
            receipt_file_obj.save(update_fields=["ocr_status"])

        return result
