export type StudioAiMode = "disabled" | "byok" | "byoa";

export type StudioAiSettings = {
  mode: StudioAiMode;
  baseUrl: string;
  model: string;
  hasApiKey: boolean;
};
