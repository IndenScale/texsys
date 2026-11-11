import React, { useEffect, useRef, useState } from "react";
import { createMarkdownEditor, type MarkdownEditorController } from "./markdown-editor";
import { t } from "./i18n";
import "@milkdown/kit/prose/view/style/prosemirror.css";
import "./markdown-preview.css";
import "./markdown-editor.css";

export function MarkdownEditor({
  path,
  value,
  onChange,
  onFocus,
}: {
  path: string;
  value: string;
  onChange: (value: string) => void;
  onFocus?: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const instance = useRef<MarkdownEditorController | null>(null);
  const latest = useRef({ value, onChange, onFocus });
  latest.current = { value, onChange, onFocus };
  const [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setError("");
    const created = createMarkdownEditor({
      root: root.current!,
      value: latest.current.value,
      label: t("Markdown 编辑器"),
      onChange: (content) => latest.current.onChange(content),
      onFocus: () => latest.current.onFocus?.(),
    });
    void created.then(
      (controller) => {
        if (cancelled) return;
        instance.current = controller;
        controller.setValue(latest.current.value);
      },
      (reason: unknown) => {
        if (!cancelled) setError(String(reason));
      },
    );
    return () => {
      cancelled = true;
      instance.current = null;
      void created.then(
        (controller) => controller.destroy(),
        () => {},
      );
    };
  }, [path]);
  useEffect(() => instance.current?.setValue(value), [value]);
  return (
    <div className="core-markdown-editor markdown-preview">
      {error && <p role="alert">{error}</p>}
      <div ref={root} />
    </div>
  );
}
