from seats_aero_alerts import parse_alert_email

SAMPLE_SINGLE_SEGMENT = """
<p>Good news! We discovered availability for your alert &#34;NYC Paris Madrid
Barcelona Business Strict&#34; with Air France/KLM Flying Blue in
<strong>business class</strong> for JFK to MAD on 2026-11-04. This flight
should now be visible and bookable.</p>
<table><tr>
<td><p><strong>UX92</strong></p><p>Flight</p></td>
<td><p>JFK/MAD</p><p>Routing</p></td>
<td><p>Business (O9), 43,000 points + $33.50 USD</p><p>Fare</p></td>
</tr></table>
<a href="https://c.seats.aero/CL0/https:%2F%2Fseats.aero%2Fi%2F35Jvw6Ador2oYJkyWSGsjLfzhBC/1/abc/def" target="_blank">View on seats.aero</a>
"""

SAMPLE_MULTI_SEGMENT = """
<p>Good news! We discovered availability for your alert &#34;NYC Colombia
Secondary Business Strict&#34; with Air Canada Aeroplan in
<strong>business class</strong> for JFK to CTG on 2026-08-31.</p>
<table><tr>
<td><p><strong>CM815, CM304</strong></p><p>Flight</p></td>
<td><p>JFK/PTY/CTG</p><p>Routing</p></td>
<td><p>Business (I6), 40,000 points + $102.70 CAD</p><p>Fare</p></td>
</tr></table>
"""


def test_parses_single_segment_alert():
    alert = parse_alert_email(SAMPLE_SINGLE_SEGMENT)
    assert alert.alert_name == "NYC Paris Madrid Barcelona Business Strict"
    assert alert.program == "Air France/KLM Flying Blue"
    assert alert.cabin == "BUSINESS"
    assert alert.origin_iata == "JFK"
    assert alert.destination_iata == "MAD"
    assert alert.date == "2026-11-04"
    assert alert.flight_number == "UX92"
    assert alert.points == 43000
    assert alert.taxes_fees == 33.50
    assert alert.found_anything
    assert alert.listing_url == "https://c.seats.aero/CL0/https:%2F%2Fseats.aero%2Fi%2F35Jvw6Ador2oYJkyWSGsjLfzhBC/1/abc/def"


def test_parses_multi_segment_routing():
    alert = parse_alert_email(SAMPLE_MULTI_SEGMENT)
    assert alert.origin_iata == "JFK"
    assert alert.destination_iata == "CTG"
    assert alert.points == 40000
    assert alert.taxes_fees == 102.70
    assert alert.flight_number == "CM815, CM304"
    assert alert.listing_url is None  # this fixture has no "View on seats.aero" link


def test_empty_input_returns_blank_alert():
    alert = parse_alert_email("")
    assert not alert.found_anything
    assert alert.points is None
