import airports


def test_resolve_bare_code_passthrough():
    assert airports.resolve("puj") == ("PUJ", "PUJ")


def test_resolve_known_alias_case_insensitive():
    assert airports.resolve("Punta Cana") == ("PUJ", "Punta Cana (PUJ)")
    assert airports.resolve("aruba") == ("AUA", "Aruba (AUA)")


def test_resolve_unknown_city_returns_none():
    assert airports.resolve("Nowhereville") == (None, None)


def test_stockholm_resolves_to_primary_airport_not_metro_code():
    # Previously mapped to the untested metro code "STO"; switched to the real
    # single airport (Arlanda) since STO was never verified against SerpApi.
    assert airports.resolve("stockholm") == ("ARN", "Stockholm (ARN)")


def test_resolve_input_from_canonical_dropdown_option():
    assert airports.resolve_input("New York (NYC)") == ("NYC", "New York (NYC)")


def test_resolve_input_from_free_typed_code():
    assert airports.resolve_input("puj") == ("PUJ", "PUJ")


def test_resolve_input_from_free_typed_known_alias():
    code, _ = airports.resolve_input("Aruba")
    assert code == "AUA"


def test_resolve_input_unrecognized_text_is_none():
    assert airports.resolve_input("not a real place") == (None, None)


def test_resolve_input_empty_is_none():
    assert airports.resolve_input(None) == (None, None)
    assert airports.resolve_input("") == (None, None)


def test_options_are_deduped_and_sorted():
    opts = airports.options()
    assert opts == sorted(opts)
    assert len(opts) == len(set(opts))


def test_code_from_option_round_trips_with_option_from_code():
    for opt in airports.options()[:20]:
        code = airports.code_from_option(opt)
        assert airports.option_from_code(code) == opt
