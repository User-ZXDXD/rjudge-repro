"""Dataset statistics for the downloaded R-Judge release."""
import json
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rj_runner

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "upstream", "R-Judge-main", "data")

cases = rj_runner.load_cases(DATA, "ALL")
print("total records:", len(cases))
print("by attack_type:", dict(Counter(e.get("attack_type") for e, _ in cases)))
print("by label:", dict(Counter(("unsafe" if e["label"] == 1 else "safe") for e, _ in cases)))
print("by category:", dict(Counter(s for _, s in cases)))
print("by scenario:", dict(Counter(e.get("scenario") for e, _ in cases)))
