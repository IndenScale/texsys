import { createContext, useContext, type ReactNode } from "react";
import { createPortal } from "react-dom";

export const EditorControlTarget = createContext<HTMLDivElement | null>(null);

/** File previews contribute controls to the containing editor group. */
export function EditorControlSlot({ children }: { children: ReactNode }) {
  const target = useContext(EditorControlTarget);
  return target ? createPortal(children, target) : null;
}
