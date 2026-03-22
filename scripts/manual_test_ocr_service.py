"""Integration test: run enhanced OCR service on example PDFs inside Docker."""
import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings"

import django
django.setup()

from bons.ocr_service import ReceiptOcrService


def test_service_pdf():
    """Test that the OCR service can process PDFs and extract paper BC fields."""
    # Build a file_map as the service would
    pdf_path = "budget_example_spreadsheet/BC16011.pdf"
    file_map = {"BC16011.pdf": pdf_path}

    print(f"Testing analyze_batch with PDF: {pdf_path}")
    print(f"OCR available: {ReceiptOcrService.is_available()}")

    if not ReceiptOcrService.is_available():
        print("SKIP: OpenAI API not available")
        return

    results = ReceiptOcrService.analyze_batch(file_map)
    print(f"\nResults ({len(results)} document(s)):")
    for r in results:
        print(f"\n  filename: {r['filename']}")
        print(f"  document_type: {r['document_type']}")
        print(f"  bc_number: {r['bc_number']}")
        print(f"  associated_bc_number: {r['associated_bc_number']}")
        print(f"  supplier_name: {r['supplier_name']}")
        print(f"  member_name: {r['member_name']}")
        print(f"  total: {r['total']}")
        print(f"  summary: {r['summary']}")

    # Validate key expectations
    paper_bcs = [r for r in results if r["document_type"] == "paper_bc"]
    invoices = [r for r in results if r["document_type"] == "invoice"]

    print(f"\n=== Validation ===")
    print(f"Paper BCs found: {len(paper_bcs)}")
    print(f"Invoices found: {len(invoices)}")

    assert len(paper_bcs) >= 1, f"Expected at least 1 paper_bc, got {len(paper_bcs)}"
    bc = paper_bcs[0]
    assert bc["bc_number"] == "16011", f"Expected bc_number=16011, got {bc['bc_number']}"
    assert bc["total"] is not None, "Expected non-null total on paper BC"
    print(f"✅ Paper BC #16011 detected correctly (total={bc['total']})")

    if invoices:
        inv = invoices[0]
        assert inv["associated_bc_number"] == "16011", \
            f"Expected associated_bc_number=16011, got {inv['associated_bc_number']}"
        print(f"✅ Invoice correctly associated with BC #16011 (total={inv['total']})")

    print("\n✅ All validations passed!")


if __name__ == "__main__":
    test_service_pdf()
