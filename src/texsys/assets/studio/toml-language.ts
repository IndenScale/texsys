import { languages } from "monaco-editor/editor/editor.api.js";

languages.register({ id: "toml", extensions: [".toml"], filenames: ["texsys.lock"] });

languages.setLanguageConfiguration("toml", {
  comments: { lineComment: "#" },
  brackets: [
    ["[", "]"],
    ["{", "}"],
  ],
  // Keep square-bracket matching without coloring the two levels of an array table header.
  colorizedBracketPairs: [["{", "}"]],
  autoClosingPairs: [
    { open: "[", close: "]" },
    { open: "{", close: "}" },
    { open: '"', close: '"', notIn: ["string", "comment"] },
    { open: "'", close: "'", notIn: ["string", "comment"] },
  ],
});

const key = /(?:[A-Za-z0-9_-]+|"(?:[^"\\]|\\.)*"|'[^']*')/;
const dottedKey = new RegExp(`${key.source}(?:[ \\t]*\\.[ \\t]*${key.source})*`);

languages.setMonarchTokensProvider("toml", {
  defaultToken: "",
  dottedKey,
  tokenizer: {
    root: [
      [
        /^[ \t]*(?:\[\[[ \t]*@dottedKey[ \t]*\]\]|\[[ \t]*@dottedKey[ \t]*\])(?=[ \t]*(?:#|$))/,
        "metatag",
      ],
      { include: "@value" },
    ],
    value: [
      [/#.*$/, "comment"],
      [/@dottedKey(?=[ \t]*=)/, "key"],
      [/"""/, "string", "@multilineBasic"],
      [/'''/, "string", "@multilineLiteral"],
      [/"(?:[^"\\]|\\.)*(?:"|$)|'[^']*(?:'|$)/, "string"],
      [/\b(?:true|false)\b/, "keyword"],
      [/[+-]?(?:\d[\w.:+-]*|inf\b|nan\b)/, "number"],
      [/\[/, "delimiter", "@array"],
      [/[\]{},=]/, "delimiter"],
    ],
    array: [[/\]/, "delimiter", "@pop"], { include: "@value" }],
    multilineBasic: [
      [/"{3,5}/, "string", "@pop"],
      [/\\.|[^"\\]+|["\\]/, "string"],
    ],
    multilineLiteral: [
      [/'{3,5}/, "string", "@pop"],
      [/[^']+|'/, "string"],
    ],
  },
});
