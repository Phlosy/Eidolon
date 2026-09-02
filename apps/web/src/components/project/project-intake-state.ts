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
  const errors: IntakeErrors = {};
  if (step === "basic" || step === "confirm") {
    if (!intake.name.trim()) errors.name = "请输入项目名称";
    if (!intake.code?.trim()) errors.code = "请输入项目编号";
    if (!intake.owner_id) errors.owner_id = "请选择项目负责人";
  }
  if (step === "purpose" || step === "confirm") {
    if (!intake.background?.trim()) errors.background = "请说明项目背景";
    if (!intake.objectives?.some((item) => item.trim())) errors.objectives = "至少添加一个目标";
  }
  if (step === "requirements" || step === "confirm") {
    const requirements = intake.requirements ?? [];
    if (!requirements.some((requirement) => requirement.title.trim())) {
      errors.requirements = "至少添加一条结构化需求";
    }
    requirements.forEach((requirement, index) => {
      if (!requirement.title.trim()) errors[`requirement-${index}-title`] = "请输入需求标题";
      if (!requirement.acceptance_criteria.trim()) {
        errors[`requirement-${index}-acceptance`] = "请输入可验证的验收标准";
      }
    });
  }
  if (step === "delivery" || step === "confirm") {
    if (!intake.deliverables?.some((item) => item.trim())) {
      errors.deliverables = "至少添加一个交付物";
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
