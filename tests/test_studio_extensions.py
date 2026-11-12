import hashlib
from io import BytesIO
import json
from zipfile import ZipFile

import pytest

from texsys.project import ProjectError
from texsys.studio import extensions
from texsys.studio.runtime import Conflict


def test_vsix_update_validates_before_replacing_and_preserves_workspace_state(tmp_path, monkeypatch):
  identity = "Test.Markdown"
  manifest = {
    "publisher": "Test", "name": "Markdown", "version": "1.0.0",
    "contributes": {"snippets": [{"language": "markdown", "path": "./snippets.json"}]},
  }
  base = "https://open-vsx.org/api/Test/Markdown"

  def archive():
    output = BytesIO()
    with ZipFile(output, "w") as bundle:
      bundle.writestr("extension/package.json", json.dumps(manifest))
      bundle.writestr("extension/snippets.json", '''{
        // VS Code snippet files can contain comments and trailing commas.
        "Task": {"prefix": ["task", "todo"], "body": ["- [ ] ${1:text}", "$0"]},
      }''')
    return output.getvalue()

  manifest_reads = []

  def serve(url, maximum=extensions.MIB, *, optional=False):
    if url.endswith("package.json"):
      manifest_reads.append(manifest["version"])
      return json.dumps(manifest).encode()
    if url.endswith(".vsix"):
      return archive()
    if url.endswith(".sha256"):
      return digest
    return json.dumps({
      "namespace": "Test", "name": "Markdown", "version": manifest["version"],
      "files": {"manifest": f"{base}/package.json", "download": f"{base}/test.vsix",
                "sha256": f"{base}/test.sha256"},
    }).encode()

  monkeypatch.setattr(extensions, "_get", serve)
  extensions._version_manifest.cache_clear()
  digest = None
  store = extensions.StudioExtensions(tmp_path / "data" / "extensions.json")
  workspace = tmp_path / "project"
  result = store.change(workspace, {
    "id": identity, "action": "install", "version": "1.0.0", "autoUpdate": False,
  })
  assert result["installed"][0]["autoUpdate"] is False
  assert result["installed"][0]["snippets"][0]["body"] == "- [ ] ${1:text}\n$0"
  store.change(workspace, {"id": identity, "action": "enable", "scope": "workspace", "enabled": False})
  assert store.state(workspace)["installed"][0]["snippets"] == []
  assert store.state(tmp_path / "another")["installed"][0]["enabled"] is True

  manifest["version"] = "1.1.0"
  digest = b"0" * 64
  update = {"id": identity, "action": "install", "version": "1.1.0", "previousVersion": "1.0.0"}
  with pytest.raises(ProjectError, match="SHA-256"):
    store.change(workspace, update)
  assert store.state(workspace)["installed"][0]["version"] == "1.0.0"

  digest = hashlib.sha256(archive()).hexdigest().encode()
  result = store.change(workspace, update)
  assert result["installed"][0]["version"] == "1.1.0"
  assert result["installed"][0]["autoUpdate"] is False
  assert result["installed"][0]["enabled"] is False
  with pytest.raises(Conflict):
    store.change(workspace, update)
  assert store.change(workspace, {"id": identity, "action": "uninstall"})["installed"] == []
  assert manifest_reads == ["1.0.0", "1.1.0"]
  extensions._version_manifest.cache_clear()


def test_recommendation_edits_keep_jsonc_comments_and_other_fields(tmp_path):
  file = tmp_path / extensions.RECOMMENDATIONS
  file.parent.mkdir()
  file.write_text('''{
  "unwantedRecommendations": ["Ignore.Me"],
  "recommendations": [
    // Keep this note and the existing entry.
    "Old.Extension",
  ],
}
''', encoding="utf-8")
  extensions.recommend(tmp_path, "build", "New.Extension")
  extensions.recommend(tmp_path, "build", "NEW.EXTENSION")
  text = file.read_text(encoding="utf-8")
  assert text.count('"new.extension"') == 1
  assert '    // Keep this note and the existing entry.\n    "Old.Extension",' in text
  result = extensions.StudioExtensions(tmp_path / "unused.json").recommendations(tmp_path, "build")
  assert result["workspace"] == ["new.extension", "Old.Extension"]


def test_detail_does_not_wait_for_a_manifest_and_compatibility_reuses_metadata(monkeypatch):
  reads = []

  def get(url, maximum=extensions.MIB, *, optional=False):
    reads.append(url)
    if url.endswith("package.json"):
      raise ProjectError("清单暂不可用")
    return json.dumps({
      "namespace": "Test", "name": "Partial", "version": "1.0.0",
      "categories": ["Snippets"],
      "files": {
        "manifest": url + "/file/package.json",
        "readme": url + "/file/README.md",
        "changelog": url + "/file/CHANGELOG.md",
        "download": url + "/file/test.vsix",
      },
    }).encode()

  monkeypatch.setattr(extensions, "_get", get)
  monkeypatch.setattr(extensions, "monotonic", lambda: 123)
  try:
    result = extensions.detail("test.partial", "1.0.0")
    assert result["readme"].endswith("/README.md")
    assert result["changelog"].endswith("/CHANGELOG.md")
    assert result["categories"] == ["Snippets"]
    assert len(reads) == 1
    with pytest.raises(ProjectError, match="清单暂不可用"):
      extensions.check_compatibility("test.partial", "1.0.0")
    assert reads == [reads[0], reads[0] + "/file/package.json"]
    assert extensions.detail("test.partial", "1.0.0") == result
  finally:
    extensions._detail_metadata.cache_clear()
