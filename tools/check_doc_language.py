"""Check the prose language of Markdown files in a Git change."""

import argparse
from os import fsdecode
from pathlib import Path
import re
import subprocess
import sys
import tomllib

from lingua import Language, LanguageDetectorBuilder
from markdown_it import MarkdownIt


CONFIG = Path(__file__).with_name("doc-language.toml")
MARKDOWN = MarkdownIt().enable("table")
URL = re.compile(r"https?://[^\s<>]+", re.IGNORECASE)
HTML_CODE = re.compile(r"<(/?)(?:code|kbd|samp)\b[^>]*>", re.IGNORECASE)


def git(*args):
  return subprocess.run(["git", *args], check=True, stdout=subprocess.PIPE).stdout


def changed_markdown(*revisions):
  changes = git(
    "diff", "--raw", "--no-abbrev", "--no-renames", "--no-relative", "--diff-filter=AMT", "-z",
    *revisions, "--", ":(top,glob)**/*.md",
  ).split(b"\0")
  for metadata, path in zip(changes[0:-1:2], changes[1::2]):
    _, new_mode, _, blob, _ = metadata.split()
    if new_mode in (b"100644", b"100755"):
      yield fsdecode(path), blob.decode("ascii")


def expected_language(path, config, patterns):
  if path in config["overrides"]:
    return config["overrides"][path]

  locales = set()
  for pattern in patterns:
    if match := pattern.fullmatch(path):
      locales.add(match["locale"].lower().replace("_", "-"))
  if len(locales) > 1:
    raise ValueError(f"conflicting locale markers ({', '.join(sorted(locales))})")
  if not locales:
    return config["default_language"]

  locale = locales.pop()
  if locale not in config["locales"]:
    raise ValueError(f"unknown locale '{locale}'")
  return config["locales"][locale]


def prose_blocks(markdown):
  for token in MARKDOWN.parse(markdown):
    if token.type != "inline":
      continue
    parts = []
    in_html_code = False
    in_autolink = False
    for child in token.children:
      if child.markup == "autolink":
        in_autolink = child.type == "link_open"
        parts.append(" ")
      elif child.type == "html_inline" and (tag := HTML_CODE.fullmatch(child.content)):
        in_html_code = not tag[1]
        parts.append(" ")
      elif child.type == "text" and not (in_html_code or in_autolink):
        parts.append(child.content)
      elif child.type in ("softbreak", "hardbreak", "code_inline", "image"):
        parts.append(" ")
    text = URL.sub(" ", "".join(parts)).strip()
    yield token.map[0] + 1, text


def check_document(path, markdown, expected, detector, config):
  blocks = []
  for line, text in prose_blocks(markdown):
    letters = sum(map(str.isalpha, text))
    if letters >= config["minimum_letters"]:
      blocks.append((line, text, letters))
  if not blocks:
    return True

  def detect(text):
    language = detector.detect_language_of(text)
    return language.iso_code_639_1.name.lower() if language is not None else None

  dominant = detect("\n".join(text for _, text, _ in blocks))
  if dominant and dominant != expected:
    print(f"{path}:1: error: expected {expected}, but the main prose appears to be {dominant}.")
    return False

  for line, text, letters in blocks:
    if letters < config["minimum_passage_letters"]:
      continue
    language = detect(text)
    if language and language != expected:
      print(f"{path}:{line}: warning: expected {expected}, but this passage appears to be {language}.")
  return True


def main():
  parser = argparse.ArgumentParser(description=__doc__)
  scope = parser.add_mutually_exclusive_group(required=True)
  scope.add_argument("--staged", action="store_true", help="check the Git index")
  scope.add_argument("--base", help="check changes from the merge base of this revision and --head")
  parser.add_argument("--head", help="source revision to check (default: HEAD)")
  args = parser.parse_args()
  if args.staged and args.head is not None:
    parser.error("--head requires --base")

  try:
    if args.staged:
      changes = list(changed_markdown("--cached"))
    else:
      base = git("rev-parse", "--verify", "--end-of-options", f"{args.base}^{{commit}}").decode().strip()
      head = git("rev-parse", "--verify", "--end-of-options", f"{args.head or 'HEAD'}^{{commit}}").decode().strip()
      changes = list(changed_markdown(f"{base}...{head}"))
    if not changes:
      return 0

    config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    patterns = [re.compile(rule["pattern"]) for rule in config["path_rules"]]
    by_code = {language.iso_code_639_1.name.lower(): language for language in Language.all()}
    languages = [by_code[code] for code in config["detector_languages"]]
    detector = (
      LanguageDetectorBuilder.from_languages(*languages)
      .with_minimum_relative_distance(config["minimum_relative_distance"])
      .build()
    )

    failed = False
    for path, blob in changes:
      try:
        expected = expected_language(path, config, patterns)
        if expected not in config["detector_languages"]:
          raise ValueError(f"expected language '{expected}' is missing from detector_languages")
        markdown = git("cat-file", "blob", blob).decode("utf-8-sig")
      except ValueError as error:
        print(f"{path}:1: error: {error}")
        failed = True
        continue
      if not check_document(path, markdown, expected, detector, config):
        failed = True
    return int(failed)
  except (OSError, ValueError, KeyError, re.error, subprocess.CalledProcessError) as error:
    parser.exit(2, f"doc-language: {error}\n")


if __name__ == "__main__":
  sys.exit(main())
