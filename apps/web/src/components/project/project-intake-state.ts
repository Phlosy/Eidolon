import type { CreateProjectInput, RequirementInput } from "../../types";

export type IntakeStep = "basic" | "purpose" | "requirements" | "delivery" | "reviews" | "confirm";

export const INTAKE_STEPS: IntakeStep[] = [
  "basic",
  "purpose",
  "requirements",
  "delivery",
  "reviews",
  "confirm",
];

export type IntakeErrors = Record<string, string>;

export function createEmptyRequirement(): RequirementInput {
  return {
    title: "",
    description: "",
    priority: "must",
    acceptance_criteria: "",
  };
}

export function createEmptyIntake(): CreateProjectInput {
  return {
    name: "",
    code: "",
    priority: "medium",
    customer: "",
    owner_id: null,
    background: "",
    objectives: [],
    requirements: [createEmptyRequirement()],
    technical_requirements: [],
    constraints: [],
    deliverables: [],
    review_configuration: {
      requirements_review: true,
      design_review: true,
      acceptance_review: true,
      additional_reviews: [],
    },
    participants: {
      customer_contact: "",
      project_owner_employee_id: null,
      presenter_employee_id: null,
      reviewer_names: [],
      approver_names: [],
    },
    tutorial_accelerated: false,
  };
}

export function normalizeRequirements(requirements: RequirementInput[]): RequirementInput[] {
  return requirements.map((requirement, index) => ({
    ...requirement,
    code: requirement.code?.trim().toUpperCase() || `REQ-${String(index + 1).padStart(3, "0")}`,
  }));
}

export function validateIntakeStep(step: IntakeStep, intake: CreateProjectInput): IntakeErrors {
  // 返回的是 i18n key 的后缀（如 "name" → project:intake.errors.name），
  // 纯函数不持有语言状态；组件负责翻译。
  const errors: IntakeErrors = {};
  if (step === "basic" || step === "confirm") {
    if (!intake.name.trim()) errors.name = "name";
    if (!intake.code?.trim()) errors.code = "code";
    if (!intake.owner_id) errors.owner_id = "owner";
  }
  if (step === "purpose" || step === "confirm") {
    if (!intake.background?.trim()) errors.background = "background";
    if (!intake.objectives?.some((item) => item.trim())) errors.objectives = "objectives";
  }
  if (step === "requirements" || step === "confirm") {
    const requirements = intake.requirements ?? [];
    if (!requirements.some((requirement) => requirement.title.trim())) {
      errors.requirements = "requirements";
    }
    requirements.forEach((requirement, index) => {
      if (!requirement.title.trim()) {
        errors[`requirement-${index}-title`] = "requirementTitle";
      }
      if (!requirement.acceptance_criteria.trim()) {
        errors[`requirement-${index}-acceptance`] = "acceptance";
      }
    });
  }
  if (step === "delivery" || step === "confirm") {
    if (!intake.deliverables?.some((item) => item.trim())) {
      errors.deliverables = "deliverables";
    }
  }
  return errors;
}

export function hydrateIntake(value: unknown): CreateProjectInput {
  if (!value || typeof value !== "object") return createEmptyIntake();
  const candidate = value as Partial<CreateProjectInput>;
  const empty = createEmptyIntake();
  return {
    ...empty,
    ...candidate,
    review_configuration: empty.review_configuration,
    participants: { ...empty.participants!, ...candidate.participants },
    requirements: candidate.requirements?.length ? candidate.requirements : empty.requirements,
  };
}
