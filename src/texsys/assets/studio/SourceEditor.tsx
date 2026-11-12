import React, { useLayoutEffect, useRef } from "react";
import { editor, MarkerSeverity } from "monaco-editor/editor/editor.api.js";
import EditorWorker from "monaco-editor/editor/editor.worker.js?worker";
import JsonWorker from "monaco-editor/language/json/json.worker.js?worker";
import "monaco-editor/features/codicon/register.js";
import "monaco-editor/editor/browser/coreCommands.js";
import "monaco-editor/editor/contrib/bracketMatching/browser/bracketMatching.js";
import "monaco-editor/editor/contrib/comment/browser/comment.js";
import "monaco-editor/editor/contrib/indentation/browser/indentation.js";
import "monaco-editor/editor/contrib/multicursor/browser/multicursor.js";
import "monaco-editor/editor/contrib/contextmenu/browser/contextmenu.js";
import "monaco-editor/editor/contrib/find/browser/findController.js";
import "monaco-editor/editor/contrib/folding/browser/folding.js";
import "monaco-editor/editor/contrib/suggest/browser/suggestController.js";
import "monaco-editor/features/snippet/register.js";
import "monaco-editor/editor/contrib/toggleTabFocusMode/browser/toggleTabFocusMode.js";
import "monaco-editor/editor/contrib/tokenization/browser/tokenization.js";
import "monaco-editor/language/json/monaco.contribution.js";
import "monaco-editor/languages/definitions/ini/register.js";
import "monaco-editor/languages/definitions/markdown/register.js";
import "monaco-editor/languages/definitions/javascript/register.js";
import "monaco-editor/languages/definitions/typescript/register.js";
import "monaco-editor/languages/definitions/python/register.js";
import "monaco-editor/languages/definitions/shell/register.js";
import "monaco-editor/languages/definitions/yaml/register.js";
import { t } from "./i18n";
import type { SourceEditorStatus, SourceStatusAction } from "./editor-groups";
import type { SourceEditCommand } from "./menus";
import "./toml-language";
import { useUserPreferences } from "./user-preferences";
import { sourceFileFormat, sourceOffset, type SearchMatch } from "./workspace-model";

globalThis.MonacoEnvironment = {
  getWorker: (_id, label) => (label === "json" ? new JsonWorker() : new EditorWorker()),
};

const languageByExtension: Record<string, string> = {
  js: "javascript",
  jsx: "javascript",
  mjs: "javascript",
  cjs: "javascript",
  ts: "typescript",
  tsx: "typescript",
  json: "json",
  jsonc: "json",
  md: "markdown",
  markdown: "markdown",
  ini: "ini",
  toml: "toml",
  py: "python",
  sh: "shell",
  yaml: "yaml",
  yml: "yaml",
};

const markdownPunctuation = Object.fromEntries(
  [..."，。！？；：、（）【】《》〈〉「」『』“”‘’…—"].map(
    (character) => [character, true] as const,
  ),
);

type ModelState = {
  model: editor.ITextModel;
  viewStates: Map<string, editor.ICodeEditorViewState | null>;
  languageOverride?: string;
};
export type SourceModels = Map<string, ModelState>;
const editorViews = new WeakMap<editor.ICodeEditor, string>();
const sourceReports = new WeakMap<editor.ICodeEditor, () => void>();

export type SourceProblem = {
  path: string;
  line: number;
  column: number;
  message: string;
  severity: "error" | "warning";
};

export function watchSourceProblems(
  models: SourceModels,
  report: (items: SourceProblem[]) => void,
) {
  const update = () =>
    report(
      [...models].flatMap(([path, { model }]) =>
        editor.getModelMarkers({ resource: model.uri }).flatMap((item): SourceProblem[] =>
          item.severity === MarkerSeverity.Error || item.severity === MarkerSeverity.Warning
            ? [
                {
                  path,
                  line: item.startLineNumber,
                  column: item.startColumn,
                  message: item.message,
                  severity: item.severity === MarkerSeverity.Error ? "error" : "warning",
                },
              ]
            : [],
        ),
      ),
    );
  const listener = editor.onDidChangeMarkers(update);
  update();
  return () => listener.dispose();
}

export function runSourceStatusAction(
  path: string,
  models: SourceModels,
  viewId: string,
  action: Exclude<SourceStatusAction, { kind: "format" }>,
) {
  const target = visibleSourceEditor(path, models, viewId);
  if (!target) return;
  const { code, model } = target;
  if (action.kind === "language") {
    const state = models.get(path)!;
    state.languageOverride = action.language === "auto" ? undefined : action.language;
    editor.setModelLanguage(model, state.languageOverride ?? detectedSourceLanguage(path));
    for (const view of editor.getEditors())
      if (view.getModel() === model) sourceReports.get(view)?.();
    return;
  }
  if (action.kind === "indent") {
    model.updateOptions({
      ...(action.insertSpaces !== undefined && { insertSpaces: action.insertSpaces }),
      ...(action.tabSize !== undefined && { tabSize: action.tabSize, indentSize: action.tabSize }),
    });
    return;
  }
  void code
    .getAction(
      action.insertSpaces ? "editor.action.indentationToSpaces" : "editor.action.indentationToTabs",
    )
    ?.run();
}

function detectedSourceLanguage(path: string) {
  return languageByExtension[sourceFileFormat(path)] ?? "plaintext";
}

export function selectedSourceText(path: string, models: SourceModels, viewId?: string) {
  const target = visibleSourceEditor(path, models, viewId);
  const selection = target?.code.getSelection();
  return selection ? target!.model.getValueInRange(selection) : "";
}

export function supportsSourceLineComments(path: string) {
  return ["ini", "py", "sh", "toml", "yaml", "yml"].includes(sourceFileFormat(path));
}

function visibleSourceEditor(path: string, models: SourceModels, viewId?: string) {
  const model = models.get(path)?.model;
  if (!model || model.isDisposed()) return;
  const candidates = editor
    .getEditors()
    .filter(
      (candidate) =>
        candidate.getModel() === model &&
        (viewId === undefined || editorViews.get(candidate) === viewId) &&
        candidate.getDomNode()?.getClientRects().length,
    );
  const code = candidates.find((candidate) => candidate.hasWidgetFocus()) ?? candidates[0];
  return code ? { model, code } : undefined;
}

export function goToSourcePosition(
  path: string,
  models: SourceModels,
  input: string,
  viewId?: string,
) {
  const match = /^([1-9]\d*)(?::([1-9]\d*))?$/.exec(input.trim());
  const target = visibleSourceEditor(path, models, viewId);
  if (!match || !target) return false;
  const lineNumber = Number(match[1]);
  const column = Number(match[2] ?? 1);
  if (
    lineNumber > target.model.getLineCount() ||
    column > target.model.getLineMaxColumn(lineNumber)
  )
    return false;
  const position = { lineNumber, column };
  target.code.setPosition(position);
  target.code.revealPositionInCenter(position);
  requestAnimationFrame(() => target.code.focus());
  return true;
}

export function runSourceEditCommand(
  path: string,
  models: SourceModels,
  command: SourceEditCommand,
  viewId?: string,
) {
  const target = visibleSourceEditor(path, models, viewId);
  if (!target) return;
  const { model, code } = target;
  code.focus();
  switch (command) {
    case "editor.undo":
      if (model.canUndo()) void model.undo();
      break;
    case "editor.redo":
      if (model.canRedo()) void model.redo();
      break;
    case "editor.find":
      void code.getAction("actions.find")?.run();
      break;
    case "editor.replace":
      void code.getAction("editor.action.startFindReplaceAction")?.run();
      break;
    case "editor.comment":
      if (supportsSourceLineComments(path)) void code.getAction("editor.action.commentLine")?.run();
      break;
    case "editor.selectAll":
      code.setSelection(model.getFullModelRange());
      break;
    case "editor.nextMatch":
      void code.getAction("editor.action.addSelectionToNextFindMatch")?.run();
      break;
    case "editor.allMatches":
      void code.getAction("editor.action.selectHighlights")?.run();
      break;
  }
}

function reportSourceStatus(
  code: editor.ICodeEditor,
  report: ((status: SourceEditorStatus) => void) | undefined,
  languageOverride = "auto",
) {
  const model = code.getModel();
  const position = code.getPosition();
  if (!report || !model || !position) return;
  const { tabSize, insertSpaces } = model.getOptions();
  const selections = code.getSelections() ?? [];
  report({
    languageOverride,
    selected: selections.reduce((count, range) => count + model.getCharacterCountInRange(range), 0),
    selections: selections.length,
    line: position.lineNumber,
    column: position.column,
    tabSize,
    insertSpaces,
    language: model.getLanguageId(),
  });
}

export function SourceEditor({
  viewId = "primary",
  path,
  value,
  models,
  visible,
  location,
  focusRequest,
  onFocusHandled,
  onChange,
  onStatus,
  onFocus,
}: {
  viewId?: string;
  path: string;
  value: string;
  models: SourceModels;
  visible: boolean;
  location?: SearchMatch;
  focusRequest?: { origin: Element | null };
  onFocusHandled: () => void;
  onChange: (value: string) => void;
  onStatus?: (status: SourceEditorStatus) => void;
  onFocus?: () => void;
}) {
  const container = useRef<HTMLDivElement>(null);
  const instance = useRef<editor.IStandaloneCodeEditor>(null);
  const attached = useRef<ModelState>(null);
  const callbacks = useRef({ onChange, onStatus, onFocus });
  callbacks.current = { onChange, onStatus, onFocus };
  const label = t("文件内容");
  const language = detectedSourceLanguage(path);
  const { editorFontSize, editorWordWrap, editorLineNumbers, editorMinimap, editorScreenReader } =
    useUserPreferences();

  useLayoutEffect(() => {
    const code = editor.create(container.current!, {
      model: null,
      automaticLayout: true,
      fontFamily: "ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
      lineNumbersMinChars: 3,
      wordWrapColumn: 110,
      wrappingIndent: "same",
      padding: { top: 8, bottom: 8 },
      scrollBeyondLastLine: false,
      fixedOverflowWidgets: true,
    });
    instance.current = code;
    editorViews.set(code, viewId);
    const change = code.onDidChangeModelContent((event) => {
      if (!event.isFlush) callbacks.current.onChange(code.getValue());
    });
    const report = () =>
      reportSourceStatus(code, callbacks.current.onStatus, attached.current?.languageOverride);
    sourceReports.set(code, report);
    const statusListeners = [
      code.onDidChangeCursorSelection(report),
      code.onDidChangeModelOptions(report),
      code.onDidChangeModelLanguage(report),
    ];
    const focus = code.onDidFocusEditorWidget(() => callbacks.current.onFocus?.());
    return () => {
      if (attached.current && !attached.current.model.isDisposed())
        attached.current.viewStates.set(viewId, code.saveViewState());
      attached.current = null;
      instance.current = null;
      change.dispose();
      statusListeners.forEach((listener) => listener.dispose());
      focus.dispose();
      editorViews.delete(code);
      sourceReports.delete(code);
      code.dispose();
    };
  }, [viewId]);

  useLayoutEffect(() => {
    instance.current?.updateOptions({
      ariaLabel: label,
      accessibilitySupport: editorScreenReader ? "on" : "auto",
      fontSize: editorFontSize,
      lineHeight: Math.round((editorFontSize * 21) / 13),
      lineNumbers: editorLineNumbers ? "on" : "off",
      wordWrap: editorWordWrap,
      minimap: { enabled: editorMinimap, renderCharacters: false, showSlider: "mouseover" },
      unicodeHighlight: { allowedCharacters: language === "markdown" ? markdownPunctuation : {} },
    });
  }, [
    viewId,
    label,
    language,
    editorFontSize,
    editorWordWrap,
    editorLineNumbers,
    editorMinimap,
    editorScreenReader,
  ]);

  useLayoutEffect(() => {
    const code = instance.current!;
    let next = models.get(path);
    if (next && next.model.getLanguageId() !== (next.languageOverride ?? language))
      editor.setModelLanguage(next.model, next.languageOverride ?? language);
    if (attached.current === next && next) {
      if (next.model.getValue() !== value) next.model.setValue(value);
      reportSourceStatus(code, callbacks.current.onStatus, next.languageOverride);
      return;
    }
    if (attached.current && !attached.current.model.isDisposed())
      attached.current.viewStates.set(viewId, code.saveViewState());
    if (!next) {
      const model = editor.createModel(value, language);
      model.updateOptions({ tabSize: 2, insertSpaces: true });
      next = { model, viewStates: new Map() };
      models.set(path, next);
    } else if (next.model.getValue() !== value) {
      next.model.setValue(value);
      next.viewStates.clear();
    }
    attached.current = next;
    code.setModel(next.model);
    const viewState = next.viewStates.get(viewId);
    if (viewState) code.restoreViewState(viewState);
    reportSourceStatus(code, callbacks.current.onStatus, next.languageOverride);
  }, [models, path, value, language, viewId]);

  useLayoutEffect(() => {
    const code = instance.current;
    const model = code?.getModel();
    if (!visible || !location || !code || !model) return;
    const content = model.getValue();
    const startOffset = sourceOffset(content, location.line, location.column);
    const endOffset = sourceOffset(content, location.line, location.end_column);
    const start = model.getPositionAt(startOffset);
    const end = model.getPositionAt(Math.max(startOffset, endOffset));
    code.setSelection({
      startLineNumber: start.lineNumber,
      startColumn: start.column,
      endLineNumber: end.lineNumber,
      endColumn: end.column,
    });
    code.revealPositionInCenter(start);
    if (!focusRequest) code.focus();
  }, [viewId, path, visible, location]);

  useLayoutEffect(() => {
    if (!visible || !focusRequest) return;
    if (document.activeElement === focusRequest.origin || document.activeElement === document.body)
      instance.current?.focus();
    onFocusHandled();
  }, [viewId, visible, focusRequest, onFocusHandled]);

  return <div className="core-code-editor" ref={container} />;
}
