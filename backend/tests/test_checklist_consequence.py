from app.services.checklist_consequence import parse_consequence_tags_from_markdown


def test_parse_tags_from_first_line():
    text = "[bid_unusable]\n未签字将被否决。"
    assert parse_consequence_tags_from_markdown(text) == ["bid_unusable"]


def test_parse_multiple_tags():
    text = "[score_risk, general_risk]\n扣分风险。"
    assert parse_consequence_tags_from_markdown(text) == ["score_risk", "general_risk"]


def test_parse_missing_returns_empty():
    assert parse_consequence_tags_from_markdown("无标签说明") == []
