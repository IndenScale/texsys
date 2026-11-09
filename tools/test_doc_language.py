import re
from pathlib import Path
import subprocess
import sys
import tempfile
import tomllib
import unittest

from check_doc_language import CONFIG, expected_language, prose_blocks


SCRIPT = CONFIG.with_name("check_doc_language.py")
CHINESE = "本项目使用本地工作台构建和验证工程模型。先按照安装说明准备开发环境，然后打开示例项目并生成系统图纸，最后检查构建结果。\n"
ENGLISH = "Install the dependencies and run the local workbench. Open the example project and generate the system diagram.\n"


class PolicyTests(unittest.TestCase):
  def test_path_rules(self):
    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    patterns = [re.compile(rule["pattern"]) for rule in config["path_rules"]]
    for path, language in {
      "README.md": "zh",
      "AGENTS.md": "en",
      "examples/README_EN.md": "en",
      "README_ZH_CN.md": "zh",
      "docs/en/subdir/setup.md": "en",
      "docs/adr/setup.md": "zh",
    }.items():
      with self.subTest(path=path):
        self.assertEqual(expected_language(path, config, patterns), language)

    for path, error in {"README_XX.md": "unknown locale", "docs/en/README_ZH.md": "conflicting locale"}.items():
      with self.subTest(path=path), self.assertRaisesRegex(ValueError, error):
        expected_language(path, config, patterns)

  def test_prose_excludes_code_urls_and_badges(self):
    markdown = """# Tex Studio

[安装说明](https://example.com/path) `long_inline_code` <code>raw_code</code> <maintainer@example.com>

https://example.com/readme [![a badge](badge.svg)](https://example.com/status)

```bash
echo "English fenced block"
```

    English indented block

| 列名 |
| --- |
| 保留表格文字 |
"""
    self.assertEqual([text for _, text in prose_blocks(markdown) if text], [
      "Tex Studio", "安装说明", "列名", "保留表格文字",
    ])


class ChangedFilesTests(unittest.TestCase):
  def setUp(self):
    directory = self.enterContext(tempfile.TemporaryDirectory())
    self.repo = Path(directory)
    self.git("init", "--quiet", "--initial-branch=main", "--template=")
    self.write("legacy.md", ENGLISH)
    self.write("old-name.md", ENGLISH)
    self.write("deleted.md", ENGLISH)
    self.commit("Initial state")

  def git(self, *args):
    return subprocess.run(
      ["git", "-c", "user.name=Doc Test", "-c", "user.email=doc-test@example.com", "-c", "commit.gpgsign=false", *args],
      cwd=self.repo, check=True, capture_output=True, text=True,
    ).stdout.strip()

  def write(self, path, content):
    target = self.repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")

  def commit(self, message):
    self.git("add", "-A")
    self.git("commit", "-qm", message)

  def lint(self, *args):
    return subprocess.run([sys.executable, str(SCRIPT), *args], cwd=self.repo, capture_output=True, text=True)

  def test_staged_checks_index_and_rename_destination(self):
    self.write("README.md", CHINESE)
    self.git("add", "README.md")
    self.git("mv", "old-name.md", "README_EN.md")
    self.git("rm", "deleted.md")
    self.write("untracked.md", ENGLISH)
    self.write("README.md", ENGLISH)

    result = self.lint("--staged")
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertEqual(result.stdout, "")

    self.git("add", "README.md")
    self.write("README.md", CHINESE)
    result = self.lint("--staged")
    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
    self.assertRegex(result.stdout, r"\AREADME\.md:1: error:[^\n]+\n\Z")

  def test_branch_range_uses_merge_base_and_source_blobs(self):
    self.git("branch", "topic")
    self.write("target-only.md", ENGLISH)
    self.commit("Target advances separately")
    self.git("switch", "topic")
    self.write("docs/en/setup.md", ENGLISH)
    self.write("user guide.md", ENGLISH)
    self.commit("Source changes")
    head = self.git("rev-parse", "HEAD")

    self.git("switch", "main")
    result = self.lint("--base", "main", "--head", head)
    self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
    self.assertRegex(result.stdout, r"\Auser guide\.md:1: error:[^\n]+\n\Z")

  def test_foreign_passage_warns_without_blocking(self):
    self.write(
      "README.md",
      f"{CHINESE}\n{CHINESE}\n{CHINESE}\n{ENGLISH}\nMicrosoft VS Code Codicons & Action Bar Alignment Standard\n",
    )
    self.git("add", "README.md")
    result = self.lint("--staged")
    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
    self.assertRegex(result.stdout, r"\AREADME\.md:7: warning:[^\n]+\n\Z")
