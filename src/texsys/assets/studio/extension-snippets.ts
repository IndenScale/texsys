import { useEffect } from "react";
import { languages } from "monaco-editor/editor/editor.api.js";

export type ExtensionSnippet = {
  name: string;
  prefixes: string[];
  body: string;
  description: string;
  languages: string[];
};

const languageAliases: Record<string, string> = {
  javascriptreact: "javascript",
  typescriptreact: "typescript",
  shellscript: "shell",
};

export function useExtensionSnippets(extensions: { name: string; snippets: ExtensionSnippet[] }[]) {
  useEffect(() => {
    const entries = extensions.flatMap((extension) =>
      extension.snippets.map((snippet) => ({ ...snippet, extension: extension.name })),
    );
    if (!entries.length) return;
    const provider = languages.registerCompletionItemProvider("*", {
      provideCompletionItems(model, position) {
        const word = model.getWordUntilPosition(position);
        const language = model.getLanguageId();
        const line = model.getLineContent(position.lineNumber);
        return {
          suggestions: entries
            .filter(
              (entry) =>
                !entry.languages.length ||
                entry.languages.some(
                  (scope) =>
                    scope === language ||
                    (languageAliases[scope] === language &&
                      (!scope.endsWith("react") || model.uri.path.endsWith("x"))),
                ),
            )
            .flatMap((entry) =>
              entry.prefixes.map((prefix) => ({
                label: prefix,
                detail: `${entry.name} · ${entry.extension}`,
                documentation: entry.description,
                kind: languages.CompletionItemKind.Snippet,
                insertText: entry.body,
                insertTextRules: languages.CompletionItemInsertTextRule.InsertAsSnippet,
                range: {
                  startLineNumber: position.lineNumber,
                  endLineNumber: position.lineNumber,
                  startColumn:
                    prefix.startsWith(line.slice(word.startColumn - 2, position.column - 1)) &&
                    word.startColumn > 1
                      ? word.startColumn - 1
                      : word.startColumn,
                  endColumn: word.endColumn,
                },
              })),
            ),
        };
      },
    });
    return () => provider.dispose();
  }, [extensions]);
}
