from app.mentions import extract_mentions, resolve_mentions


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


def test_extract_mentions_recognizes_accented_start():
    assert extract_mentions("@Ícaro, sua vez") == ["ícaro"]


def test_resolve_mentions_matches_multi_word_name():
    assert resolve_mentions("@Osvaldo Tibúrcio, comece", ["Osvaldo Tibúrcio"]) == ["osvaldo tibúrcio"]


def test_resolve_mentions_is_case_insensitive():
    assert resolve_mentions("@osvaldo tibúrcio, comece", ["Osvaldo Tibúrcio"]) == ["osvaldo tibúrcio"]


def test_resolve_mentions_prefers_longer_name_at_same_position():
    assert resolve_mentions("@Osvaldo Tibúrcio, comece", ["Osvaldo", "Osvaldo Tibúrcio"]) == [
        "osvaldo tibúrcio"
    ]


def test_resolve_mentions_does_not_match_partial_word():
    assert resolve_mentions("@Osvaldoo, comece", ["Osvaldo"]) == []


def test_resolve_mentions_ignores_unknown_name():
    assert resolve_mentions("@alguem-que-nao-existe oi", ["bob"]) == []


def test_resolve_mentions_recognizes_all():
    assert resolve_mentions("@all, opinem", ["bob"]) == ["all"]


def test_resolve_mentions_deduplicates_preserving_order():
    assert resolve_mentions(
        "@Osvaldo Tibúrcio e @Osvaldo Tibúrcio de novo", ["Osvaldo Tibúrcio"]
    ) == ["osvaldo tibúrcio"]
