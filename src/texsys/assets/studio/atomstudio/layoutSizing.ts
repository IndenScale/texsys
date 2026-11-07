/* SPDX-License-Identifier: MIT
 * Adapted from AtomStudio. Copyright (c) 2026 AtomDocs Contributors.
 * See SOURCE.json and LICENSE for origin and adaptation scope.
 */
export const DEFAULT_SIDEBAR_WIDTH = 320;
export const MIN_SIDEBAR_WIDTH = 208;
export const MIN_INSPECTOR_WIDTH = 280;
export const MIN_BOTTOM_HEIGHT = 144;

export function clampPanelSize(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, Math.round(value)));
}

export function panelSizeFromPointer(
  startSize: number,
  startPointer: number,
  currentPointer: number,
  min: number,
  max: number,
  direction: 1 | -1 = -1,
): number {
  return clampPanelSize(startSize + (currentPointer - startPointer) * direction, min, max);
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
