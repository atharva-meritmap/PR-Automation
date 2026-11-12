import os
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.config import get_settings


@dataclass(frozen=True)
class ReplyClassification:
    intent: str  # positive_interest | opt_out | auto_reply | decline
    confidence: float
    probabilities: dict[str, float]


@dataclass(frozen=True)
class RelevanceScore:
    is_relevant: bool
    confidence: float


class DecisionClassifier(Protocol):
    def classify_reply(self, body: str) -> ReplyClassification: ...
    def score_article_relevance(
        self,
        *,
        article_title: str,
        article_summary: str,
        client_name: str,
        client_industry: str,
    ) -> RelevanceScore: ...


class HeuristicDecisionClassifier:
    """Zero-dependency fallback classifier for CI and lightweight environments."""

    def classify_reply(self, body: str) -> ReplyClassification:
        text = body.lower()
        if any(k in text for k in ("unsubscribe", "remove me", "opt out", "stop emailing")):
            return ReplyClassification(intent="opt_out", confidence=0.95, probabilities={"opt_out": 0.95})
        if any(k in text for k in ("out of office", "automatic reply", "auto-reply", "ooo")):
            return ReplyClassification(intent="auto_reply", confidence=0.95, probabilities={"auto_reply": 0.95})
        if any(k in text for k in ("interested", "send more", "let's chat", "schedule", "interview")):
            return ReplyClassification(
                intent="positive_interest", confidence=0.85, probabilities={"positive_interest": 0.85}
            )
        return ReplyClassification(intent="decline", confidence=0.60, probabilities={"decline": 0.60})

    def score_article_relevance(
        self,
        *,
        article_title: str,
        article_summary: str,
        client_name: str,
        client_industry: str,
    ) -> RelevanceScore:
        haystack = f"{article_title} {article_summary}".lower()
        tokens = [t for t in client_industry.lower().split() if len(t) > 2]
        matched = any(t in haystack for t in tokens) if tokens else False
        return RelevanceScore(is_relevant=matched, confidence=0.75 if matched else 0.35)


class LayaDecisionClassifier:
    """Self-hosted System 1 decision classifier backed by convaiinnovations/laya."""

    def __init__(self, model_id: str, hf_home: str = "") -> None:
        self.model_id = model_id
        self.hf_home = hf_home
        self._agent: Any = None

    def _get_agent(self) -> Any:
        if self._agent is None:
            if self.hf_home:
                os.environ.setdefault("HF_HOME", self.hf_home)
            import laya  # type: ignore[import-untyped]

            self._agent = laya.load(self.model_id)
        return self._agent

    def classify_reply(self, body: str) -> ReplyClassification:
        agent = self._get_agent()
        res = agent.predict(
            {"body": body},
            {
                "reply_intent": {
                    "type": "choice",
                    "instructions": "Classify the journalist reply",
                    "criteria": {
                        "positive_interest": "wants interview, demo, or more info",
                        "opt_out": "asks to unsubscribe or stop emailing",
                        "auto_reply": "out of office or automated bounce",
                        "decline": "politely declines or not a fit",
                    },
                }
            },
        )
        ans = res["answers"]["reply_intent"]
        return ReplyClassification(
            intent=str(ans["choice"]),
            confidence=float(ans.get("confidence", 0.0)),
            probabilities={str(k): float(v) for k, v in ans.get("probabilities", {}).items()},
        )

    def score_article_relevance(
        self,
        *,
        article_title: str,
        article_summary: str,
        client_name: str,
        client_industry: str,
    ) -> RelevanceScore:
        agent = self._get_agent()
        res = agent.predict(
            {
                "article_title": article_title,
                "article_summary": article_summary,
                "client_name": client_name,
                "client_industry": client_industry,
            },
            {
                "is_relevant": {
                    "type": "noul",
                    "instructions": "Is this journalist article relevant to the client's industry?",
                }
            },
        )
        ans = res["answers"]["is_relevant"]
        prob = float(ans.get("probability", ans.get("confidence", 0.5)))
        return RelevanceScore(is_relevant=prob >= 0.5, confidence=prob)


_classifier_singleton: DecisionClassifier | None = None


def get_decision_classifier() -> DecisionClassifier:
    global _classifier_singleton
    if _classifier_singleton is None:
        settings = get_settings()
        if settings.DECISION_BACKEND == "laya":
            _classifier_singleton = LayaDecisionClassifier(
                model_id=settings.LAYA_MODEL_ID,
                hf_home=settings.HF_HOME,
            )
        else:
            _classifier_singleton = HeuristicDecisionClassifier()
    return _classifier_singleton

