/** Keep an ordered prefix visible, reserving the overflow trigger's own width. */
export function visibleMenuCount(
  available: number,
  widths: number[],
  overflowWidth: number,
  gap = 2,
): number {
  const space = Math.max(0, Math.floor(available));
  const sizes = widths.map(Math.ceil);
  if (sizes.reduce((sum, width) => sum + width, 0) + Math.max(0, sizes.length - 1) * gap <= space)
    return sizes.length;
  let used = Math.ceil(overflowWidth),
    count = 0;
  for (const width of sizes) {
    if (used + width + gap > space) break;
    used += width + gap;
    count++;
  }
  return count;
}
