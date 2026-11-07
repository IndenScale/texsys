import React, { useEffect, useRef, useState, type ReactNode } from "react";

/** Keep the departing surface until its exit transition completes. It stops accepting input immediately. */
export function AnimatedPresence({ show, children }: { show: boolean; children: ReactNode }) {
  const [retained, setRetained] = useState(show);
  const content = useRef(children);
  if (show) content.current = children;
  useEffect(() => {
    if (show) {
      setRetained(true);
      return;
    }
    const timer = setTimeout(() => setRetained(false), 220);
    return () => clearTimeout(timer);
  }, [show]);
  return show || retained ? (
    <div className="motion-presence" data-open={show} inert={!show} aria-hidden={!show}>
      {show ? children : content.current}
    </div>
  ) : null;
}
