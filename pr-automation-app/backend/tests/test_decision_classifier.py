from unittest.mock import MagicMock

from app.services.decision import HeuristicDecisionClassifier, LayaDecisionClassifier


def test_heuristic_decision_classifier() -> None:
    clf = HeuristicDecisionClassifier()
    opt = clf.classify_reply("Please remove me from your list.")
    assert opt.intent == "opt_out"
    assert opt.confidence >= 0.9

    pos = clf.classify_reply("Looks interesting, let's chat next week!")
    assert pos.intent == "positive_interest"

    rel = clf.score_article_relevance(
        article_title="New Robotics Breakthrough",
        article_summary="Industrial robotics startups raised $50M.",
        client_name="Acme Robotics",
        client_industry="Robotics",
    )
    assert rel.is_relevant is True


def test_laya_decision_classifier_parses_agent_output() -> None:
    clf = LayaDecisionClassifier(model_id="convaiinnovations/laya")
    mock_agent = MagicMock()
    mock_agent.predict.return_value = {
        "answers": {
            "reply_intent": {
                "type": "choice",
                "choice": "opt_out",
                "confidence": 0.8321,
                "probabilities": {"opt_out": 0.9505, "decline": 0.0372},
            }
        }
    }
    clf._agent = mock_agent

    res = clf.classify_reply("Please unsubscribe me.")
    assert res.intent == "opt_out"
    assert res.confidence == 0.8321
    assert res.probabilities["opt_out"] == 0.9505

