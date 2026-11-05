"use strict";

const shellLanguages = new Set(["bash", "console", "sh", "shell", "shell-session", "zsh"]);
const forbiddenCommand = /\b(?:python(?:\d+(?:\.\d+)*)?|pip\d*)\b/i;

/** @type {import("markdownlint").Rule} */
module.exports = {
  names: ["no-python-pip-commands", "texsys001"],
  description: "Shell examples must use uv instead of direct Python or pip commands",
  tags: ["texsys", "commands"],
  parser: "markdownit",
  function: (params, onError) => {
    const fences = params.parsers.markdownit.tokens.filter((token) => token.type === "fence");

    for (const fence of fences) {
      const language = fence.info.trim().split(/\s+/, 1)[0].toLowerCase();
      if (!shellLanguages.has(language)) continue;

      const contentLines = fence.content.split("\n");
      contentLines.forEach((line, index) => {
        if (line.trimStart().startsWith("#")) return;

        const match = forbiddenCommand.exec(line);
        if (!match) return;

        onError({
          lineNumber: fence.map[0] + index + 2,
          detail: `Use uv project commands instead of '${match[0]}'.`,
          context: line.trim(),
        });
      });
    }
  },
};
