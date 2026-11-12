export type Point3 = [number, number, number];

export type DrawingBounds = { x: number; y: number; width: number; height: number };
export type ViewportSize = { width: number; height: number };
export type DrawingViewport = {
  kind: "drawing";
  pageId?: string;
  x: number;
  y: number;
  scale: number;
};
export type SceneViewport = {
  kind: "scene";
  center: Point3;
  yaw: number;
  pitch: number;
  scale: number;
};
export type PreviewViewport = DrawingViewport | SceneViewport;

export function fitDrawingViewport(
  bounds: DrawingBounds,
  size: ViewportSize,
  pageId?: string,
): DrawingViewport {
  return {
    kind: "drawing",
    pageId,
    x: bounds.x + bounds.width / 2,
    y: bounds.y + bounds.height / 2,
    scale: Math.min(size.width / bounds.width, size.height / bounds.height) * 0.96,
  };
}

export function drawingViewBox(view: DrawingViewport, size: ViewportSize): DrawingBounds {
  const width = size.width / view.scale;
  const height = size.height / view.scale;
  return { x: view.x - width / 2, y: view.y - height / 2, width, height };
}

export function sceneBounds(objects: Iterable<{ points: Point3[] }>) {
  const low: Point3 = [Infinity, Infinity, Infinity];
  const high: Point3 = [-Infinity, -Infinity, -Infinity];
  for (const { points } of objects) {
    for (const point of points) {
      for (let axis = 0; axis < 3; axis++) {
        low[axis] = Math.min(low[axis], point[axis]);
        high[axis] = Math.max(high[axis], point[axis]);
      }
    }
  }
  if (!Number.isFinite(low[0])) return;
  const center: Point3 = [(low[0] + high[0]) / 2, (low[1] + high[1]) / 2, (low[2] + high[2]) / 2];
  const radius = Math.max(
    0.000001,
    Math.hypot(...high.map((value, axis) => value - low[axis])) / 2,
  );
  return { center, radius, floor: low[2] };
}

export function fitSceneViewport(
  bounds: NonNullable<ReturnType<typeof sceneBounds>>,
  size: ViewportSize,
): SceneViewport {
  return {
    kind: "scene",
    center: bounds.center,
    yaw: -0.78,
    pitch: 0.62,
    scale: (Math.min(size.width, size.height) * 0.4) / bounds.radius,
  };
}

export function sceneProjection(view: SceneViewport, size: ViewportSize) {
  const { center, yaw, pitch, scale } = view;
  const cos = Math.cos(yaw);
  const sin = Math.sin(yaw);
  const vertical = Math.cos(pitch);
  const tilt = Math.sin(pitch);
  return ([x, y, z]: Point3): Point3 => {
    const dx = x - center[0];
    const dy = y - center[1];
    const dz = z - center[2];
    const depth = sin * dx + cos * dy;
    return [
      size.width / 2 + scale * (cos * dx - sin * dy),
      size.height / 2 - scale * (vertical * dz - tilt * depth),
      vertical * depth + tilt * dz,
    ];
  };
}

export function panSceneViewport(view: SceneViewport, dx: number, dy: number): SceneViewport {
  const x = -dx / view.scale;
  const y = dy / view.scale;
  return {
    ...view,
    center: [
      view.center[0] + Math.cos(view.yaw) * x - Math.sin(view.yaw) * Math.sin(view.pitch) * y,
      view.center[1] - Math.sin(view.yaw) * x - Math.cos(view.yaw) * Math.sin(view.pitch) * y,
      view.center[2] + Math.cos(view.pitch) * y,
    ],
  };
}
