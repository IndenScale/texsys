/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
import type { ReactNode } from "react";
export function EditorTabBar({ children }: { children: ReactNode }) {
  return (
    <div className="editor-tab-bar editor-area__tab-bar">
      <div className="editor-tab-bar__tabs">{children}</div>
    </div>
  );
}
