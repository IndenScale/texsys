import React, { useEffect, useLayoutEffect, useRef, useState } from "react";
import { t } from "./i18n";
import { Empty } from "./ui";

export function HtmlPreview({ content, path }: { content: string; path: string }) {
  const [snapshot, setSnapshot] = useState({ content, revision: 0 });
  useEffect(() => {
    if (snapshot.content === content) return;
    const timer = window.setTimeout(() => {
      setSnapshot(({ revision }) => ({ content, revision: revision + 1 }));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [content, snapshot]);

  if (new TextEncoder().encode(snapshot.content).byteLength > 1024 * 1024)
    return <Empty title={t("无法预览此文件")}>{t("HTML 预览限 1 MiB")}</Empty>;
  return <HtmlPreviewFrame key={snapshot.revision} content={snapshot.content} path={path} />;
}

function HtmlPreviewFrame({ content, path }: { content: string; path: string }) {
  const frame = useRef<HTMLIFrameElement>(null);
  const [failed, setFailed] = useState(false);

  useLayoutEffect(() => {
    const element = frame.current!;
    const onReady = (event: MessageEvent) => {
      const target = element.contentWindow;
      if (!target || event.source !== target || event.origin !== "null") return;
      if (event.data?.type !== "texsys:html-preview-ready") return;
      window.removeEventListener("message", onReady);
      window.clearTimeout(timeout);
      // The preview has an opaque origin. Only this window receives the buffer, once.
      target.postMessage({ type: "texsys:html-preview", content }, "*");
    };
    const timeout = window.setTimeout(() => {
      window.removeEventListener("message", onReady);
      setFailed(true);
    }, 5000);
    window.addEventListener("message", onReady);
    element.src = "/api/html-preview";
    return () => {
      window.removeEventListener("message", onReady);
      window.clearTimeout(timeout);
    };
  }, [content]);

  if (failed) return <Empty title={t("无法预览此文件")}>{t("无法启动 HTML 预览")}</Empty>;
  return (
    <iframe
      ref={frame}
      className="project-preview-html"
      title={`${t("HTML 预览")} · ${path}`}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
    />
  );
}
