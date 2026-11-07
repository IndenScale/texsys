import React from "react";

export function Keycaps({ keys }: { keys: string[] }) {
  return (
    <span className="keycaps" aria-label={keys.join(" ")}>
      {keys.map((key, index) => (
        <kbd key={index} className={key.length > 1 ? "keycap-wide" : undefined}>
          {key}
        </kbd>
      ))}
    </span>
  );
}
