import pytest

from texsys_spatial import Space
from texsys_fire_protection import Barrier, FireCompartment, check_separation, compartment_of


def _compartments():
  return {
    "L01/Hall": FireCompartment("L01/Hall", Space((0, 0, 0), (4, 3, 3)), "Hall"),
    "L01/Services": FireCompartment("L01/Services", Space((4, 0, 0), (8, 3, 3)), "Services"),
  }


def _barriers():
  return {"L01/Hall-Services": Barrier(0, 4, ((0, 3), (0, 3)), "concrete", 200, {"wall": 2.0})}


def test_fire_compartment_needs_an_identity_and_a_space():
  compartment = FireCompartment("L01/Hall", Space((0, 0, 0), (4, 3, 3)), "Hall")
  assert compartment.contains_point((2, 1.5, 1.5))
  assert not compartment.contains_point((6, 1.5, 1.5))
  with pytest.raises(ValueError, match="non-empty identity"):
    FireCompartment(" ", Space((0, 0, 0), (1, 1, 1)))
  with pytest.raises(ValueError, match="spatial envelope"):
    FireCompartment("L01/Hall", (0, 0, 0))


def test_compartment_of_resolves_the_innermost_zone():
  compartments = _compartments()
  assert compartment_of((2, 1.5, 1.5), compartments) == "L01/Hall"
  assert compartment_of((6, 1.5, 1.5), compartments) == "L01/Services"
  assert compartment_of((20, 1.5, 1.5), compartments) is None
  nested = dict(compartments, **{"L01/Hall/Inner": FireCompartment(
    "L01/Hall/Inner", Space((1, 1, 1), (2, 2, 2)), "Hall")})
  assert compartment_of((1.5, 1.5, 1.5), nested) == "L01/Hall/Inner"


def test_separation_passes_for_different_compartments_with_a_rated_barrier():
  result = check_separation(_compartments(), _barriers(), (2, 1.5, 1.5), (6, 1.5, 1.5),
                            {"wall": 2.0}, {"L01/Hall-Services": {"wall": 2.0}})
  assert result["status"] == "pass"
  assert result["compartment_a"] == "L01/Hall"
  assert result["compartment_b"] == "L01/Services"
  assert result["shared_face"]["name"] == "East"
  assert result["barrier"] == "L01/Hall-Services"
  assert result["failed_checks"] == []
  assert all(result["checks"].values())


def test_separation_fails_when_both_locations_share_a_compartment():
  result = check_separation(_compartments(), _barriers(), (1, 1.5, 1.5), (2, 1.5, 1.5),
                            {"wall": 2.0}, {"L01/Hall-Services": {"wall": 2.0}})
  assert result["status"] == "fail"
  assert result["reason"] == "same-compartment"
  assert result["failed_checks"] == ["different-compartment"]


def test_separation_fails_on_inadequate_rating_or_no_barrier():
  inadequate = check_separation(_compartments(), _barriers(), (2, 1.5, 1.5), (6, 1.5, 1.5),
                                {"wall": 2.0}, {"L01/Hall-Services": {"wall": 1.0}})
  assert inadequate["status"] == "fail"
  assert inadequate["reason"] == "inadequate-rating"
  assert inadequate["failed_checks"] == ["rating"]
  unprotected = check_separation(_compartments(), {}, (2, 1.5, 1.5), (6, 1.5, 1.5),
                                 {"wall": 2.0}, {})
  assert unprotected["status"] == "fail"
  assert unprotected["reason"] == "unprotected-separation"
  assert unprotected["failed_checks"] == ["barrier-declared"]


def test_separation_is_undecidable_without_evidence_or_adjacency():
  missing = check_separation(_compartments(), _barriers(), (20, 1.5, 1.5), (6, 1.5, 1.5),
                             {"wall": 2.0}, {"L01/Hall-Services": {"wall": 2.0}})
  assert missing["status"] == "undecidable"
  assert missing["reason"] == "missing-compartment"
  no_rating = check_separation(_compartments(), _barriers(), (2, 1.5, 1.5), (6, 1.5, 1.5),
                               {"wall": 2.0}, {})
  assert no_rating["status"] == "undecidable"
  assert no_rating["reason"] == "missing-rating-evidence"
  far = dict(_compartments(), **{"L01/Far": FireCompartment("L01/Far", Space((20, 0, 0), (24, 3, 3)))})
  non_adjacent = check_separation(far, _barriers(), (2, 1.5, 1.5), (22, 1.5, 1.5),
                                  {"wall": 2.0}, {"L01/Hall-Services": {"wall": 2.0}})
  assert non_adjacent["status"] == "undecidable"
  assert non_adjacent["reason"] == "non-adjacent"


def test_separation_validates_ratings_and_identities():
  with pytest.raises(ValueError, match="required fire ratings"):
    check_separation(_compartments(), _barriers(), (2, 1.5, 1.5), (6, 1.5, 1.5), {}, {})
  with pytest.raises(ValueError, match="rating criteria"):
    check_separation(_compartments(), _barriers(), (2, 1.5, 1.5), (6, 1.5, 1.5),
                     {"wall": 2.0}, {"L01/Hall-Services": {"wall": -1}})
  with pytest.raises(ValueError, match="non-empty"):
    check_separation({"": FireCompartment("x", Space((0, 0, 0), (1, 1, 1)))}, {},
                     (0.5, 0.5, 0.5), (0.5, 0.5, 0.5), {"wall": 2.0}, {})
