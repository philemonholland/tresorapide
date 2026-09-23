"""Receipt analysis service using the OpenAI Vision API.

Preserves each source page as a separate image while grouping a bounded number
of pages into each request.  This avoids the destructive downscaling caused by
very tall multi-page composite images.
"""
import base64
import io
import json
import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from django.conf import settings

from .ai_confidence import (
    DUPLICATE_FIELD_CONFIDENCE_KEYS,
    OCR_FIELD_CONFIDENCE_KEYS,
    build_complete_ai_confidence_scores,
)
from .amounts import build_amount_consistency_warning, cap_amount_confidence_scores

logger = logging.getLogger(__name__)

try:
    from openai import OpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    from PIL import Image, ImageOps
    PILLOW_AVAILABLE = True
except ImportError:
    PILLOW_AVAILABLE = False

try:
    from pdf2image import convert_from_path
    PDF2IMAGE_AVAILABLE = True
except ImportError:
    PDF2IMAGE_AVAILABLE = False

# ── Prompt for batch analysis ────────────────────────────────────────────────

MAX_PAGES_PER_BATCH = 4  # Max pages per API call to preserve accuracy


@dataclass(frozen=True)
class _PdfPageRange:
    """A bounded range of one PDF to render for a single OCR request."""

    path: str
    first_page: int
    last_page: int


_MEANINGFUL_EXTRACTION_FIELDS = (
    "bc_number",
    "associated_bc_number",
    "supplier_name",
    "supplier_address",
    "expense_member_name",
    "expense_apartment",
    "validator_member_name",
    "validator_apartment",
    "member_name",
    "apartment_number",
    "merchant",
    "purchase_date",
    "subtotal",
    "tps",
    "tvq",
    "untaxed_extra_amount",
    "total",
)

RECEIPT_BATCH_PROMPT = """\
Tu es un assistant spécialisé dans l'extraction de données de documents \
financiers pour une coopérative d'habitation au Québec.

Le message contient une ou plusieurs images de documents (reçus, factures, \
bons de commande papier). Chaque image est immédiatement précédée d'un libellé \
texte avec le nom du fichier original et le numéro de page (ex: \
«BC16011.pdf - Page 1» ou «TestRecu1.png»).

PHOTOS MOBILES: L'image peut provenir d'un téléphone et montrer un document \
(reçu, facture, bon de commande) posé sur une surface (table, comptoir, \
plancher) avec de l'arrière-plan visible. Concentre-toi UNIQUEMENT sur le \
document lui-même — ignore complètement l'arrière-plan, les surfaces, les \
ombres et tout objet non pertinent. Si le document n'occupe qu'une partie de \
l'image, extrais quand même toutes les données possibles de ce document.

IMPORTANT: Un même document peut s'étendre sur PLUSIEURS sous-images \
consécutives (ex: facture de plusieurs pages, reçu photographié en 2 parties). \
Dans ce cas, combine les informations en UNE SEULE entrée JSON et utilise le \
nom de fichier de la PREMIÈRE sous-image comme "filename".

COUVERTURE OBLIGATOIRE DES PAGES:
- Examine chaque image fournie et n'omets aucune page contenant un document.
- Des pages du même fournisseur avec des dates, numéros de facture ou montants \
différents sont des documents DISTINCTS: retourne une entrée JSON par document.
- Ne combine des pages que si elles sont clairement la continuation du même \
document (même numéro de facture/transaction ou pagination explicite).
- Une seule image peut montrer plusieurs documents physiques superposés; dans \
ce cas, retourne une entrée JSON distincte pour chacun avec le même filename.

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
    "reimburse_to": "member",
    "expense_member_name": "Nom de la personne ayant effectué la dépense",
    "expense_apartment": "202",
    "validator_member_name": "Nom du 2e signataire",
    "validator_apartment": "203",
    "signer_roles_ambiguous": false,
    "member_name": "",
    "apartment_number": "",
    "merchant": "",
    "purchase_date": "YYYY-MM-DD",
    "subtotal": 0.00,
    "tps": 0.00,
    "tvq": 0.00,
    "untaxed_extra_amount": 0.00,
    "total": 0.00,
    "summary": "Courte description des achats",
    "field_confidence_scores": {
      "document_type": 8,
      "bc_number": 9,
      "associated_bc_number": "NA",
      "supplier_name": 7,
      "supplier_address": 6,
      "reimburse_to": 7,
      "expense_member_name": 5,
      "expense_apartment": 6,
      "validator_member_name": 4,
      "validator_apartment": "NA",
      "signer_roles_ambiguous": 5,
      "member_name": 7,
      "apartment_number": 8,
      "merchant": 9,
      "purchase_date": 8,
      "subtotal": 9,
      "tps": 8,
      "tvq": 8,
      "untaxed_extra_amount": "NA",
      "total": 9,
      "summary": 7
    }
  }
]

Règles:
- filename: reproduis EXACTEMENT le libellé texte « FICHIER/PAGE » fourni \
immédiatement avant l'image. Si un document s'étend sur plusieurs sous-images, \
utilise le libellé de la première image. N'invente jamais un autre nom.
- document_type: "paper_bc", "invoice", ou "receipt".
- bc_number: pour "paper_bc" seulement — le numéro sous "Notre numéro de \
commande". Laisser "" pour les autres types.
- associated_bc_number: si c'est une "invoice" liée à un "paper_bc" du même \
lot, mettre le numéro du BC. Utilise le contexte (montants, fournisseur, \
description) pour associer. Laisser "" si pas de BC associé.
- supplier_name: pour "paper_bc" et "invoice" — le nom du fournisseur ou la \
personne à rembourser tel qu'inscrit sur le document.
- supplier_address: adresse du fournisseur si visible.
- reimburse_to: pour "paper_bc" — détermine qui doit être remboursé. Regarde \
le champ "Nom du fournisseur ou de la personne à rembourser" sur le bon de \
commande papier. Si ce nom correspond à la personne qui a effectué la dépense \
(expense_member_name) ou à un membre du répertoire de la maison, retourne \
"member". Si c'est une entreprise ou un fournisseur externe, retourne \
"supplier". Pour les "invoice" et "receipt", laisser "".
- expense_member_name: pour "paper_bc" — le NOM de la personne qui a signé \
le bon de commande, inscrit au-dessus de "NOM EN LETTRES MOULÉES" dans la \
section signature. C'est la personne qui a effectué la dépense. Laisser "" si \
non visible ou pour les autres types.
- expense_apartment: pour "paper_bc" — le numéro d'appartement de la personne \
ayant effectué la dépense, s'il est visible sur le bon. Laisser "" si absent.
- validator_member_name: pour "paper_bc" — s'il y a une DEUXIÈME signature qui \
  valide l'achat, le nom de cette personne. Cette personne peut être EXTERNE à \
  la coop. Ne force pas ce nom vers un membre du répertoire si ce n'est pas un \
  match clair. Sinon "".
- validator_apartment: pour "paper_bc" — l'appartement du 2e signataire si \
  visible sur le document lui-même. Si le 2e signataire semble externe ou si \
  l'appartement n'est pas clairement écrit près de sa signature, laisse "". \
  Ne déduis jamais validator_apartment uniquement à partir du répertoire des \
  membres. Sinon "".
- signer_roles_ambiguous: pour "paper_bc" — true s'il y a deux signatures mais \
qu'il est ambigu lequel est l'acheteur vs lequel valide l'achat; false sinon.
- member_name: pour "receipt" seulement — le nom écrit à la main par le membre. \
Si complètement illisible, utilise "ILLISIBLE".
- apartment_number: pour "receipt" seulement — le numéro d'appartement manuscrit \
(souvent 3 chiffres: 101, 202, 307).
- merchant: pour "receipt" — le nom du commerce sur le reçu.
- purchase_date: date au format ISO YYYY-MM-DD. Cherche la date imprimée sur \
le reçu ET les annotations manuscrites dans toutes les marges, notamment \
au-dessus du reçu. Interprète une date québécoise manuscrite JJ/MM/AA comme \
jour/mois/année (ex: 27/12/23 = 2023-12-27). Si absente, null.
- subtotal: sous-total AVANT taxes. Si absent, null (NE PAS calculer).
- tps: TPS (taxe fédérale, ~5%). Si absente, null.
- tvq: TVQ (taxe provinciale, ~9.975%). Si absente, null.
- untaxed_extra_amount: pourboire, livraison ou autres frais NON TAXABLES
  inclus au total. Si absent, null.
- total: montant TOTAL TTC. Si absent, null. ATTENTION: extraire le montant \
EXACT tel qu'affiché sur le document, ne pas recalculer.
- IMPORTANT pour les factures ("invoice"): extraire ABSOLUMENT les montants \
(subtotal, tps, tvq, untaxed_extra_amount, total) même s'ils apparaissent dans un tableau, un bon \
de commande associé, ou une section « montant ». C'est CRITIQUE pour la \
vérification croisée avec le bon de commande papier. Si le prix unitaire et \
la quantité sont visibles mais pas de sous-total explicite, calculer \
prix × quantité comme subtotal.
- Vérification interne OBLIGATOIRE des montants: quand subtotal, tps, tvq, \
  untaxed_extra_amount et total sont visibles, compare toujours \
  subtotal + tps + tvq + untaxed_extra_amount au total. Si les montants ne se \
  réconcilient pas exactement, conserve les valeurs telles que lues sur le \
  document, mais baisse fortement la confiance des champs financiers concernés.
- Confiance CONSERVATRICE: n'utilise 9 que si la valeur est parfaitement \
  lisible, explicite, imprimée/net, et cohérente avec le reste du document. Si \
  tu dois déduire, hésiter, ou si les montants/taxes ne se réconcilient pas \
  parfaitement, reste à 6 ou moins. En cas d'ambiguïté importante, utilise 3 ou \
  moins.
- Si la photo contient de l'arrière-plan visible (surface, objets non liés au \
  document), baisse la confiance de 1 point pour tous les champs par rapport à \
  ce que tu aurais donné pour un scan propre, car les conditions de capture sont \
  moins idéales.
- summary: courte description du contenu du document. Garde le resume a
  l'essentiel: decris simplement les articles, travaux ou achats, sans
  ajouter de formule comme "Bon de commande pour", "Recu pour",
  "Facture pour", ni le nom du fournisseur ou de la maison sauf si c'est
  indispensable pour comprendre l'achat.
- Exemple de mauvais resume: "Bon de commande pour quincaillerie Parent:
  joints toriques, cartouches/robinet et rondelle pour maison B"
- Exemple de bon resume: "Joints toriques, cartouches/robinet et rondelle"
- Les montants en nombre décimal (pas de $).
- field_confidence_scores: pour CHAQUE champ ci-dessus, retourne un score 0..9
  représentant ton niveau de confiance pour cette information précise. Si
  l'information n'est pas trouvée, retourne "NA" et non 0.
- Retourne UNIQUEMENT le JSON, sans texte additionnel.
"""


def _get_openai_model() -> str:
    """Return the configured model name."""
    return getattr(settings, "OPENAI_MODEL", "") or "gpt-5.6-sol"


def _get_openai_reasoning_effort() -> str:
    """Return a validated reasoning effort for OCR and verification."""
    effort = str(
        getattr(settings, "OPENAI_REASONING_EFFORT", "medium") or "medium"
    ).strip().lower()
    return effort if effort in {"none", "low", "medium", "high", "xhigh", "max"} else "medium"


class ReceiptOcrService:
    """Analyze receipt images using OpenAI Vision API."""

    @staticmethod
    def is_available() -> bool:
        if not OPENAI_AVAILABLE:
            return False
        return bool(getattr(settings, "OPENAI_API_KEY", ""))

    @staticmethod
    def _build_member_directory(house) -> list[str]:
        """Return dated residency lines so historical receipts resolve correctly."""
        if not house:
            return []

        from members.models import Residency

        residencies = (
            Residency.objects
            .filter(
                apartment__house=house,
                apartment__is_active=True,
            )
            .select_related("member", "apartment")
            .order_by(
                "apartment__code",
                "start_date",
                "member__last_name",
                "member__first_name",
            )
        )

        lines = []
        seen = set()
        for residency in residencies:
            member_name = (residency.member.display_name or "").strip()
            apartment_code = (residency.apartment.code or "").strip()
            if not member_name:
                continue
            start = residency.start_date.isoformat() if residency.start_date else "date inconnue"
            end = residency.end_date.isoformat() if residency.end_date else "présent"
            key = (member_name.casefold(), apartment_code, start, end)
            if key in seen:
                continue
            seen.add(key)
            if apartment_code:
                lines.append(
                    f"Appartement {apartment_code}: {member_name} "
                    f"({start} au {end})"
                )
            else:
                lines.append(f"{member_name} ({start} au {end})")
        return lines

    @classmethod
    def _build_batch_prompt(cls, house=None) -> str:
        """Build the OCR prompt, optionally enriched with the house member directory."""
        prompt = RECEIPT_BATCH_PROMPT.rstrip()
        member_lines = cls._build_member_directory(house)
        if not member_lines:
            return prompt

        member_directory = "\n".join(f"- {line}" for line in member_lines)
        return (
            f"{prompt}\n\n"
            "RÉPERTOIRE OFFICIEL DES RÉSIDENCES DE LA MAISON:\n"
            f"{member_directory}\n\n"
            "Normalisation des noms de membres:\n"
            "- Quand un document mentionne un membre (member_name, expense_member_name, "
            "validator_member_name), compare avec ce répertoire.\n"
            "- Utilise la date d'achat du document pour choisir la résidence active à "
            "cette date. Une date de fin est inclusive. Un ancien achat reste attribué "
            "à l'ancien résident; un achat postérieur au déménagement va au nouveau.\n"
            "- Si le nom OCR ressemble clairement à un membre du répertoire malgré une "
            "variation mineure (accent manquant, lettre en moins/en trop, OCR approximatif), "
            "retourne EXACTEMENT le nom officiel du répertoire dans le JSON.\n"
            "- Si un appartement du document permet d'identifier un membre du répertoire, "
            "retourne aussi l'appartement officiel du répertoire dans apartment_number, "
            "expense_apartment ou validator_apartment.\n"
            "- IMPORTANT pour validator_member_name / validator_apartment sur un paper_bc: "
            "le 2e signataire peut être une personne EXTERNE à la coop. N'utilise le "
            "répertoire pour ce 2e signataire que si le nom manuscrit correspond "
            "clairement à un membre. Sinon, conserve le nom lu tel quel et laisse "
            "validator_apartment vide.\n"
            "- N'invente jamais un membre absent du répertoire. Si aucun match crédible "
            "n'existe, conserve le texte lu tel quel.\n"
        )

    # ── Page image preparation ───────────────────────────────────────────

    @staticmethod
    def _build_analysis_images(
        file_map: dict[str, str | _PdfPageRange],
    ) -> list[tuple[str, bytes]]:
        """Render each source page as its own PNG for the vision request.

        PDF page ranges are created by ``_split_file_map`` so no request has
        more than ``MAX_PAGES_PER_BATCH`` pages.  Keeping the pages separate
        prevents the vision API from shrinking a tall composite until its text
        is unreadable.
        """
        max_target_width = 2200
        analysis_images = []

        for label, file_ref in file_map.items():
            if isinstance(file_ref, _PdfPageRange):
                file_path = file_ref.path
                first_page = file_ref.first_page
                last_page = file_ref.last_page
            else:
                file_path = file_ref
                first_page = None
                last_page = None

            lower_path = file_path.lower()
            page_images = []

            if lower_path.endswith(".pdf"):
                if not PDF2IMAGE_AVAILABLE:
                    logger.warning("PDF conversion is unavailable for %s", file_path)
                    continue
                try:
                    pdf_pages = convert_from_path(
                        file_path,
                        dpi=300,
                        first_page=first_page,
                        last_page=last_page,
                    )
                    page_number = first_page or 1
                    for offset, page_img in enumerate(pdf_pages):
                        page_images.append(
                            (f"{label} - Page {page_number + offset}", page_img)
                        )
                except Exception:
                    logger.exception("Cannot convert PDF %s", file_path)
                    continue
            else:
                try:
                    with Image.open(file_path) as source_image:
                        page_images.append((label, source_image.copy()))
                except Exception:
                    logger.exception("Cannot open image %s", file_path)
                    continue

            for page_label, source_image in page_images:
                image = ImageOps.exif_transpose(source_image)
                if image.mode != "RGB":
                    image = image.convert("RGB")

                if image.width > max_target_width:
                    ratio = max_target_width / image.width
                    image = image.resize(
                        (max_target_width, int(image.height * ratio)),
                        Image.LANCZOS,
                    )

                buffer = io.BytesIO()
                image.save(buffer, format="PNG", optimize=True)
                analysis_images.append((page_label, buffer.getvalue()))

        if not analysis_images:
            raise ValueError("Aucune image valide à analyser.")

        return analysis_images

    # ── API call ─────────────────────────────────────────────────────────

    @classmethod
    def _count_pages(cls, file_map: dict[str, str]) -> dict[str, int]:
        """Count how many analysis pages each file will produce."""
        counts = {}
        for label, file_path in file_map.items():
            lower_path = file_path.lower()
            if lower_path.endswith(".pdf") and PDF2IMAGE_AVAILABLE:
                try:
                    from pdf2image.pdf2image import pdfinfo_from_path
                    info = pdfinfo_from_path(file_path)
                    counts[label] = max(1, int(info.get("Pages", 1)))
                except Exception:
                    counts[label] = 1
            else:
                counts[label] = 1
        return counts

    @classmethod
    def _split_file_map(
        cls,
        file_map: dict[str, str],
    ) -> list[dict[str, str | _PdfPageRange]]:
        """Split file_map into sub-batches for OCR accuracy.

        Strategy:
        - Each PDF is isolated from other uploads and split into bounded page
          ranges. This preserves document grouping without ever constructing
          an over-tall image.
        - Individual images (PNG/JPG) are grouped together up to
          MAX_PAGES_PER_BATCH to remain efficient for single-receipt photos.
        """
        page_counts = cls._count_pages(file_map)
        batches = []
        image_batch = {}
        image_pages = 0

        for label, path in file_map.items():
            file_pages = page_counts.get(label, 1)
            is_pdf = path.lower().endswith(".pdf")

            if is_pdf:
                # Flush any pending images before the PDF
                if image_batch:
                    batches.append(image_batch)
                    image_batch = {}
                    image_pages = 0
                # Keep one PDF isolated, but split long PDFs into page ranges.
                for first_page in range(1, file_pages + 1, MAX_PAGES_PER_BATCH):
                    last_page = min(
                        first_page + MAX_PAGES_PER_BATCH - 1,
                        file_pages,
                    )
                    batches.append({
                        label: _PdfPageRange(path, first_page, last_page),
                    })
            else:
                # Group images together, respecting page limit
                if image_pages + file_pages > MAX_PAGES_PER_BATCH and image_batch:
                    batches.append(image_batch)
                    image_batch = {}
                    image_pages = 0
                image_batch[label] = path
                image_pages += file_pages

        if image_batch:
            batches.append(image_batch)

        return batches

    @classmethod
    def _analyze_single_batch(
        cls,
        file_map: dict[str, str | _PdfPageRange],
        house=None,
    ) -> list[dict]:
        """Send separately preserved page images to OpenAI and parse results."""
        analysis_images = cls._build_analysis_images(file_map)
        page_labels = [label for label, _ in analysis_images]
        num_pages = len(page_labels)

        client = OpenAI(api_key=settings.OPENAI_API_KEY)
        model = _get_openai_model()
        reasoning_effort = _get_openai_reasoning_effort()
        prompt = cls._build_batch_prompt(house)

        user_content = [{
            "type": "text",
            "text": (
                f"Ce message contient {num_pages} page(s) provenant "
                f"de {len(file_map)} fichier(s). Chaque image est précédée "
                "de son libellé exact. Analyse chaque page, classifie les "
                "documents et extrais les informations en JSON."
            ),
        }]
        for page_label, image_bytes in analysis_images:
            image_b64 = base64.b64encode(image_bytes).decode("utf-8")
            user_content.extend([
                {
                    "type": "text",
                    "text": f"FICHIER/PAGE: {page_label}",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{image_b64}",
                        "detail": "original",
                    },
                },
            ])

        request_options = {
            "model": model,
            "messages": [
                {"role": "system", "content": prompt},
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "reasoning_effort": reasoning_effort,
            "max_completion_tokens": 16000,
        }
        if reasoning_effort == "none":
            request_options["temperature"] = 0

        response = client.chat.completions.create(**request_options)

        raw = response.choices[0].message.content.strip()
        logger.info("GPT batch analysis (model=%s, %d pages): %s",
                     model, num_pages, raw)
        return cls._parse_batch_response(raw, list(file_map.keys()))

    @classmethod
    def analyze_batch(cls, file_map: dict[str, str], house=None) -> list[dict]:
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
            results = cls._analyze_single_batch(sub_map, house=house)
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

    @staticmethod
    def _safe_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "yes", "oui"}

    @staticmethod
    def _json_safe_value(value):
        if isinstance(value, dict):
            return {
                str(key): ReceiptOcrService._json_safe_value(item)
                for key, item in value.items()
            }
        if isinstance(value, (list, tuple)):
            return [ReceiptOcrService._json_safe_value(item) for item in value]
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, date):
            return value.isoformat()
        return value

    @classmethod
    def _parse_one(cls, data: dict) -> dict:
        """Parse a single document entry from the GPT JSON response."""
        reimburse_to = str(data.get("reimburse_to") or "").strip().lower()
        if reimburse_to not in {"member", "supplier"}:
            reimburse_to = ""

        parsed = {
            "filename": str(data.get("filename") or ""),
            "document_type": str(data.get("document_type") or "receipt").strip(),
            "bc_number": str(data.get("bc_number") or "").strip(),
            "associated_bc_number": str(data.get("associated_bc_number") or "").strip(),
            "supplier_name": str(data.get("supplier_name") or "").strip(),
            "supplier_address": str(data.get("supplier_address") or "").strip(),
            "reimburse_to": reimburse_to,
            "expense_member_name": str(data.get("expense_member_name") or "").strip(),
            "expense_apartment": str(data.get("expense_apartment") or "").strip(),
            "validator_member_name": str(data.get("validator_member_name") or "").strip(),
            "validator_apartment": str(data.get("validator_apartment") or "").strip(),
            "signer_roles_ambiguous": cls._safe_bool(data.get("signer_roles_ambiguous", False)),
            "member_name": str(data.get("member_name") or "").strip(),
            "apartment_number": str(data.get("apartment_number") or "").strip(),
            "merchant": str(data.get("merchant") or ""),
            "purchase_date": cls._safe_date(data.get("purchase_date")),
            "subtotal": cls._safe_decimal(data.get("subtotal")),
            "tps": cls._safe_decimal(data.get("tps")),
            "tvq": cls._safe_decimal(data.get("tvq")),
            "untaxed_extra_amount": cls._safe_decimal(data.get("untaxed_extra_amount")),
            "total": cls._safe_decimal(data.get("total")),
            "summary": str(data.get("summary") or "").strip(),
            "field_confidence_scores": build_complete_ai_confidence_scores(
                data.get("field_confidence_scores"),
                allowed_keys=OCR_FIELD_CONFIDENCE_KEYS,
            ),
        }
        return cls._apply_conservative_confidence_guards(parsed)

    @staticmethod
    def _has_meaningful_extraction(result: dict) -> bool:
        """Return whether OCR found at least one reviewable source field."""
        return any(
            result.get(field_name) not in (None, "")
            for field_name in _MEANINGFUL_EXTRACTION_FIELDS
        )

    @staticmethod
    def _apply_conservative_confidence_guards(parsed: dict) -> dict:
        warning = build_amount_consistency_warning(
            subtotal=parsed.get("subtotal"),
            tps=parsed.get("tps"),
            tvq=parsed.get("tvq"),
            total=parsed.get("total"),
            untaxed_extra_amount=parsed.get("untaxed_extra_amount"),
        )
        parsed["field_confidence_scores"] = cap_amount_confidence_scores(
            parsed.get("field_confidence_scores"),
            warning,
        )
        return parsed

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
        already_matched = set()
        for r in results:
            gpt_fn = r["filename"]
            # Exact match
            if gpt_fn in filenames:
                r["source_filename"] = gpt_fn
                already_matched.add(gpt_fn)
                continue
            # Fuzzy: GPT filename contains or is contained by an original name,
            # also checking the base name (without ::rPK suffix).
            matched = False
            for fn in filenames:
                base_fn = fn.split("::r")[0] if "::r" in fn else fn
                if fn and gpt_fn and (
                    fn in gpt_fn or gpt_fn in fn
                    or base_fn in gpt_fn or gpt_fn in base_fn
                ):
                    r["source_filename"] = fn
                    already_matched.add(fn)
                    matched = True
                    break
            if not matched:
                # Fallback: assign to the first filename not yet matched
                for fn in filenames:
                    if fn not in already_matched:
                        r["source_filename"] = fn
                        already_matched.add(fn)
                        matched = True
                        break
                if not matched:
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
            "reimburse_to": "",
            "expense_member_name": "",
            "expense_apartment": "",
            "validator_member_name": "",
            "validator_apartment": "",
            "signer_roles_ambiguous": False,
            "member_name": "",
            "apartment_number": "",
            "merchant": "",
            "purchase_date": None,
            "subtotal": None,
            "tps": None,
            "tvq": None,
            "untaxed_extra_amount": None,
            "total": None,
            "summary": "",
            "field_confidence_scores": build_complete_ai_confidence_scores(
                {},
                allowed_keys=OCR_FIELD_CONFIDENCE_KEYS,
            ),
        }

    # ── Batch processing pipeline ────────────────────────────────────────

    @classmethod
    def process_receipts_batch(cls, receipt_file_objs: list, house=None) -> tuple[list[dict], str]:
        """
        Process multiple ReceiptFile objects in a single API call.
        Returns (list_of_results, error_message).
        """
        from .models import ReceiptOcrResult, ReceiptExtractedFields, OcrStatus

        empties = [cls._empty_result(r.original_filename) for r in receipt_file_objs]

        if not cls.is_available():
            msg = (
                "Clé API OpenAI non configurée. Ajoutez OPENAI_API_KEY ou "
                "OPENAI_API_KEY_FILE dans la configuration."
            )
            logger.warning(msg)
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])
            return empties, msg

        # Build file map: unique_key → file path (images AND PDFs)
        # Use unique keys (filename::rPK) to avoid collisions when
        # multiple uploads share the same original_filename.
        file_map = {}
        for r in receipt_file_objs:
            r.ocr_status = OcrStatus.PENDING
            r.save(update_fields=["ocr_status"])
            ct = (r.content_type or "").lower()
            if ct.startswith("image/") or ct == "application/pdf":
                unique_key = f"{r.original_filename}::r{r.pk}"
                file_map[unique_key] = r.file.path

        if not file_map:
            msg = "Aucun fichier analysable (images et PDF supportés)."
            for r in receipt_file_objs:
                r.ocr_status = OcrStatus.FAILED
                r.ocr_raw_text = msg
                r.save(update_fields=["ocr_status", "ocr_raw_text"])
            return empties, msg

        if house is None:
            for receipt_obj in receipt_file_objs:
                bon = getattr(receipt_obj, "bon_de_commande", None)
                if bon and getattr(bon, "house", None):
                    house = bon.house
                    break

        try:
            results = cls.analyze_batch(file_map, house=house)

            # Group results by source_filename (one file may produce multiple docs)
            from collections import defaultdict
            results_by_source = defaultdict(list)
            for r in results:
                results_by_source[r.get("source_filename", r["filename"])].append(r)

            model = _get_openai_model()
            extraction_warnings = []

            for receipt_obj in receipt_file_objs:
                unique_key = f"{receipt_obj.original_filename}::r{receipt_obj.pk}"
                fn = receipt_obj.original_filename
                file_results = results_by_source.get(unique_key, [cls._empty_result(fn)])
                json_safe_file_results = cls._json_safe_value(file_results)
                raw_result_text = json.dumps(json_safe_file_results)
                meaningful_results = [
                    result
                    for result in file_results
                    if cls._has_meaningful_extraction(result)
                ]

                if meaningful_results:
                    receipt_obj.ocr_raw_text = raw_result_text
                    receipt_obj.ocr_status = OcrStatus.EXTRACTED
                    primary = next(
                        (
                            result
                            for result in meaningful_results
                            if result.get("document_type") == "paper_bc"
                        ),
                        meaningful_results[0],
                    )
                else:
                    warning = (
                        f"Aucune donnée exploitable extraite de « {fn} ». "
                        "Vérifiez le document et relancez l'analyse OCR."
                    )
                    extraction_warnings.append(warning)
                    receipt_obj.ocr_raw_text = warning
                    receipt_obj.ocr_status = OcrStatus.FAILED
                    primary = cls._empty_result(fn)

                receipt_obj.save(update_fields=["ocr_raw_text", "ocr_status"])

                ReceiptOcrResult.objects.create(
                    receipt_file=receipt_obj,
                    engine_name=f"openai-{model}",
                    raw_text=raw_result_text,
                    raw_json=json_safe_file_results,
                )

                ReceiptExtractedFields.objects.update_or_create(
                    receipt_file=receipt_obj,
                    defaults={
                        "document_type_candidate": primary.get("document_type", "receipt"),
                        "bc_number_candidate": primary.get("bc_number", ""),
                        "associated_bc_number_candidate": primary.get("associated_bc_number", ""),
                        "supplier_name_candidate": primary.get("supplier_name", ""),
                        "supplier_address_candidate": primary.get("supplier_address", ""),
                        "reimburse_to_candidate": primary.get("reimburse_to", ""),
                        "expense_member_name_candidate": primary.get("expense_member_name", ""),
                        "expense_apartment_candidate": primary.get("expense_apartment", ""),
                        "validator_member_name_candidate": primary.get("validator_member_name", ""),
                        "validator_apartment_candidate": primary.get("validator_apartment", ""),
                        "signer_roles_ambiguous_candidate": primary.get("signer_roles_ambiguous", False),
                        "member_name_candidate": primary.get("member_name", ""),
                        "apartment_number_candidate": primary.get("apartment_number", ""),
                        "merchant_candidate": primary.get("merchant", ""),
                        "purchase_date_candidate": primary.get("purchase_date"),
                        "subtotal_candidate": primary.get("subtotal"),
                        "tps_candidate": primary.get("tps"),
                        "tvq_candidate": primary.get("tvq"),
                        "untaxed_extra_amount_candidate": primary.get("untaxed_extra_amount"),
                        "total_candidate": primary.get("total"),
                        "summary_candidate": primary.get("summary", ""),
                        "candidate_confidence_scores": primary.get("field_confidence_scores", {}),
                    },
                )

            return results, " ".join(extraction_warnings)

        except Exception as e:
            error_str = str(e)
            if "insufficient_quota" in error_str or "429" in error_str:
                msg = "Quota OpenAI épuisé. Vérifiez votre forfait sur platform.openai.com."
            elif "401" in error_str or "invalid_api_key" in error_str:
                msg = (
                    "Clé API OpenAI invalide. Vérifiez OPENAI_API_KEY ou "
                    "OPENAI_API_KEY_FILE dans la configuration."
                )
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


# ══════════════════════════════════════════════════════════════════════════════
# Duplicate Detection Service
# ══════════════════════════════════════════════════════════════════════════════

DUPLICATE_COMPARISON_PROMPT = """\
Tu es un assistant spécialisé dans la détection de factures en doublon \
pour une coopérative d'habitation au Québec.

On te présente DEUX images de factures/reçus. Tu dois déterminer si \
ces deux documents correspondent au MÊME achat (même transaction).

Critères de comparaison :
- Même fournisseur / marchand
- Même date (ou dates très proches, ±2 jours)
- Mêmes articles / descriptions
- Même montant total
- Même numéro de facture (si visible)

NOTE : Les deux images peuvent être de qualité différente, sous un \
angle différent, ou l'une peut être une photocopie/scan de l'autre. \
Concentre-toi sur le CONTENU, pas la qualité de l'image.

Réponds UNIQUEMENT avec un JSON valide :
{
  "date_new": "YYYY-MM-DD ou chaîne vide",
  "date_old": "YYYY-MM-DD ou chaîne vide",
  "document_number_new": "numéro de facture/reçu/transaction ou chaîne vide",
  "document_number_old": "numéro de facture/reçu/transaction ou chaîne vide",
  "dates_match": true/false,
  "document_numbers_match": true/false,
  "is_same_purchase": true/false,
  "confidence": 0.0 à 1.0,
  "reasoning": "Explication courte de ta décision",
  "field_confidence_scores": {
    "date_new": 0 à 9,
    "date_old": 0 à 9,
    "document_number_new": 0 à 9,
    "document_number_old": 0 à 9,
    "dates_match": 0 à 9,
    "document_numbers_match": 0 à 9,
    "is_same_purchase": 0 à 9,
    "confidence": 0 à 9,
    "reasoning": 0 à 9
  }
}

Règles obligatoires :
- si les deux dates sont lisibles et différentes, is_same_purchase doit être false;
- si les deux numéros sont lisibles et différents, is_same_purchase doit être false;
- n'invente jamais une date ou un numéro;
- si une information manque, utilise une chaîne vide pour sa valeur et "NA"
  dans field_confidence_scores plutôt que 0.
"""


class DuplicateDetectionService:
    """Detect duplicate invoices/receipts using totals comparison and GPT Vision."""

    @staticmethod
    def _normalize_confidence(value) -> float:
        """Normalize GPT confidence to a 0.0-1.0 float."""
        if value is None:
            return 0.0
        try:
            if isinstance(value, str):
                value = value.strip().rstrip("%")
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0

        if confidence > 1.0 and confidence <= 100.0:
            confidence = confidence / 100.0
        if confidence < 0.0:
            return 0.0
        if confidence > 1.0:
            return 1.0
        return confidence

    @staticmethod
    def _normalize_comparison_date(value) -> str:
        if isinstance(value, date):
            return value.isoformat()
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return date.fromisoformat(text).isoformat()
        except ValueError:
            return ""

    @staticmethod
    def _normalize_document_number(value) -> str:
        return "".join(
            character.casefold()
            for character in str(value or "")
            if character.isalnum()
        )

    @staticmethod
    def _receipt_purchase_date(receipt_file):
        try:
            extracted_fields = receipt_file.extracted_fields
        except Exception:
            return None
        return (
            extracted_fields.final_purchase_date
            or extracted_fields.purchase_date_candidate
        )

    @classmethod
    def _stored_dates_conflict(cls, receipt_file_new, receipt_file_old) -> bool:
        new_date = cls._receipt_purchase_date(receipt_file_new)
        old_date = cls._receipt_purchase_date(receipt_file_old)
        return bool(new_date and old_date and new_date != old_date)

    @staticmethod
    def _same_file_checksum(receipt_file_new, receipt_file_old) -> bool:
        new_checksum = str(receipt_file_new.sha256_checksum or "").strip().lower()
        old_checksum = str(receipt_file_old.sha256_checksum or "").strip().lower()
        return bool(new_checksum and old_checksum and new_checksum == old_checksum)

    @classmethod
    def _enforce_comparison_evidence(cls, result: dict) -> dict:
        """Apply deterministic date/identifier gates to an AI comparison."""
        date_new = cls._normalize_comparison_date(result.get("date_new"))
        date_old = cls._normalize_comparison_date(result.get("date_old"))
        number_new_raw = str(result.get("document_number_new") or "").strip()
        number_old_raw = str(result.get("document_number_old") or "").strip()
        number_new = cls._normalize_document_number(number_new_raw)
        number_old = cls._normalize_document_number(number_old_raw)

        date_conflict = bool(date_new and date_old and date_new != date_old)
        number_conflict = bool(
            number_new and number_old and number_new != number_old
        )
        dates_match = bool(date_new and date_old and date_new == date_old)
        document_numbers_match = bool(
            number_new and number_old and number_new == number_old
        )
        evidence_complete = dates_match and document_numbers_match

        enforced = dict(result)
        enforced.update({
            "date_new": date_new,
            "date_old": date_old,
            "document_number_new": number_new_raw,
            "document_number_old": number_old_raw,
            "dates_match": dates_match,
            "document_numbers_match": document_numbers_match,
            "evidence_complete": evidence_complete,
            "hard_conflict": date_conflict or number_conflict,
        })
        if date_conflict or number_conflict:
            conflicts = []
            if date_conflict:
                conflicts.append(f"dates différentes ({date_new} / {date_old})")
            if number_conflict:
                conflicts.append(
                    "numéros différents "
                    f"({number_new_raw} / {number_old_raw})"
                )
            original_reason = str(result.get("reasoning") or "").strip()
            enforced["is_same_purchase"] = False
            enforced["confidence"] = 0.0
            enforced["reasoning"] = (
                f"Pas un doublon : {', '.join(conflicts)}."
                + (f" {original_reason}" if original_reason else "")
            )
        return enforced

    @classmethod
    def is_confirmed_duplicate_pair(cls, receipt_file_new, receipt_file_old) -> bool:
        """Return whether a pair has evidence stronger than a matching total."""
        if cls._stored_dates_conflict(receipt_file_new, receipt_file_old):
            return False
        if cls._same_file_checksum(receipt_file_new, receipt_file_old):
            return True

        from .models import DuplicateFlag, DuplicateFlagStatus

        return DuplicateFlag.objects.filter(
            receipt_file=receipt_file_new,
            suspected_duplicate_receipt=receipt_file_old,
            status=DuplicateFlagStatus.CONFIRMED_DUPLICATE,
        ).exists() or DuplicateFlag.objects.filter(
            receipt_file=receipt_file_old,
            suspected_duplicate_receipt=receipt_file_new,
            status=DuplicateFlagStatus.CONFIRMED_DUPLICATE,
        ).exists()

    @staticmethod
    def find_matching_totals(receipt_file, house, lookback_years=2):
        """
        Find existing receipts in the same house with matching final_total.
        Returns list of ReceiptExtractedFields with matching totals.
        """
        from django.db import models as db_models
        from django.utils import timezone as tz
        from datetime import timedelta
        from .models import ReceiptExtractedFields, OcrStatus as OS, BonStatus

        try:
            ef = receipt_file.extracted_fields
        except ReceiptExtractedFields.DoesNotExist:
            return []

        target_total = ef.final_total or ef.total_candidate
        if target_total is None:
            return []

        cutoff = tz.now() - timedelta(days=lookback_years * 365)

        matches = (
            ReceiptExtractedFields.objects
            .filter(
                receipt_file__bon_de_commande__house=house,
                receipt_file__created_at__gte=cutoff,
                receipt_file__archived_at__isnull=True,
                receipt_file__ocr_status__in=[OS.EXTRACTED, OS.CORRECTED],
                receipt_file__bon_de_commande__is_scan_session=False,
            )
            .exclude(receipt_file_id=receipt_file.pk)
            .exclude(receipt_file__bon_de_commande__status=BonStatus.VOID)
            .filter(
                db_models.Q(final_total=target_total)
                | db_models.Q(total_candidate=target_total)
            )
            .select_related("receipt_file", "receipt_file__bon_de_commande")
        )
        return list(matches)

    @staticmethod
    def _receipt_to_base64(receipt_file) -> str | None:
        """Convert a receipt file to base64 image for GPT comparison."""
        if not PILLOW_AVAILABLE:
            return None

        try:
            file_path = receipt_file.file.path
        except (ValueError, AttributeError):
            return None

        content_type = receipt_file.content_type or ""

        if content_type.startswith("image/"):
            try:
                img = Image.open(file_path).convert("RGB")
                max_w = 800
                if img.width > max_w:
                    ratio = max_w / img.width
                    img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode("utf-8")
            except Exception:
                logger.exception("Failed to convert image for dup comparison: %s", file_path)
                return None

        elif content_type == "application/pdf":
            if not PDF2IMAGE_AVAILABLE:
                return None
            try:
                images = convert_from_path(file_path, first_page=1, last_page=1, dpi=150)
                if not images:
                    return None
                img = images[0].convert("RGB")
                max_w = 800
                if img.width > max_w:
                    ratio = max_w / img.width
                    img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                return base64.b64encode(buf.getvalue()).decode("utf-8")
            except Exception:
                logger.exception("Failed to convert PDF for dup comparison: %s", file_path)
                return None

        return None

    @classmethod
    def compare_with_gpt(cls, receipt_file_new, receipt_file_old) -> dict:
        """
        Use GPT Vision to compare two receipt images.
        Returns dict with keys: is_same_purchase, confidence, reasoning.
        """
        if not OPENAI_AVAILABLE or not ReceiptOcrService.is_available():
            return {
                "is_same_purchase": False,
                "confidence": 0.0,
                "reasoning": "Service OpenAI non disponible",
                "field_confidence_scores": build_complete_ai_confidence_scores(
                    {},
                    allowed_keys=DUPLICATE_FIELD_CONFIDENCE_KEYS,
                ),
            }

        img1_b64 = cls._receipt_to_base64(receipt_file_new)
        img2_b64 = cls._receipt_to_base64(receipt_file_old)

        if not img1_b64 or not img2_b64:
            return {
                "is_same_purchase": False,
                "confidence": 0.0,
                "reasoning": "Impossible de convertir les images pour comparaison",
                "field_confidence_scores": build_complete_ai_confidence_scores(
                    {},
                    allowed_keys=DUPLICATE_FIELD_CONFIDENCE_KEYS,
                ),
            }

        try:
            client = OpenAI(api_key=settings.OPENAI_API_KEY)
            model = _get_openai_model()
            reasoning_effort = _get_openai_reasoning_effort()

            request_options = {
                "model": model,
                "messages": [
                    {"role": "system", "content": DUPLICATE_COMPARISON_PROMPT},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Compare ces deux factures/reçus. "
                                    "Sont-ils le MÊME achat (même transaction) ?"
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img1_b64}",
                                },
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{img2_b64}",
                                },
                            },
                        ],
                    },
                ],
                "reasoning_effort": reasoning_effort,
                "max_completion_tokens": 4000,
            }
            if reasoning_effort == "none":
                request_options["temperature"] = 0

            response = client.chat.completions.create(**request_options)

            raw = response.choices[0].message.content.strip()
            logger.info("GPT duplicate comparison (model=%s): %s", model, raw)

            if "```json" in raw:
                raw = raw.split("```json")[1].split("```")[0].strip()
            elif "```" in raw:
                raw = raw.split("```")[1].split("```")[0].strip()

            result = json.loads(raw)
            confidence = cls._normalize_confidence(result.get("confidence", 0.0))
            normalized_result = {
                "is_same_purchase": ReceiptOcrService._safe_bool(result.get("is_same_purchase", False)),
                "confidence": confidence,
                "reasoning": str(result.get("reasoning", "")),
                "date_new": result.get("date_new", ""),
                "date_old": result.get("date_old", ""),
                "document_number_new": result.get("document_number_new", ""),
                "document_number_old": result.get("document_number_old", ""),
                "field_confidence_scores": build_complete_ai_confidence_scores(
                    result.get("field_confidence_scores"),
                    allowed_keys=DUPLICATE_FIELD_CONFIDENCE_KEYS,
                ),
            }
            return cls._enforce_comparison_evidence(normalized_result)

        except Exception as e:
            logger.exception("GPT duplicate comparison failed: %s", e)
            return {
                "is_same_purchase": False,
                "confidence": 0.0,
                "reasoning": f"Erreur lors de la comparaison : {str(e)[:200]}",
                "field_confidence_scores": build_complete_ai_confidence_scores(
                    {},
                    allowed_keys=DUPLICATE_FIELD_CONFIDENCE_KEYS,
                ),
            }

    @classmethod
    def check_and_flag_duplicates(cls, receipt_file, house):
        """
        Full duplicate detection pipeline for a single receipt.
        1. Find matching totals in the house
        2. If matches found, compare images with GPT
        3. Create DuplicateFlag entries
        Returns list of created DuplicateFlag objects.
        """
        from .models import DuplicateFlag, DuplicateFlagStatus

        matching_efs = cls.find_matching_totals(receipt_file, house)
        if not matching_efs:
            return []

        created_flags = []
        for match_ef in matching_efs:
            existing_receipt = match_ef.receipt_file

            # Skip if already flagged in either direction
            already = DuplicateFlag.objects.filter(
                receipt_file=receipt_file,
                suspected_duplicate_receipt=existing_receipt,
            ).exists() or DuplicateFlag.objects.filter(
                receipt_file=existing_receipt,
                suspected_duplicate_receipt=receipt_file,
            ).exists()
            if already:
                continue

            # A known date conflict is a deterministic negative and must not
            # incur an AI call or create a warning.
            if cls._stored_dates_conflict(receipt_file, existing_receipt):
                continue

            exact_file_match = cls._same_file_checksum(
                receipt_file,
                existing_receipt,
            )
            if exact_file_match:
                purchase_date = cls._receipt_purchase_date(receipt_file)
                date_note = (
                    f", date {purchase_date.isoformat()}"
                    if purchase_date
                    else ""
                )
                comparison = {
                    "is_same_purchase": True,
                    "confidence": 1.0,
                    "reasoning": (
                        "Fichier source identique (SHA-256 exact)"
                        f"{date_note}."
                    ),
                    "field_confidence_scores": (
                        build_complete_ai_confidence_scores(
                            {},
                            allowed_keys=DUPLICATE_FIELD_CONFIDENCE_KEYS,
                        )
                    ),
                    "evidence_complete": True,
                    "hard_conflict": False,
                }
            else:
                comparison = cls.compare_with_gpt(
                    receipt_file,
                    existing_receipt,
                )

            # A negative comparison or conflicting date/document number is
            # not a duplicate warning.
            if (
                not comparison.get("is_same_purchase")
                or comparison.get("hard_conflict")
            ):
                continue

            status = DuplicateFlagStatus.PENDING
            if exact_file_match or (
                comparison["confidence"] >= 0.90
                and comparison.get("evidence_complete")
            ):
                status = DuplicateFlagStatus.CONFIRMED_DUPLICATE

            flag = DuplicateFlag.objects.create(
                receipt_file=receipt_file,
                suspected_duplicate_receipt=existing_receipt,
                confidence=Decimal(str(round(comparison["confidence"], 2))),
                gpt_comparison_result=comparison["reasoning"],
                field_confidence_scores=comparison.get("field_confidence_scores", {}),
                status=status,
            )
            created_flags.append(flag)

            logger.info(
                "Duplicate flag created: receipt #%d ↔ #%d (confidence=%.2f, status=%s)",
                receipt_file.pk, existing_receipt.pk,
                comparison["confidence"], status,
            )

        return created_flags
