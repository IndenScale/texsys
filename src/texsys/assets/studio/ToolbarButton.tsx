import React, {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
} from "react";
import { createPortal } from "react-dom";
import { Keycaps } from "./Keycaps";
import { AnimatedPresence } from "./AnimatedPresence";

type TooltipButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  tooltip: string;
  shortcut?: string[];
};

export function ToolbarButton({ className = "", ...props }: TooltipButtonProps) {
  return <TooltipButton {...props} className={`toolbar-button ${className}`} />;
}

/** Place the tooltip above the workbench layout while keeping focus on the trigger. */
export function TooltipButton({
  tooltip,
  shortcut,
  className = "",
  onPointerEnter,
  onPointerLeave,
  onPointerDown,
  onFocus,
  onBlur,
  onClick,
  onKeyDown,
  ...props
}: TooltipButtonProps) {
  const button = useRef<HTMLButtonElement>(null),
    tip = useRef<HTMLDivElement>(null),
    timer = useRef<number | undefined>(undefined);
  const id = useId();
  const [open, setOpen] = useState(false),
    [position, setPosition] = useState<{ left: number; top: number }>();
  const cancel = () => {
    window.clearTimeout(timer.current);
    timer.current = undefined;
  };
  const dismiss = () => {
    cancel();
    setOpen(false);
  };
  const show = () => {
    cancel();
    if (!open && !props.disabled)
      timer.current = window.setTimeout(() => {
        setPosition(undefined);
        setOpen(true);
      }, 550);
  };
  useEffect(() => () => window.clearTimeout(timer.current), []);
  useEffect(() => {
    if (props.disabled) {
      window.clearTimeout(timer.current);
      setOpen(false);
    }
  }, [props.disabled]);
  useLayoutEffect(() => {
    if (!open || !button.current || !tip.current) return;
    const place = () => {
      if (!button.current || !tip.current) return;
      const anchor = button.current.getBoundingClientRect(),
        box = tip.current.getBoundingClientRect();
      setPosition({
        left: Math.max(
          8,
          Math.min(window.innerWidth - box.width - 8, anchor.left + (anchor.width - box.width) / 2),
        ),
        top:
          anchor.bottom + 8 + box.height <= window.innerHeight - 8
            ? anchor.bottom + 8
            : Math.max(8, anchor.top - box.height - 8),
      });
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true);
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open, tooltip]);
  return (
    <>
      <button
        {...props}
        ref={button}
        className={className}
        aria-describedby={props["aria-describedby"] ?? (open ? id : undefined)}
        onPointerEnter={(event) => {
          show();
          onPointerEnter?.(event);
        }}
        onPointerLeave={(event) => {
          dismiss();
          onPointerLeave?.(event);
        }}
        onPointerDown={(event) => {
          dismiss();
          onPointerDown?.(event);
        }}
        onFocus={(event) => {
          if (event.currentTarget.matches(":focus-visible")) show();
          onFocus?.(event);
        }}
        onBlur={(event) => {
          dismiss();
          onBlur?.(event);
        }}
        onClick={(event) => {
          dismiss();
          onClick?.(event);
        }}
        onKeyDown={(event) => {
          if (event.key === "Escape") dismiss();
          onKeyDown?.(event);
        }}
      />
      {createPortal(
        <AnimatedPresence show={open}>
          <div
            ref={tip}
            id={id}
            role="tooltip"
            className="chrome-tooltip"
            style={{
              left: position?.left ?? 0,
              top: position?.top ?? 0,
              visibility: position ? "visible" : "hidden",
            }}
          >
            <span>{tooltip}</span>
            {shortcut && <Keycaps keys={shortcut} />}
          </div>
        </AnimatedPresence>,
        document.body,
      )}
    </>
  );
}
