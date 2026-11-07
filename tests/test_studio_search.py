"""Code Search: line grouping, editor options, bounded regex and reviewed replacements."""
import time

import pytest

from texsys.project import ProjectError
from texsys.studio.files import search_project
from texsys.studio.runtime import Conflict
from texsys.studio.search import replacement


def test_multiple_occurrences_share_one_result_line_and_keep_columns(tmp_path):
  (tmp_path / "README.md").write_text("😀 test tests/test_code test\nother\n")
  result = search_project(tmp_path, "build", "test")
  assert len(result["matches"]) == 4
  assert len(result["lines"]) == 1
  line = result["lines"][0]
  assert line["count"] == 4
  assert [match["column"] for match in line["matches"]] == [3, 8, 14, 24]
  assert line["text"] == "😀 test tests/test_code test"


def test_whole_word_case_and_glob_filters(tmp_path):
  (tmp_path / "src").mkdir()
  (tmp_path / "src/model.txt").write_text("test Test tests pytest test_code")
  (tmp_path / "src/skip.json").write_text("test")
  (tmp_path / "other.md").write_text("test")
  result = search_project(tmp_path, "build", "test", whole_word=True, case_sensitive=True,
                          include="src/*, *.md", exclude="*.json, other.md")
  assert len(result["matches"]) == 1 and result["matches"][0]["path"] == "src/model.txt"
  assert len(search_project(tmp_path, "build", "test", whole_word=True, include="*.txt")["matches"]) == 2


def test_regex_search_and_invalid_expression_are_real(tmp_path):
  (tmp_path / "model").write_text("P-01 P-02 P-X\n")
  result = search_project(tmp_path, "build", r"P-\d{2}", regex=True)
  assert len(result["matches"]) == 2 and len(result["lines"]) == 1
  with pytest.raises(ProjectError, match="正则表达式无效"):
    search_project(tmp_path, "build", "[", regex=True)


def test_catastrophic_regex_cannot_block_api_process(tmp_path, monkeypatch):
  import texsys.studio.search as search
  (tmp_path / "slow").write_text("a" * 30000 + "!")
  monkeypatch.setattr(search, "REGEX_TIMEOUT", 0.25)
  started = time.monotonic()
  with pytest.raises(ProjectError, match="正则搜索超过"):
    search_project(tmp_path, "build", "(a+)+$", regex=True)
  assert time.monotonic() - started < 2
  assert len(search_project(tmp_path, "build", "!")["matches"]) == 1


def test_zero_width_matches_do_not_generate_unselectable_rows(tmp_path):
  (tmp_path / "model").write_text("abc\n")
  result = search_project(tmp_path, "build", "^|$", regex=True)
  assert result["matches"] == [] and result["lines"] == []


def test_replacement_requires_preview_snapshot_and_does_not_write_during_preview(tmp_path):
  file = tmp_path / "model"
  file.write_bytes(b"\xef\xbb\xbftest test\r\n")
  request = {"query": "test", "replacement": "pump"}
  plan = replacement(tmp_path, "build", request)
  assert plan["count"] == 2 and len(plan["changes"]) == 1
  assert "content" not in plan["changes"][0]
  assert file.read_bytes() == b"\xef\xbb\xbftest test\r\n"
  with pytest.raises(Conflict):
    replacement(tmp_path, "build", {**request, "apply": True})
  result = replacement(tmp_path, "build", {**request, "snapshot": plan["snapshot"], "apply": True})
  assert result["complete"] and result["applied"] == ["model"]
  assert file.read_bytes() == b"\xef\xbb\xbfpump pump\r\n"


def test_external_change_invalidates_entire_replace_batch_before_any_write(tmp_path):
  for name in ("a", "b"):
    (tmp_path / name).write_text("test")
  request = {"query": "test", "replacement": "changed"}
  plan = replacement(tmp_path, "build", request)
  (tmp_path / "b").write_text("externally changed test")
  with pytest.raises(Conflict):
    replacement(tmp_path, "build", {**request, "apply": True, "snapshot": plan["snapshot"]})
  assert (tmp_path / "a").read_text() == "test"
  assert (tmp_path / "b").read_text() == "externally changed test"


def test_new_matches_in_new_file_require_a_fresh_preview(tmp_path):
  (tmp_path / "a").write_text("test")
  request = {"query": "test", "replacement": "new"}
  plan = replacement(tmp_path, "build", request)
  (tmp_path / "b").write_text("test")
  with pytest.raises(Conflict):
    replacement(tmp_path, "build", {**request, "apply": True, "snapshot": plan["snapshot"]})
  assert (tmp_path / "a").read_text() == "test"


def test_regex_captures_and_preserve_case_in_replacement(tmp_path):
  (tmp_path / "model").write_text("P-01 P-02 TEST Test test")
  request = {"query": r"P-(\d+)", "replacement": "Pump-$1", "regex": True}
  plan = replacement(tmp_path, "build", request)
  replacement(tmp_path, "build", {**request, "snapshot": plan["snapshot"], "apply": True})
  assert (tmp_path / "model").read_text().startswith("Pump-01 Pump-02")
  request = {"query": "test", "replacement": "valve", "preserve_case": True}
  plan = replacement(tmp_path, "build", request)
  replacement(tmp_path, "build", {**request, "snapshot": plan["snapshot"], "apply": True})
  assert (tmp_path / "model").read_text().endswith("VALVE Valve valve")


def test_empty_replacement_and_file_scope_are_supported(tmp_path):
  (tmp_path / "a").write_text("test:test")
  (tmp_path / "b").write_text("test")
  request = {"query": "test", "replacement": "", "paths": ["a"]}
  plan = replacement(tmp_path, "build", request)
  replacement(tmp_path, "build", {**request, "snapshot": plan["snapshot"], "apply": True})
  assert (tmp_path / "a").read_text() == ":"
  assert (tmp_path / "b").read_text() == "test"


def test_truncated_results_cannot_silently_replace_subset(tmp_path, monkeypatch):
  import texsys.studio.files as files
  monkeypatch.setattr(files, "MAX_SEARCH_MATCHES", 2)
  (tmp_path / "model").write_text("test test test")
  with pytest.raises(ProjectError, match="超过上限"):
    replacement(tmp_path, "build", {"query": "test", "replacement": "x"})
  assert (tmp_path / "model").read_text() == "test test test"


def test_noop_replacement_does_not_create_changes(tmp_path):
  (tmp_path / "model").write_text("test")
  plan = replacement(tmp_path, "build", {"query": "test", "replacement": "test"})
  assert plan["count"] == 0 and not plan["changes"]


def test_unexpected_write_failure_reports_partial_application(tmp_path, monkeypatch):
  import texsys.studio.files as files
  for name in ("a", "b"):
    (tmp_path / name).write_text("test")
  request = {"query": "test", "replacement": "new"}
  plan = replacement(tmp_path, "build", request)
  save = files.file_operation
  def failing_save(root, build, operation):
    if operation["path"] == "b":
      raise OSError("disk failure")
    return save(root, build, operation)
  monkeypatch.setattr(files, "file_operation", failing_save)
  result = replacement(tmp_path, "build", {**request, "snapshot": plan["snapshot"], "apply": True})
  assert not result["complete"] and result["applied"] == ["a"]
  assert "disk failure" in result["error"]
  assert (tmp_path / "b").read_text() == "test"
