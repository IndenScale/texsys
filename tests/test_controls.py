from texsys_controls.changeover import ConfirmedChangeover, Observation


def test_changeover_requires_fresh_proof_and_revokes_on_loss():
  control = ConfirmedChangeover("leak", {"supply": "supply-closed", "return": "return-closed"},
                                "backup", "flow", 12, 9)
  control.receive(2, Observation("leak", True, 2))
  control.receive(3, Observation("supply-closed", True, 1))
  control.receive(4, Observation("supply-closed", True, 4))
  control.receive(5, Observation("supply-closed", False, 5))
  control.receive(6, Observation("supply-closed", True, 4))
  control.receive(6.5, Observation("return-closed", True, 6.5))
  assert control.state == "isolating"
  control.receive(7, Observation("supply-closed", True, 7))
  assert control.state == "starting"
  control.receive(8, Observation("flow", True, 7))
  assert control.state == "starting"
  control.receive(9, Observation("flow", True, 9))
  assert control.state == "proved"
  control.receive(10, Observation("flow", False, 10))
  assert control.state == "lockout"
  assert control.trace[-2] == {"at_s": 10, "kind": "command", "target": "backup", "action": "disable"}
  assert control.trace[-1]["reason"] == "backup-proof-lost"


def test_timeout_has_priority_over_feedback_at_the_deadline():
  control = ConfirmedChangeover("leak", {"valve": "closed"}, "backup", "flow", 4, 4)
  control.receive(2, Observation("leak", True, 2))
  control.receive(6, Observation("closed", True, 6))
  assert control.state == "lockout"
  assert not any(item.get("action") == "enable" for item in control.trace)


def test_newer_valve_sample_can_revoke_backup_even_if_it_arrives_after_enable():
  control = ConfirmedChangeover("leak", {"supply": "supply-closed", "return": "return-closed"},
                                "backup", "flow", 12, 9)
  control.receive(2, Observation("leak", True, 2))
  control.receive(4, Observation("supply-closed", True, 4))
  control.receive(5, Observation("return-closed", True, 5))
  assert control.state == "starting"
  control.receive(5.5, Observation("supply-closed", False, 4.5))
  assert control.state == "lockout"
  assert control.trace[-2] == {"at_s": 5.5, "kind": "command", "target": "backup", "action": "disable"}
  assert control.trace[-1]["reason"] == "isolation-proof-lost"
