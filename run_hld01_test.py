"""One-off real test — not part of the final pipeline. Runs the actual classify
and extract steps against HLD-01.pdf using Groq, so we can see real AI output
before trusting it on the rest of the documents."""
import json

from bookable_payable.classify import classify_document
from bookable_payable.extraction import extract_payable
from bookable_payable.ingestion import render_pdf_pages
from bookable_payable.llm import get_vision_client

pages = render_pdf_pages("documents/HLD-01.pdf")
print(f"Rendered {len(pages)} page(s)\n")

client = get_vision_client()

print("=== CLASSIFICATION ===")
classification = classify_document(pages, client)
print(json.dumps(classification, indent=2, ensure_ascii=False))

if classification["is_payable"]:
    print("\n=== EXTRACTION ===")
    extracted = extract_payable(pages, client)
    print(json.dumps(extracted, indent=2, ensure_ascii=False))