from app.services.cloud_scope import rule_matches_connection, rule_matches_destination


def test_destination_wide_rule_applies_to_every_connection():
    assert rule_matches_connection(None, 11)


def test_connection_rule_does_not_leak_to_another_connection():
    assert rule_matches_connection(11, 11)
    assert not rule_matches_connection(11, 12)


def test_legacy_rule_without_destination_only_applies_to_focus():
    assert rule_matches_destination(None, 1, is_focus_destination=True)
    assert not rule_matches_destination(None, 34, is_focus_destination=False)


def test_destination_rule_does_not_leak_to_focus_or_another_destination():
    assert rule_matches_destination(1, 1, is_focus_destination=True)
    assert not rule_matches_destination(34, 1, is_focus_destination=True)
    assert not rule_matches_destination(1, 34, is_focus_destination=False)
