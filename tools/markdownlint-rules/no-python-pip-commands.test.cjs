"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const { lint: lintSync } = require("markdownlint/sync");
const markdownIt = require("markdown-it");
const rule = require("./no-python-pip-commands.cjs");

function lint(content) {
  const results = lintSync({
    strings: { "example.md": content },
    config: {
      default: false,
      "no-python-pip-commands": true,
    },
    customRules: [rule],
    markdownItFactory: () => markdownIt({ html: true }),
  });
  return results["example.md"];
}

test("accepts uv commands in shell examples", () => {
  const errors = lint(`# Example

\`\`\`bash
uv sync
uv run texsys --version
uv run pytest
uv run pre-commit run --all-files
\`\`\`
`);

  assert.deepEqual(errors, []);
});

test("rejects direct and compatibility-interface commands", () => {
  const commands = [
    "python -m texsys",
    "python3 -m pytest",
    "pip install -e .",
    "pip3 install pytest",
    "uv run python -m texsys",
    "uv pip install pytest",
  ];

  for (const command of commands) {
    const errors = lint(`# Example\n\n\`\`\`bash\n${command}\n\`\`\`\n`);
    assert.equal(errors.length, 1, command);
    assert.equal(errors[0].ruleNames[0], "no-python-pip-commands");
  }
});

test("checks all supported shell fence languages", () => {
  for (const language of ["bash", "console", "sh", "shell", "shell-session", "zsh"]) {
    const errors = lint(`# Example\n\n\`\`\`${language}\npip install texsys\n\`\`\`\n`);
    assert.equal(errors.length, 1, language);
  }
});

test("ignores prose and non-shell code examples", () => {
  const errors = lint(`# Python API

Use the Python API and the pyproject.toml package metadata.

\`\`\`toml
requires-python = ">=3.11"
\`\`\`
`);

  assert.deepEqual(errors, []);
});
