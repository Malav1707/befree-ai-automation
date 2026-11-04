"""Transaction classifier - NO LLM.

Hybrid of two traditional approaches:
  1. Heuristic rules  : keyword lists in data/category_rules.json (fast, explainable, easy to edit)
  2. Machine learning : TF-IDF (character n-grams) + Logistic Regression, trained on
                        data/training_data.csv (+ any user corrections). Handles merchants
                        that are not in the keyword list and small OCR typos.

Order: priority rules -> keyword rules -> ML (if confident) -> fallback rules -> "Others".
Every prediction also returns a confidence and the method used, so low-confidence
rows can be reviewed by the user.
"""
import json
import os
import re

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline

from .config import CORRECTIONS_FILE, RULES_FILE, TRAINING_FILE

ML_CONFIDENCE_THRESHOLD = 0.35  # below this the ML guess is not trusted
DEFAULT_CATEGORY = "Others"

# Categories that only make sense for money going OUT. If one of these is predicted for a
# credit (money coming IN), it is most likely a refund or a transfer from someone.
EXPENSE_ONLY = {"Food & Dining", "Groceries", "Shopping", "Transport", "Fuel", "Travel", "Utilities & Bills",
                "Rent", "EMI & Loans", "Credit Card Payment", "Insurance", "Healthcare", "Education",
                "Entertainment", "Cash Withdrawal", "Personal Care", "Bank Charges", "Taxes"}


def normalize(text):
    """'UPI/123456789012/SWIGGY*Order@icici' -> 'upi swiggy order icici'."""
    text = str(text or "").lower()
    text = re.sub(r"\d{4,}", " ", text)          # long numbers = reference ids, not useful
    text = re.sub(r"[^a-z0-9&]+", " ", text)      # punctuation / separators -> space
    return re.sub(r"\s+", " ", text).strip()


def _compile_keywords(rule_dict):
    """{category: [kw, ...]} -> [(category, keyword, regex), ...]."""
    compiled = []
    for category, keywords in rule_dict.items():
        for kw in keywords:
            kw_norm = normalize(kw)
            if kw_norm:
                compiled.append((category, kw_norm, re.compile(r"\b" + re.escape(kw_norm) + r"\b")))
    return compiled


class TransactionClassifier:
    def __init__(self, rules_file=RULES_FILE, training_file=TRAINING_FILE, corrections_file=CORRECTIONS_FILE):
        self.rules_file = rules_file
        self.training_file = training_file
        self.corrections_file = corrections_file
        self.model = None
        self.load_rules()
        self.train()

    # ------------------------------------------------------------------ rules
    def load_rules(self):
        with open(self.rules_file, encoding="utf-8") as f:
            rules = json.load(f)
        self.priority_rules = _compile_keywords(rules.get("priority_rules", {}))
        self.rules = _compile_keywords(rules.get("rules", {}))
        self.fallback_rules = _compile_keywords(rules.get("fallback_rules", {}))

    @staticmethod
    def _first_match(text, compiled):
        for category, kw, rx in compiled:
            if rx.search(text):
                return category, kw
        return None

    @staticmethod
    def _longest_match(text, compiled):
        best = None
        for category, kw, rx in compiled:
            if rx.search(text) and (best is None or len(kw) > len(best[1])):
                best = (category, kw)
        return best

    # --------------------------------------------------------------------- ML
    def _training_frame(self):
        frames = [pd.read_csv(self.training_file)]
        if self.corrections_file and os.path.exists(self.corrections_file):
            frames.append(pd.read_csv(self.corrections_file))
        df = pd.concat(frames, ignore_index=True).dropna(subset=["description", "category"])
        df["text"] = df["description"].map(normalize)
        return df[df["text"] != ""]

    def train(self):
        df = self._training_frame()
        if df["category"].nunique() < 2:
            self.model = None
            return
        self.model = make_pipeline(
            # char n-grams inside word boundaries: robust to "SWIGY", "zomato@hdfc", OCR noise
            TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), sublinear_tf=True),
            LogisticRegression(max_iter=2000, C=10, class_weight="balanced"),
        )
        self.model.fit(df["text"], df["category"])
        self.training_size = len(df)

    def _ml_predict(self, texts):
        if self.model is None or not texts:
            return [(None, 0.0)] * len(texts)
        probs = self.model.predict_proba(texts)
        classes = self.model.classes_
        return [(classes[p.argmax()], float(p.max())) for p in probs]

    # -------------------------------------------------------------- classify
    def classify(self, description, txn_type=None):
        return self.classify_many([description], [txn_type])[0]

    def classify_many(self, descriptions, txn_types=None):
        """Return list of dicts: {category, confidence, method}."""
        txn_types = txn_types or [None] * len(descriptions)
        texts = [normalize(d) for d in descriptions]
        results = [None] * len(texts)

        # 1 + 2: rules
        need_ml = []
        for i, text in enumerate(texts):
            hit = self._first_match(text, self.priority_rules) or self._longest_match(text, self.rules)
            if hit:
                results[i] = {"category": hit[0], "confidence": 1.0, "method": f"rule:{hit[1]}"}
            else:
                need_ml.append(i)

        # 3: ML for the rest
        ml_out = self._ml_predict([texts[i] for i in need_ml])
        for i, (cat, prob) in zip(need_ml, ml_out):
            if cat and prob >= ML_CONFIDENCE_THRESHOLD:
                results[i] = {"category": cat, "confidence": round(prob, 2), "method": "ml"}
                continue
            # 4: fallback rules (payment channel words like UPI / NEFT)
            hit = self._first_match(texts[i], self.fallback_rules)
            if hit:
                results[i] = {"category": hit[0], "confidence": 0.5, "method": f"fallback:{hit[1]}"}
            elif txn_types[i] == "Credit":
                results[i] = {"category": "Other Income", "confidence": 0.3, "method": "default"}
            else:
                results[i] = {"category": DEFAULT_CATEGORY, "confidence": round(prob, 2), "method": "default"}

        # 5: sanity check using direction (debit / credit)
        for i, res in enumerate(results):
            if txn_types[i] == "Credit" and res["category"] in EXPENSE_ONLY:
                channel = self._first_match(texts[i], self.fallback_rules)
                res["category"] = "Transfer" if channel and channel[0] == "Transfer" else "Refund & Cashback"
                res["confidence"] = min(res["confidence"], 0.6)
                res["method"] += "+credit-adjusted"
        return results

    # ------------------------------------------------------------ feedback
    def add_corrections(self, pairs):
        """Save user corrections [(description, category), ...] and retrain (learning from feedback)."""
        pairs = [(d, c) for d, c in pairs if d and c]
        if not pairs:
            return 0
        new = pd.DataFrame(pairs, columns=["description", "category"])
        os.makedirs(os.path.dirname(os.path.abspath(self.corrections_file)), exist_ok=True)
        header = not os.path.exists(self.corrections_file)
        new.to_csv(self.corrections_file, mode="a", header=header, index=False)
        self.train()
        return len(new)

    @property
    def categories(self):
        cats = {c for c, _, _ in self.priority_rules + self.rules + self.fallback_rules}
        if self.model is not None:
            cats |= set(self.model.classes_)
        return sorted(cats | {DEFAULT_CATEGORY, "Other Income"})
