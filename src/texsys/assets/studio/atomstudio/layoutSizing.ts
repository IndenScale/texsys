/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
export function clampPanelSize(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function panelSizeFromPointer(
  startSize: number,
  startPointer: number,
  currentPointer: number,
  min: number,
  max: number,
): number {
  return clampPanelSize(startSize + startPointer - currentPointer, min, max);
}

export function panelSizeFromContainerPointer(
  currentPointer: number,
  containerStart: number,
  containerEnd: number,
  growsForward: boolean,
  min: number,
  max: number,
): number {
  const size = growsForward ? currentPointer - containerStart : containerEnd - currentPointer;
  return clampPanelSize(size, min, max);
}
