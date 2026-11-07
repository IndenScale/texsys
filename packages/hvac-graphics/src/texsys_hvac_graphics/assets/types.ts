export type Point = { x: number; y: number };
export type Primitive = { tag: string; attrs: Record<string, string | number> };
export type Binding = { value?: number; unit: string; state: string; source: string };
export type Port = Point & {
  id: string;
  name: string;
  semantic_ref: string;
  kind: string;
  direction: string;
  side: string;
};
export type Graphic = {
  template: string;
  version: string;
  width: number;
  height: number;
  primitives: Primitive[];
  ports: Port[];
  label_anchor: Point;
  annotation_anchor: Point;
};
export type DiagramNode = {
  id: string;
  semantic_ref: string;
  label: string;
  component: string;
  x: number;
  y: number;
  width: number;
  height: number;
  ports: Port[];
  parameters: Record<string, Binding>;
  symbol: {
    level: string;
    presentation_exact: boolean;
    actual: Record<string, unknown>;
    requested: Record<string, unknown>;
  };
  graphic?: Graphic;
  engineering_labels?: string[];
};
export type DiagramEdge = {
  id: string;
  semantic_ref: string;
  source: string;
  target: string;
  medium: string;
  directed: boolean;
  points: Point[];
  label_position: Point;
};
export type Diagram = {
  schema_version: number;
  title: string;
  view: string;
  target: string;
  profile: string;
  model_digest: string;
  behavior_state: string;
  spatial_state: string;
  deliverable: string;
  nodes: DiagramNode[];
  edges: DiagramEdge[];
  media: Record<string, string>;
  pages: { id: string; width: number; height: number }[];
  presentation?: Record<string, unknown>;
  [key: string]: unknown;
};

export type SimulationVariable = {
  unit: string;
  semantic_ref: string;
  role: string;
  values: number[];
};
export type Simulation = {
  schema_version: number;
  backend?: string | { name: string; [key: string]: unknown };
  status: string;
  scenario: string;
  model_digest: string;
  time_s: number[];
  variables: Record<string, SimulationVariable>;
  events: { time_s: number; changes: Record<string, unknown> }[];
  assertions: {
    status: string;
    variable?: string;
    operator?: string;
    actual?: number;
    value?: number;
    unit?: string;
    message?: string;
  }[];
  checks: Record<string, number>;
};
