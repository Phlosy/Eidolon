import type {
  AccessPackage,
  EmployeeRole,
  ModelEntry,
  ProviderType,
  RuntimeType,
} from "../../types";

/** Step order for the hire wizard (v0.4). */
export const HIRE_WIZARD_STEPS = [
  "identity",
  "department",
  "manager",
  "runtime",
  "provider",
  "brain",
  "packages",
  "preview",
  "hire",
] as const;
export type HireWizardStep = (typeof HIRE_WIZARD_STEPS)[number];

export interface HireWizardState {
  name: string;
  slug: string;
  /** True once the user edits the slug manually — stops auto-derivation. */
  slugTouched: boolean;
  title: string;
  role: EmployeeRole;
  departmentId: number | null;
  positionId: number | null;
  managerEmployeeId: number | null;
  runtimeType: RuntimeType | null;
  providerMode: "none" | "existing" | "new";
  providerId: number | null;
  providerName: string;
  providerType: ProviderType;
  providerBaseUrl: string;
  providerApiKey: string;
  /** 条目式模型编辑器的条目；model 字段始终等于默认条目的真实模型名 */
  providerEntries: ModelEntry[];
  model: string;
  personality: string;
  goals: string;
  learningEnabled: boolean;
  curiosity: number;
  packageIds: number[];
}

export const INITIAL_HIRE_STATE: HireWizardState = {
  name: "",
  slug: "",
  slugTouched: false,
  title: "",
  role: "engineer",
  departmentId: null,
  positionId: null,
  managerEmployeeId: null,
  runtimeType: "mock",
  providerMode: "none",
  providerId: null,
  providerName: "",
  providerType: "openai",
  providerBaseUrl: "",
  providerApiKey: "",
  providerEntries: [],
  model: "",
  personality: "结构化、可靠、主动沟通",
  goals: "持续交付高质量成果，并积累可复用知识",
  learningEnabled: true,
  curiosity: 0.6,
  packageIds: [],
};

/** Whether the wizard may leave `step` given the current state. Pure; unit-tested. */
export function canAdvanceHire(step: HireWizardStep, state: HireWizardState): boolean {
  switch (step) {
    case "identity":
      return state.name.trim().length > 0 && state.title.trim().length > 0;
    case "department":
      return state.departmentId != null;
    case "manager":
      return true; // optional
    case "runtime":
      return state.runtimeType != null;
    case "provider":
      // 真实运行时没有 provider+model 就起不了容器：不允许“稍后配置”
      if (state.providerMode === "none") return state.runtimeType === "mock";
      if (state.providerMode === "existing") {
        return state.providerId != null && state.model.trim().length > 0;
      }
      return state.providerName.trim().length > 0 && state.model.trim().length > 0;
    case "brain":
      return state.personality.trim().length > 0;
    case "packages":
      return true; // optional — packages can be assigned later
    case "preview":
      return true; // unavailable steps warn but never block
    case "hire":
      // The finish line needs the same minimum as the contract requires.
      return state.name.trim().length > 0 && state.departmentId != null;
  }
}

/** ASCII slug derivation from a display name (CJK input yields "" — user types one). */
export function deriveSlug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

/**
 * Role-default packages (doc §9): `base-employee` plus the package whose slug
 * matches the role (`qa_engineer` → `qa-engineer`). Convention-based because
 * the /access-packages contract exposes no role field.
 */
export function defaultPackageIds(packages: AccessPackage[], role: EmployeeRole): number[] {
  const roleSlug = role.replace(/_/g, "-");
  return packages.filter((p) => p.slug === "base-employee" || p.slug === roleSlug).map((p) => p.id);
}
