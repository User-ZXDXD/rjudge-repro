"""Pull the leaderboard tables out of the R-Judge paper PDF."""
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
from PyPDF2 import PdfReader

path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "upstream", "R-Judge_paper.pdf")
reader = PdfReader(path)
print("pages:", len(reader.pages))
for i, page in enumerate(reader.pages):
    text = page.extract_text() or ""
    if "74.42" in text or "Specificity" in text or "Validity" in text:
        print("=" * 20, "page", i + 1, "=" * 20)
        print(text[:4200])
