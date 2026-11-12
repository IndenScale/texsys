import { useLayoutEffect, useState } from "react";
import type { ViewportSize } from "./preview-viewport";

export function usePreviewSize<T extends HTMLElement | SVGElement>() {
  const [element, setElement] = useState<T | null>(null);
  const [size, setSize] = useState<ViewportSize>({ width: 0, height: 0 });
  useLayoutEffect(() => {
    if (!element) return;
    const measure = () => {
      const { clientWidth: width, clientHeight: height } = element;
      setSize((previous) =>
        width === previous.width && height === previous.height ? previous : { width, height },
      );
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(element);
    return () => observer.disconnect();
  }, [element]);
  return { element, setElement, size };
}
