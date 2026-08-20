"""Wrapper around the pre-trained per-label SVM classifiers.

The shipped artifact (assets/ML/svm_models.pkl) is a joblib-serialized dict
of five independent one-vs-rest binary classifiers keyed by short label
names: "locked", "reset", "otp", "vpn", "irrelevant". Each value is a
CalibratedClassifierCV wrapping a LinearSVC, exposing predict_proba with
classes_ == [0, 1] (0 = label does not apply, 1 = label applies).

assets/ML/thresholds.json carries a per-label confidence threshold using
those same short keys, plus svm_confidence_threshold / gemma_confidence_threshold
used by FallbackAnalyzer to decide whether to trust the SVM outright or fall
back to the LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import joblib  # type: ignore[import-untyped]

from snoc_agent.ai.preprocessing import clean_high_confidence_artifacts, normalize_unicode

# Maps the short label keys used by the trained classifiers/thresholds to the
# full action identifiers used everywhere else in the pipeline (operations,
# FallbackAnalyzer, business API, etc).
SHORT_LABEL_TO_ACTION: dict[str, str] = {
    "locked": "account_unblock",
    "reset": "password_reset",
    "otp": "otp_number_change",
    "vpn": "vpn_access",
    "irrelevant": "irrelevant",
}


class SVMClassifier:
    """Load the per-label SVM classifiers and apply configured thresholds."""

    def __init__(
        self,
        *,
        model_path: str | Path | None = None,
        vectorizer_path: str | Path | None = None,
        thresholds_path: str | Path | None = None,
        classifiers: dict[str, Any] | None = None,
        vectorizer: Any | None = None,
        thresholds: dict[str, float] | None = None,
    ) -> None:
        repo_root = Path(__file__).resolve().parents[3]

        if classifiers is None:
            if model_path is None:
                candidate_paths = [
                    repo_root / "assets" / "ML" / "svm_model.pkl",
                    repo_root / "assets" / "ML" / "svm_models.pkl",
                ]
                for candidate in candidate_paths:
                    if candidate.exists():
                        model_path = candidate
                        break
                if model_path is None:
                    raise ValueError("model_path is required when no classifiers are provided")
            loaded = joblib.load(model_path)
            if not isinstance(loaded, dict):
                raise ValueError(
                    f"Expected a dict of per-label classifiers in {model_path}, "
                    f"got {type(loaded).__name__}"
                )
            classifiers = loaded
        self.classifiers: dict[str, Any] = classifiers

        if vectorizer is None:
            if vectorizer_path is None:
                vectorizer_path = repo_root / "assets" / "ML" / "vectorizer.pkl"
            vectorizer_path = Path(vectorizer_path)
            if not vectorizer_path.exists():
                raise ValueError(
                    f"Text vectorizer not found at {vectorizer_path}. The SVM classifiers "
                    "require raw text to be transformed by this vectorizer before scoring."
                )
            vectorizer = joblib.load(vectorizer_path)
        self.vectorizer = vectorizer

        if thresholds is None:
            if thresholds_path is None:
                thresholds_path = repo_root / "assets" / "ML" / "thresholds.json"
            thresholds_path = Path(thresholds_path)
            if thresholds_path.exists():
                thresholds = json.loads(thresholds_path.read_text(encoding="utf-8"))
            else:
                thresholds = {}
        self.thresholds = thresholds or {}

    @staticmethod
    def _preprocess_email(text: str) -> str:
        cleaned, _ = clean_high_confidence_artifacts(text)
        return normalize_unicode(cleaned).strip()

    def predict(self, email: str | dict[str, Any] | Any) -> dict[str, Any]:
        if isinstance(email, dict):
            text = "\n".join(
                str(value)
                for key, value in email.items()
                if value is not None and isinstance(value, str | int | float)
            )
        elif hasattr(email, "model_dump"):
            text = json.dumps(email.model_dump(), ensure_ascii=False)
        else:
            text = str(email)

        preprocessed = self._preprocess_email(text)
        features = self.vectorizer.transform([preprocessed])

        # Run each of the five binary "does this label apply?" classifiers
        # and collect the probability of the positive class.
        short_label_probabilities: dict[str, float] = {}
        for short_label, clf in self.classifiers.items():
            try:
                proba = clf.predict_proba(features)[0]
                classes = list(getattr(clf, "classes_", [0, 1]))
                positive_index = classes.index(1) if 1 in classes else len(proba) - 1
                short_label_probabilities[short_label] = float(proba[positive_index])
            except Exception:
                short_label_probabilities[short_label] = 0.0

        if not short_label_probabilities:
            short_label_probabilities = {"irrelevant": 1.0}

        best_short_label = max(short_label_probabilities.items(), key=lambda item: item[1])[0]
        best_confidence = short_label_probabilities[best_short_label]

        threshold = self.thresholds.get(best_short_label)
        if threshold is None:
            threshold = self.thresholds.get("default")
        if threshold is None:
            threshold = 0.5
        confident = bool(best_confidence >= float(threshold))

        best_action = SHORT_LABEL_TO_ACTION.get(best_short_label, best_short_label)
        action_probabilities = {
            SHORT_LABEL_TO_ACTION.get(label, label): proba
            for label, proba in short_label_probabilities.items()
        }

        return {
            "labels": {"action": best_action},
            "probabilities": action_probabilities,
            "confident": confident,
            "best_confidence": best_confidence,
            "threshold": float(threshold),
        }
