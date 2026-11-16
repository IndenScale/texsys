import {
  Box3,
  BufferGeometry,
  Color,
  DirectionalLight,
  DoubleSide,
  Float32BufferAttribute,
  GridHelper,
  Group,
  HemisphereLight,
  Line,
  LineBasicMaterial,
  Mesh,
  MeshLambertMaterial,
  OrthographicCamera,
  PerspectiveCamera,
  Plane,
  Raycaster,
  Scene,
  ShapeUtils,
  Vector2,
  Vector3,
  WebGLRenderer,
} from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import type { Point3, SceneViewport } from "./preview-viewport";
import { defaultScenePreferences } from "./user-preferences";

export type ModelObject = {
  id: string;
  prim_path: string;
  owner_path?: string;
  semantic_ref: string | null;
  name: string;
  points: Point3[];
  faces: number[][];
  envelope: boolean;
  type?: string;
  facets?: Record<string, string>;
  collections?: string[];
  color?: Point3 | null;
  properties?: Record<string, { value: string | boolean | number; unit?: string; prim: string }>;
};
export type SceneAxis = "x" | "y" | "z";
export type ViewDirection = "iso" | "top" | "front" | "side";
type Hit = { id: string; point: Point3 };
type ViewState = {
  visible: Set<string>;
  ghost: Set<string>;
  selected: ReadonlySet<string>;
  hovered: string;
};
const axes = { x: new Vector3(1, 0, 0), y: new Vector3(0, 1, 0), z: new Vector3(0, 0, 1) };
const directions = {
  iso: new Vector3(1, -1.45, 1),
  top: new Vector3(0, -0.00001, 1),
  front: new Vector3(0, -1, 0),
  side: new Vector3(1, 0, 0),
};
const palette = [0x749aa8, 0x788daf, 0x889e95, 0x988a75];
const movementKeys = new Set(["KeyW", "KeyA", "KeyS", "KeyD", "KeyQ", "KeyE"]);
const lookKeys = new Set(["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"]);
const radians = Math.PI / 180;

function keyAxis(keys: ReadonlySet<string>, positive: string, negative: string) {
  return Number(keys.has(positive)) - Number(keys.has(negative));
}

function navigationDelta(right: Vector3, keys: ReadonlySet<string>, distance: number) {
  const horizontal = right.clone().setZ(0).normalize();
  return new Vector3()
    .addScaledVector(new Vector3().crossVectors(axes.z, horizontal), keyAxis(keys, "KeyW", "KeyS"))
    .addScaledVector(horizontal, keyAxis(keys, "KeyD", "KeyA"))
    .addScaledVector(axes.z, keyAxis(keys, "KeyE", "KeyQ"))
    .normalize()
    .multiplyScalar(distance);
}

function triangles(points: Point3[], faces: number[][]) {
  return faces.flatMap((face) => {
    if (face.length === 3) return face;
    const normal = new Vector3();
    for (let i = 0; i < face.length; i++) {
      const [x, y, z] = points[face[i]];
      const [a, b, c] = points[face[(i + 1) % face.length]];
      normal.x += (y - b) * (z + c);
      normal.y += (z - c) * (x + a);
      normal.z += (x - a) * (y + b);
    }
    const axis = normal
      .toArray()
      .map(Math.abs)
      .indexOf(Math.max(...normal.toArray().map(Math.abs)));
    const vertices = face.map((id) => {
      const vertex = points[id];
      return new Vector2(vertex[(axis + 1) % 3], vertex[(axis + 2) % 3]);
    });
    return ShapeUtils.triangulateShape(vertices, []).flatMap((triangle) =>
      triangle.map((index) => face[index]),
    );
  });
}

export class SceneRenderer {
  private readonly renderer: WebGLRenderer;
  private readonly scene = new Scene();
  private readonly model = new Group();
  private readonly grid = new GridHelper(2, 10, 0x9aaeb8, 0xc9d6dc);
  private readonly overlay = new Group();
  private readonly raycaster = new Raycaster();
  private readonly meshes = new Map<string, Mesh<BufferGeometry, MeshLambertMaterial>>();
  private readonly materials = new Map<string, MeshLambertMaterial>();
  private readonly perspective = new PerspectiveCamera(52, 1, 0.01, 5000);
  private readonly orthographic = new OrthographicCamera(-1, 1, 1, -1, 0.01, 5000);
  private camera: PerspectiveCamera | OrthographicCamera = this.perspective;
  private controls?: OrbitControls;
  private readonly target = new Vector3();
  private readonly worldBounds = new Box3();
  private offset = new Vector3();
  private visible = new Set<string>();
  private ghost = new Set<string>();
  private keys = new Set<string>();
  private width = 1;
  private height = 1;
  private span = 2;
  private navigation = defaultScenePreferences;
  private boosted = false;
  private lastFrame = 0;
  private frame = 0;
  private cut?: Plane;
  private measureLine?: Line<BufferGeometry, LineBasicMaterial>;

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly changed: (viewport: SceneViewport) => void,
  ) {
    this.renderer = new WebGLRenderer({ canvas, antialias: true, alpha: false });
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.scene.background = new Color(0xe9eff2);
    this.perspective.up.copy(axes.z);
    this.orthographic.up.copy(axes.z);
    this.grid.rotation.x = Math.PI / 2;
    this.grid.material.transparent = true;
    this.grid.material.opacity = 0.66;
    this.scene.add(
      this.grid,
      this.model,
      this.overlay,
      new HemisphereLight(0xf4f9fd, 0x84949e, 2.05),
    );
    const sun = new DirectionalLight(0xffffff, 2.15);
    sun.position.set(20, -14, 38);
    this.scene.add(sun);
    this.attachOrbit();
  }

  private attachOrbit() {
    this.controls?.dispose();
    this.controls = new OrbitControls(this.camera, this.canvas);
    this.controls.target.copy(this.target);
    this.controls.rotateSpeed = this.navigation.sceneMouseSensitivity;
    this.controls.zoomToCursor = true;
    this.controls.enableDamping = false;
    this.controls.addEventListener("change", () => this.draw());
    this.controls.addEventListener("end", () => this.saveView());
    this.controls.update();
  }

  private material(color: Color, appearance: string) {
    const key = `${color.getHexString()}/${appearance}`;
    const cached = this.materials.get(key);
    if (cached) return cached;
    const ghost = appearance === "ghost";
    const highlighted = appearance === "selected" || appearance === "hovered";
    const material = new MeshLambertMaterial({
      color: highlighted ? (appearance === "selected" ? 0x137f76 : 0x38ab9c) : color,
      emissive: highlighted ? 0x154e46 : 0x000000,
      emissiveIntensity: 0.55,
      side: DoubleSide,
      transparent: ghost,
      opacity: ghost ? 0.19 : 1,
      depthWrite: !ghost,
    });
    this.materials.set(key, material);
    return material;
  }

  load(objects: ModelObject[], viewport?: SceneViewport) {
    for (const mesh of this.meshes.values()) mesh.geometry.dispose();
    this.meshes.clear();
    this.model.clear();
    this.worldBounds.makeEmpty();
    for (const object of objects) {
      for (const point of object.points) this.worldBounds.expandByPoint(new Vector3(...point));
    }
    this.offset = this.worldBounds.getCenter(new Vector3());
    this.visible = new Set(objects.map((object) => object.id));
    for (const [index, object] of objects.entries()) {
      const positions = object.points.flatMap(([x, y, z]) => [
        x - this.offset.x,
        y - this.offset.y,
        z - this.offset.z,
      ]);
      const geometry = new BufferGeometry();
      geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
      geometry.setIndex(triangles(object.points, object.faces));
      geometry.computeVertexNormals();
      geometry.computeBoundingBox();
      const color = object.color
        ? new Color(...object.color)
        : new Color(palette[index % palette.length]);
      const mesh = new Mesh(geometry, this.material(color, "normal"));
      mesh.userData = { id: object.id, color };
      this.meshes.set(object.id, mesh);
      this.model.add(mesh);
    }
    const size = this.worldBounds.getSize(new Vector3());
    this.grid.scale.setScalar(Math.max(size.x, size.y, 1) * 0.59);
    this.grid.position.setZ(
      this.worldBounds.isEmpty() ? 0 : this.worldBounds.min.z - this.offset.z,
    );
    this.span = 2;
    this.setSection(undefined, 0.5);
    if (viewport?.eye && viewport.at) this.restore(viewport);
    else this.fit();
  }

  resize(width: number, height: number) {
    if (!width || !height) return;
    this.width = width;
    this.height = height;
    this.renderer.setSize(width, height, false);
    this.updateProjection();
    this.draw();
  }

  private updateProjection() {
    const aspect = this.width / this.height;
    this.perspective.aspect = aspect;
    this.perspective.updateProjectionMatrix();
    this.orthographic.top = this.span / 2;
    this.orthographic.bottom = -this.span / 2;
    this.orthographic.left = (-this.span * aspect) / 2;
    this.orthographic.right = (this.span * aspect) / 2;
    this.orthographic.updateProjectionMatrix();
  }

  private restore(view: SceneViewport) {
    if (!view.eye || !view.at) return;
    this.camera = view.projection === "orthographic" ? this.orthographic : this.perspective;
    this.span = view.span ?? 2;
    this.orthographic.zoom = view.zoom ?? 1;
    this.target.set(...view.at).sub(this.offset);
    this.camera.position.set(...view.eye).sub(this.offset);
    this.camera.lookAt(this.target);
    this.updateProjection();
    this.attachOrbit();
    this.draw();
  }

  private saveView() {
    const at = this.controls?.target ?? this.target;
    this.target.copy(at);
    this.changed({
      kind: "scene",
      center: new Vector3().copy(at).add(this.offset).toArray(),
      yaw: 0,
      pitch: 0,
      scale: 1,
      eye: this.camera.position.clone().add(this.offset).toArray(),
      at: at.clone().add(this.offset).toArray(),
      projection: this.camera === this.orthographic ? "orthographic" : "perspective",
      span: this.span,
      zoom: this.orthographic.zoom,
    });
  }

  private bounds(ids: Iterable<string>) {
    const box = new Box3();
    for (const id of ids) {
      const geometry = this.meshes.get(id)?.geometry;
      if (geometry?.boundingBox) box.union(geometry.boundingBox);
    }
    return box;
  }

  fit(ids: Iterable<string> = this.visible, direction?: ViewDirection) {
    const bounds = this.bounds(ids);
    if (bounds.isEmpty()) return;
    const from = direction
      ? directions[direction].clone().normalize()
      : this.camera.position.clone().sub(this.target).normalize();
    if (from.lengthSq() === 0) from.copy(directions.iso).normalize();
    if (direction) this.camera = direction === "iso" ? this.perspective : this.orthographic;
    const radius = Math.max(bounds.getSize(new Vector3()).length() / 2, 0.05);
    const aspect = this.width / this.height;
    this.span = (2.24 * radius) / Math.min(1, aspect);
    this.orthographic.zoom = 1;
    const halfAngle = Math.atan(
      Math.tan((this.perspective.fov * radians) / 2) * Math.min(1, aspect),
    );
    const distance = (1.16 * radius) / Math.sin(halfAngle);
    bounds.getCenter(this.target);
    this.camera.position.copy(this.target).addScaledVector(from, distance);
    const far = Math.max(
      5000,
      distance * 100,
      this.worldBounds.getSize(new Vector3()).length() * 5,
    );
    this.camera.near = Math.max(0.001, Math.min(radius / 2000, 0.2));
    this.camera.far = far;
    this.camera.lookAt(this.target);
    this.updateProjection();
    this.attachOrbit();
    this.saveView();
    this.draw();
  }

  zoom(factor: number) {
    if (this.camera === this.orthographic) {
      this.orthographic.zoom = Math.min(5000, Math.max(0.008, this.orthographic.zoom * factor));
      this.orthographic.updateProjectionMatrix();
    } else {
      const delta = this.camera.position.clone().sub(this.target);
      this.camera.position.copy(this.target).addScaledVector(delta, 1 / factor);
    }
    this.saveView();
    this.draw();
  }

  setState({ visible, ghost, selected, hovered }: ViewState) {
    this.visible = visible;
    this.ghost = ghost;
    for (const [id, mesh] of this.meshes) {
      mesh.visible = visible.has(id);
      if (!mesh.visible) continue;
      mesh.material = this.material(
        mesh.userData.color as Color,
        ghost.has(id)
          ? "ghost"
          : selected.has(id)
            ? "selected"
            : hovered === id
              ? "hovered"
              : "normal",
      );
    }
    this.draw();
  }

  setSection(axis: SceneAxis | undefined, fraction: number) {
    this.cut = undefined;
    if (axis && !this.worldBounds.isEmpty()) {
      const min = this.worldBounds.min[axis] - this.offset[axis];
      const max = this.worldBounds.max[axis] - this.offset[axis];
      this.cut = new Plane(axes[axis].clone().negate(), min + (max - min) * fraction);
    }
    this.renderer.clippingPlanes = this.cut ? [this.cut] : [];
    this.draw();
  }

  measurement(points: Point3[]) {
    if (this.measureLine) {
      this.overlay.remove(this.measureLine);
      this.measureLine.geometry.dispose();
      this.measureLine.material.dispose();
      this.measureLine = undefined;
    }
    if (points.length === 2) {
      const geometry = new BufferGeometry().setFromPoints(
        points.map((point) => new Vector3(...point).sub(this.offset)),
      );
      this.measureLine = new Line(
        geometry,
        new LineBasicMaterial({ color: 0xaa4525, depthTest: false }),
      );
      this.overlay.add(this.measureLine);
    }
    this.draw();
  }

  pick(x: number, y: number): Hit | undefined {
    this.raycaster.setFromCamera(
      new Vector2((x / this.width) * 2 - 1, 1 - (y / this.height) * 2),
      this.camera,
    );
    const hits = this.raycaster
      .intersectObjects(
        [...this.visible].flatMap((id) => this.meshes.get(id) ?? []),
        false,
      )
      .filter(({ point }) => !this.cut || this.cut.distanceToPoint(point) >= -0.00001);
    const hit = hits.find(({ object }) => !this.ghost.has(object.userData.id)) ?? hits[0];
    if (hit)
      return { id: hit.object.userData.id as string, point: hit.point.add(this.offset).toArray() };
  }

  setNavigation(preferences: typeof defaultScenePreferences) {
    this.navigation = preferences;
    if (this.controls) this.controls.rotateSpeed = preferences.sceneMouseSensitivity;
  }

  private look(right: Vector3, horizontal: number, vertical: number) {
    const direction = this.camera.getWorldDirection(new Vector3());
    const yaw = Math.atan2(right.x, -right.y) - horizontal;
    const limit = Math.PI / 2 - 0.00001;
    const pitch = Math.max(-limit, Math.min(limit, Math.asin(direction.z) + vertical));
    direction.set(
      Math.cos(yaw) * Math.cos(pitch),
      Math.sin(yaw) * Math.cos(pitch),
      Math.sin(pitch),
    );
    const distance = this.camera.position.distanceTo(this.target);
    this.target.copy(this.camera.position).addScaledVector(direction, Math.max(0.1, distance));
    this.controls?.target.copy(this.target);
    this.camera.lookAt(this.target);
  }

  key(code: string, pressed: boolean, shift: boolean) {
    const navigationKey = movementKeys.has(code) || lookKeys.has(code);
    const now = performance.now();
    if (
      this.frame &&
      (this.boosted !== shift || (navigationKey && this.keys.has(code) !== pressed))
    )
      this.advance(now);
    this.boosted = shift;
    if (!navigationKey) return false;
    if (pressed) this.keys.add(code);
    else this.keys.delete(code);
    if (this.keys.size && !this.frame) {
      this.lastFrame = now;
      this.frame = requestAnimationFrame(this.step);
    } else if (!this.keys.size) this.stop();
    return true;
  }

  stop() {
    this.keys.clear();
    this.boosted = false;
    if (!this.frame) return;
    cancelAnimationFrame(this.frame);
    this.frame = 0;
    this.lastFrame = 0;
    this.saveView();
  }

  private advance(time: number) {
    // Track elapsed time through ordinary slow frames, while bounding a stalled tab's step.
    const elapsed = Math.max(0, Math.min((time - this.lastFrame) / 1000, 0.25));
    if (!elapsed) return;
    this.lastFrame = time;
    const right = new Vector3(1, 0, 0).applyQuaternion(this.camera.quaternion);
    const yaw = keyAxis(this.keys, "ArrowRight", "ArrowLeft");
    const pitch = keyAxis(this.keys, "ArrowUp", "ArrowDown");
    if (yaw || pitch) this.look(right, yaw * elapsed * 1.5, pitch * elapsed * 1.5);
    const speed = this.navigation.sceneMoveSpeed * (this.boosted ? this.navigation.sceneBoost : 1);
    const movement = navigationDelta(
      right.set(1, 0, 0).applyQuaternion(this.camera.quaternion),
      this.keys,
      speed * elapsed,
    );
    this.camera.position.add(movement);
    this.target.add(movement);
    this.controls?.target.copy(this.target);
    this.draw();
  }

  private step = (time: number) => {
    this.advance(time);
    this.frame = requestAnimationFrame(this.step);
  };

  private draw() {
    this.renderer.render(this.scene, this.camera);
  }

  dispose() {
    if (this.frame) cancelAnimationFrame(this.frame);
    this.controls?.dispose();
    for (const mesh of this.meshes.values()) mesh.geometry.dispose();
    for (const material of this.materials.values()) material.dispose();
    this.measureLine?.geometry.dispose();
    this.measureLine?.material.dispose();
    this.grid.geometry.dispose();
    this.grid.material.dispose();
    this.renderer.dispose();
  }
}
