from app.mentions import extract_mentions


def test_extract_single_mention():
    assert extract_mentions("oi @bob, tudo bem?") == ["bob"]


def test_extract_multiple_mentions():
    assert extract_mentions("@alice e @bob, o que acham?") == ["alice", "bob"]


def test_extract_mentions_no_mentions():
    assert extract_mentions("mensagem sem menções") == []


def test_extract_mentions_deduplicates_preserving_order():
    assert extract_mentions("@bob @alice @bob") == ["bob", "alice"]


def test_extract_mentions_allows_hyphen_and_underscore():
    assert extract_mentions("@investidor-conservador e @dev_junior") == [
        "investidor-conservador",
        "dev_junior",
    ]


def test_extract_mentions_recognizes_all():
    assert extract_mentions("@all, o que vocÃªs acham?") == ["all"]
