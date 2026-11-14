import pytest

from texsys_spatial import Face, Space


def test_space_requires_positive_finite_extents():
  space = Space((0, 0, 0), (4, 3, 3))
  assert space.size_m == (4, 3, 3)
  assert space.center_m == (2, 1.5, 1.5)
  assert space.volume_m3 == pytest.approx(36)
  with pytest.raises(ValueError, match="positive finite extents"):
    Space((0, 0, 0), (4, 3, 0))
  with pytest.raises(ValueError, match="positive finite extents"):
    Space((0, 0, 0), (4, float("inf"), 3))


def test_face_requires_a_normal_axis_and_positive_extents():
  face = Face("East", 0, 4, ((0, 3), (0, 3)))
  assert face.contains((4, 1.5, 1.5))
  assert not face.contains((4, 4, 1.5))
  assert not face.contains((0, 1.5, 1.5))
  with pytest.raises(ValueError, match="normal axis"):
    Face("East", 3, 4, ((0, 3), (0, 3)))
  with pytest.raises(ValueError, match="positive extents"):
    Face("East", 0, 4, ((0, 3), (3, 3)))


def test_containment_distinguishes_inside_surface_and_outside():
  room = Space((0, 0, 0), (4, 3, 3))
  assert room.contains_point((2, 1.5, 1.5))
  assert room.contains_point((0, 0, 0))
  assert not room.contains_point((4.1, 1.5, 1.5))
  assert not room.contains_point((2, 1.5, 1.5), inset_m=2)
  assert room.locate((2, 1.5, 1.5)) == "inside"
  assert room.locate((4, 1.5, 1.5)) == "surface"
  assert room.locate((5, 1.5, 1.5)) == "outside"
  with pytest.raises(ValueError, match="three finite coordinates"):
    room.contains_point((2, 1.5))
  with pytest.raises(ValueError, match="non-negative"):
    room.contains_point((2, 1.5, 1.5), inset_m=-1)


def test_boundary_faces_name_the_face_edge_and_corner():
  room = Space((0, 0, 0), (4, 3, 3))
  assert room.boundary_faces((4, 1.5, 1.5)) == ("East",)
  assert room.boundary_faces((2, 1.5, 3)) == ("Top",)
  assert set(room.boundary_faces((4, 3, 1.5))) == {"East", "North"}
  assert set(room.boundary_faces((4, 3, 3))) == {"East", "North", "Top"}
  assert room.boundary_faces((2, 1.5, 1.5)) == ()
  assert set(room.faces()) == {"West", "East", "South", "North", "Bottom", "Top"}


def test_intersection_and_containment_of_spaces():
  a = Space((0, 0, 0), (4, 3, 3))
  b = Space((2, 1, 1), (6, 4, 4))
  assert a.intersects(b)
  assert a.intersection(b) == Space((2, 1, 1), (4, 3, 3))
  assert a.contains_space(Space((1, 1, 1), (2, 2, 2)))
  assert not a.contains_space(b)
  touching = Space((4, 0, 0), (8, 3, 3))
  assert not a.intersects(touching)
  assert a.intersection(touching) is None
  assert a.intersection(Space((10, 10, 10), (11, 11, 11))) is None


def test_shared_faces_identify_the_separating_plane():
  hall = Space((0, 0, 0), (4, 3, 3))
  services = Space((4, 0, 0), (8, 3, 3))
  shared = hall.shared_faces(services)
  assert len(shared) == 1
  face = shared[0]
  assert face.name == "East"
  assert face.axis == 0
  assert face.station_m == pytest.approx(4)
  assert face.extent_m == ((0, 3), (0, 3))
  assert face.contains((4, 1.5, 1.5))
  assert hall.shared_faces(Space((10, 0, 0), (12, 3, 3))) == ()
  stacked = Space((0, 0, 3), (4, 3, 6))
  assert hall.shared_faces(stacked)[0].name == "Top"
