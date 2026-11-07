import { t } from "./i18n";
import React, { useEffect, useState } from "react";
import type { FilePreview, WorkbenchProps } from "./sdk";
import { Empty, Icon } from "./ui";

export function FileDocument({ path, api }: { path: string; api: WorkbenchProps["api"] }) {
  const [file, setFile] = useState<FilePreview>(),
    [error, setError] = useState("");
  useEffect(() => {
    let current = true;
    setFile(undefined);
    setError("");
    void api<FilePreview>(`/api/file?path=${encodeURIComponent(path)}`)
      .then((data) => {
        if (current) setFile(data);
      })
      .catch((error) => {
        if (current) setError(String(error));
      });
    return () => {
      current = false;
    };
  }, [path, api]);
  return (
    <div className="file-document">
      <header>
        <Icon name="code" size={15} />
        <span title={path}>{path}</span>
        <span className="file-readonly">{t("只读")}</span>
      </header>
      {error ? (
        <Empty title={t("无法打开文件")}>{error}</Empty>
      ) : !file ? (
        <Empty title={t("读取文件中")} />
      ) : !file.previewable ? (
        <Empty title={t("无法文本预览")}>{t(file.reason)}</Empty>
      ) : (
        <div
          className="file-document-code"
          role="region"
          aria-label={t("文件内容 {0}", { "0": path })}
          tabIndex={0}
        >
          <pre>
            {file.content
              ?.split("\n")
              .slice(0, 5000)
              .map((line, index) => (
                <span className="file-code-line" key={index}>
                  <span className="file-line-number" aria-hidden="true">
                    {index + 1}
                  </span>
                  <code>{line || " "}</code>
                </span>
              ))}
          </pre>
          {(file.content?.split("\n").length ?? 0) > 5000 && (
            <p className="file-preview-limit">{t("仅预览前 5000 行。")}</p>
          )}
        </div>
      )}
    </div>
  );
}
