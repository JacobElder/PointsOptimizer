"""Tests for award_trips — the live re-verification every published deal rests on.

This module decides publish-vs-drop and supplies the stops, seats and current
price shown on each card, so a parsing mistake here is a wrong number in the
email rather than a crash. It had no tests at all: six separate mutations to it
(always-nonstop, always-bookable, always-9-seats, never-mixed-cabin, a network
blip becoming "gone", and dropping the 429 guard) all left the suite green.
"""

import pytest
import requests

import award_trips


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        return self._payload


class _Session:
    """Stands in for requests, returning one recorded seats.aero payload."""

    def __init__(self, payload=None, status=200, raises=None):
        self.payload, self.status, self.raises = payload, status, raises
        self.calls = 0

    def get(self, url, **kw):
        self.calls += 1
        if self.raises:
            raise self.raises
        return _Resp(self.payload, self.status)


def _segment(flight, origin, dest, cabin="business"):
    return {"FlightNumber": flight, "OriginAirport": origin,
            "DestinationAirport": dest, "Cabin": cabin}


def _payload(segments, cost=88000, seats=2, cabin="business", **trip):
    return {"data": [{"Cabin": cabin, "MileageCost": cost, "TotalDuration": 600,
                      "RemainingSeats": seats, "Carriers": "LO, OS",
                      "AvailabilitySegments": segments, "Connections": ["KRK"],
                      "DepartsAt": "2026-10-23T17:15:00Z",
                      "ArrivesAt": "2026-10-24T09:05:00Z", **trip}],
            "carriers": {"LO": "LOT Polish", "OS": "Austrian Airlines"},
            "booking_links": [{"link": "https://example.com", "label": "Book", "primary": True}]}


@pytest.fixture(autouse=True)
def _api_key(monkeypatch):
    monkeypatch.setenv("SEATS_AERO_API_KEY", "test-key")


def test_a_connecting_itinerary_is_never_reported_as_nonstop():
    """seats.aero's own `direct` flag means "one flight number", not nonstop, which
    is what inflated CPP before: the stop count has to come from the segments."""
    trip = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(_payload(
        [_segment("LO2084", "EWR", "KRK"), _segment("OS680", "KRK", "VIE")])))
    assert trip.nonstop is False and trip.stops == 1
    assert trip.flights == ["LO2084 EWR–KRK", "OS680 KRK–VIE"]
    assert trip.carriers == "LOT Polish, Austrian Airlines"

    one_leg = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(
        _payload([_segment("TK30", "EWR", "IST")])))
    assert one_leg.nonstop is True and one_leg.stops == 0


def test_an_award_that_has_repriced_upward_is_not_reported_as_bookable():
    """price_matches is what still_bookable uses to drop a deal; if it were always
    True the digest would publish awards at a price they can no longer be had for."""
    gone_up = award_trips.fetch("id", "BUSINESS", 88000,
                                session=_Session(_payload([_segment("LX17", "JFK", "ZRH")],
                                                          cost=110000)))
    assert gone_up.price_matches is False and gone_up.current_points == 110000

    cheaper = award_trips.fetch("id", "BUSINESS", 88000,
                                session=_Session(_payload([_segment("LX17", "JFK", "ZRH")],
                                                          cost=70000)))
    assert cheaper.price_matches is True  # an improvement is still bookable


def test_seat_count_is_read_from_the_payload():
    """"1 left when we checked" drives the urgency line on the card."""
    assert award_trips.fetch("id", "BUSINESS", 88000, session=_Session(
        _payload([_segment("LX17", "JFK", "ZRH")], seats=1))).seats == 1
    assert award_trips.fetch("id", "BUSINESS", 88000, session=_Session(
        _payload([_segment("LX17", "JFK", "ZRH")], seats=0))).seats == 0


def test_an_economy_leg_inside_a_business_award_is_flagged():
    """A "business" award can carry an economy leg; the ranking discounts it 60%."""
    trip = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(_payload(
        [_segment("LO2084", "EWR", "KRK", cabin="economy"),
         _segment("OS680", "KRK", "VIE", cabin="business")])))
    assert trip.mixed_cabin is True
    assert trip.lower_cabin_legs == ["LO2084 EWR–KRK (economy)"]

    clean = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(_payload(
        [_segment("LO2084", "EWR", "KRK"), _segment("OS680", "KRK", "VIE")])))
    assert clean.mixed_cabin is False and clean.lower_cabin_legs == []


def test_an_airport_change_mid_trip_is_flagged():
    trip = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(_payload(
        [_segment("AA1", "JFK", "DCA"), _segment("AA2", "IAD", "LHR")])))
    assert trip.airport_changes == ["DCA → IAD"]


def test_a_network_failure_is_not_mistaken_for_a_gone_award():
    """Returning None would mean "no longer offered", which drops the deal. A blip
    must raise instead, so the card is published flagged rather than deleted."""
    with pytest.raises(award_trips.LookupFailed):
        award_trips.fetch("id", "BUSINESS", 88000,
                          session=_Session(raises=requests.ConnectionError("boom")))
    with pytest.raises(award_trips.LookupFailed):
        award_trips.fetch("id", "BUSINESS", 88000, session=_Session({}, status=500))


def test_a_429_stops_the_run_rather_than_burning_the_rest_of_the_quota():
    """Every later call would fail too, against a 1,000/day allowance shared with
    the owner's own searches."""
    with pytest.raises(award_trips.QuotaExhausted):
        award_trips.fetch("id", "BUSINESS", 88000, session=_Session({}, status=429))


def test_an_award_no_longer_offered_in_this_cabin_returns_none():
    """Distinct from a failure: None means seats.aero answered and it's gone."""
    assert award_trips.fetch("id", "FIRST", 88000, session=_Session(
        _payload([_segment("LX17", "JFK", "ZRH")], cabin="business"))) is None


def test_the_cheapest_itinerary_at_or_below_the_scanned_price_is_chosen():
    payload = _payload([_segment("SLOW1", "JFK", "ZRH")], cost=88000)
    payload["data"].append({**payload["data"][0], "MileageCost": 70000,
                            "TotalDuration": 900,
                            "AvailabilitySegments": [_segment("CHEAP1", "JFK", "ZRH")]})
    trip = award_trips.fetch("id", "BUSINESS", 88000, session=_Session(payload))
    assert trip.flights == ["CHEAP1 JFK–ZRH"] and trip.current_points == 70000
    assert trip.other_itineraries == 1
