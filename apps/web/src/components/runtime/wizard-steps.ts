import type { RuntimeType, RuntimeTypeInfo } from "../../types";

/** Step order for the runtime create wizard. */
export const WIZARD_STEPS = [
  "type",
  "deployment",
  "provider",
  "model",
  "resources",
  "confirm",
] as const;
export type WizardStep = (typeof WIZARD_STEPS)[number];

export interface WizardState {
  runtimeType: RuntimeType | null;
  deploymentMode: string;
  providerId: number | null;
  model: string;
  cpuLimit: number;
  memoryLimitMb: number;
}

export const INITIAL_WIZARD_STATE: WizardState = {
  runtimeType: null,
  deploymentMode: "docker",
  providerId: null,
  model: "",
  cpuLimit: 1,
  memoryLimitMb: 512,
};

/** Whether the wizard may leave `step` given the current selections. Pure; unit-tested. */
export function canAdvance(
  step: WizardStep,
  state: WizardState,
  runtimeTypes: RuntimeTypeInfo[],
): boolean {
  const selected = runtimeTypes.find((t) => t.type === state.runtimeType);
  switch (step) {
    case "type":
      return selected != null && selected.implemented;
    case "deployment":
      return (
        selected != null &&
        selected.deployment_modes.includes("docker") &&
        selected.docker_available
      );
    case "provider":
      // Mock 运行时不连接模型服务：没有 provider 也应该能建出来
      return state.runtimeType === "mock" || state.providerId != null;
    case "model":
      return state.runtimeType === "mock" || state.model.trim().length > 0;
    case "resources":
      return state.cpuLimit > 0 && state.memoryLimitMb >= 128;
    case "confirm":
      return true;
  }
}
