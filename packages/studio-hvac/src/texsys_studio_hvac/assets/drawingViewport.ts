import {
  useLayoutEffect,
  useRef,
  useState,
  type RefObject,
  type PointerEvent as ReactPointerEvent,
} from "react";

export function fitDrawingWidth(
  width: number,
  height: number,
  pageWidth: number,
  pageHeight: number,
) {
  return Math.max(160, Math.min(width - 64, ((height - 72) * pageWidth) / (pageHeight + 92)));
}

/** Layout changes move the aperture, never refit the drawing or move its screen anchor. */
export function useDrawingViewport(
  ref: RefObject<HTMLDivElement | null>,
  enabled: boolean,
  key: string,
  pageWidth: number,
  pageHeight: number,
  resetZoom: () => void,
) {
  const [width, setWidth] = useState(800);
  const [panning, setPanning] = useState(false);
  const camera = useRef({ key: "", left: 0, top: 0, x: 0, y: 0 });
  const drag = useRef<{ x: number; y: number; panX: number; panY: number } | null>(null);
  const paint = () => {
    const element = ref.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    element.style.setProperty(
      "--camera-x",
      `${camera.current.left - rect.left + camera.current.x}px`,
    );
    element.style.setProperty(
      "--camera-y",
      `${camera.current.top - rect.top + camera.current.y}px`,
    );
  };
  const fit = () => {
    const element = ref.current;
    if (!element) return;
    const rect = element.getBoundingClientRect();
    if (!rect.width || !rect.height) return;
    camera.current = { key, left: rect.left, top: rect.top, x: 0, y: 0 };
    setWidth(fitDrawingWidth(element.clientWidth, element.clientHeight, pageWidth, pageHeight));
    resetZoom();
    paint();
  };
  useLayoutEffect(() => {
    const element = ref.current;
    if (!enabled || !element) return;
    const update = () => {
      if (!element.clientWidth || !element.clientHeight) return;
      if (camera.current.key !== key) fit();
      else paint();
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(element);
    const wheel = (event: WheelEvent) => {
      if (event.ctrlKey || event.metaKey) return;
      event.preventDefault();
      const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1;
      camera.current.x -= (event.shiftKey ? event.deltaY : event.deltaX) * unit;
      camera.current.y -= (event.shiftKey ? event.deltaX : event.deltaY) * unit;
      paint();
    };
    element.addEventListener("wheel", wheel, { passive: false });
    return () => {
      observer.disconnect();
      element.removeEventListener("wheel", wheel);
    };
  }, [enabled, key]);
  const stop = () => {
    drag.current = null;
    setPanning(false);
  };
  const locate = (element: Element | undefined) => {
    if (!element || !ref.current) return;
    const bounds = ref.current.getBoundingClientRect(),
      target = element.getBoundingClientRect();
    camera.current.x += bounds.left + bounds.width / 2 - (target.left + target.width / 2);
    camera.current.y += bounds.top + bounds.height / 2 - (target.top + target.height / 2);
    paint();
  };
  return {
    width,
    panning,
    fit,
    locate,
    handlers: {
      onPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
        if (
          event.button ||
          (event.target instanceof Element &&
            event.target.closest('[role="button"],button,input,select'))
        )
          return;
        drag.current = {
          x: event.clientX,
          y: event.clientY,
          panX: camera.current.x,
          panY: camera.current.y,
        };
        setPanning(true);
        event.currentTarget.setPointerCapture(event.pointerId);
      },
      onPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
        if (!drag.current) return;
        camera.current.x = drag.current.panX + event.clientX - drag.current.x;
        camera.current.y = drag.current.panY + event.clientY - drag.current.y;
        paint();
      },
      onPointerUp: stop,
      onPointerCancel: stop,
      onLostPointerCapture: stop,
    },
  };
}
