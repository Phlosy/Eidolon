import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  ClipboardList,
  FileCheck2,
  Plus,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { useCreateProject } from "../../hooks/useProjects";
import { useEmployees } from "../../hooks/useEmployees";
import { useClassicSnakeTemplate, usePractice } from "../../hooks/useTutorial";
import type { CreateProjectInput, RequirementInput } from "../../types";
import { cn } from "../../utils/cn";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input, Textarea } from "../common/input";
import {
  createEmptyIntake,
  createEmptyRequirement,
  hydrateIntake,
  INTAKE_STEPS,
  normalizeRequirements,
  validateIntakeStep,
  type IntakeErrors,
} from "./project-intake-state";

const DRAFT_KEY = "eidolon-project-intake-draft-v1";
const STEP_NUMBER: Record<string, string> = {
  basic: "01",
  purpose: "02",
  requirements: "03",
  delivery: "04",
  reviews: "05",
  confirm: "06",
};
const PRIORITIES = ["low", "medium", "high", "critical"] as const;
const REQUIREMENT_PRIORITIES = ["must", "should", "could"] as const;
const MANDATORY_REVIEWS = ["requirements", "design", "acceptance"] as const;
/** 存储值保持英文（后端/数据兼容），展示走 i18n。 */
const ADDITIONAL_REVIEWS = [
  { value: "Architecture Review", key: "architecture_review" },
  { value: "Prototype Review", key: "prototype_review" },
  { value: "Code Review", key: "code_review" },
  { value: "Internal Test Review", key: "internal_test_review" },
  { value: "Security Review", key: "security_review" },
  { value: "Release Review", key: "release_review" },
] as const;

const selectClass =
  "h-11 w-full rounded-md border border-border bg-transparent px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface ProjectIntakeWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function splitLines(value: string): string[] {
  return value
    .split("\n")
    .map((item) => item.trim())
    .filter(Boolean);
}

function LinesField({
  id,
  label,
  value,
  onChange,
  hint,
  error,
}: {
  id: string;
  label: string;
  value: string[];
  onChange: (value: string[]) => void;
  hint: string;
  error?: string;
}) {
  const { t } = useTranslation("project");
  return (
    <label className="block space-y-1.5" htmlFor={id}>
      <span className="text-xs font-medium">{label}</span>
      <Textarea
        id={id}
        value={value.join("\n")}
        onChange={(event) => onChange(splitLines(event.target.value))}
        placeholder={hint}
        aria-describedby={error ? `${id}-error` : undefined}
      />
      {error ? (
        <span id={`${id}-error`} className="block text-xs text-danger">
          {error}
        </span>
      ) : (
        <span className="block text-[10px] text-muted-foreground">{t("intake.perLine")}</span>
      )}
    </label>
  );
}

export function ProjectIntakeWizard({ open, onOpenChange }: ProjectIntakeWizardProps) {
  const { t } = useTranslation("project");
  const navigate = useNavigate();
  const employees = useEmployees().data ?? [];
  // Classic Snake 现在是"可选实战"，所以预填模板只看实战教程的状态
  const practice = usePractice().data;
  const practicing = practice?.progress.status === "active";
  const snakeTemplate = useClassicSnakeTemplate(open && practicing);
  const createProject = useCreateProject();
  const errorSummaryRef = useRef<HTMLDivElement>(null);
  const appliedTemplate = useRef(false);
  const [stepIndex, setStepIndex] = useState(0);
  const [intake, setIntake] = useState<CreateProjectInput>(createEmptyIntake);
  const [errors, setErrors] = useState<IntakeErrors>({});
  const [dirty, setDirty] = useState(false);
  const step = INTAKE_STEPS[stepIndex];

  useEffect(() => {
    if (!open) {
      appliedTemplate.current = false;
      return;
    }
    const stored = window.localStorage.getItem(DRAFT_KEY);
    if (!stored) return;
    try {
      setIntake(hydrateIntake(JSON.parse(stored)));
      // 有草稿说明用户之前审阅/修改过：模板不能再覆盖
      appliedTemplate.current = true;
    } catch {
      window.localStorage.removeItem(DRAFT_KEY);
    }
  }, [open]);

  useEffect(() => {
    if (!open || !dirty) return;
    window.localStorage.setItem(DRAFT_KEY, JSON.stringify(intake));
  }, [dirty, intake, open]);

  const patch = (value: Partial<CreateProjectInput>) => {
    setIntake((current) => ({ ...current, ...value }));
    setDirty(true);
    setErrors({});
  };

  const close = () => {
    if (dirty && !window.confirm(t("intake.closeConfirm"))) return;
    onOpenChange(false);
  };

  const goNext = () => {
    const nextErrors = validateIntakeStep(step, intake);
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      window.setTimeout(() => errorSummaryRef.current?.focus(), 0);
      return;
    }
    setErrors({});
    setStepIndex((index) => Math.min(index + 1, INTAKE_STEPS.length - 1));
  };

  const applySnakeTemplate = () => {
    if (!snakeTemplate.data) return;
    const template = snakeTemplate.data.intake;
    const owner = employees.find((employee) => employee.role === "ceo") ?? employees[0];
    const qa = employees.find((employee) => employee.role === "qa_engineer");
    const reviewerNames = [owner?.name, qa?.name].filter((name): name is string => Boolean(name));
    const deadline = new Date(Date.now() + 14 * 24 * 60 * 60 * 1000).toISOString();
    setIntake(
      hydrateIntake({
        ...template,
        owner_id: owner?.id ?? null,
        deadline,
        participants: {
          ...template.participants,
          customer_contact: template.participants?.customer_contact || "Eidolon Tutorial",
          project_owner_employee_id: owner?.id ?? null,
          presenter_employee_id: owner?.id ?? null,
          reviewer_names: reviewerNames,
          approver_names: owner?.name ? [owner.name] : [],
        },
      }),
    );
    setDirty(true);
  };

  // 实战教程打开向导时自动套用示例数据：用户只需要审阅，不需要自己找模板按钮。
  useEffect(() => {
    if (!open || !practicing || !snakeTemplate.data || appliedTemplate.current) return;
    appliedTemplate.current = true;
    applySnakeTemplate();
  }, [open, practicing, snakeTemplate.data, employees]);

  const updateRequirement = (index: number, value: Partial<RequirementInput>) => {
    const requirements = [...(intake.requirements ?? [])];
    requirements[index] = { ...requirements[index], ...value };
    patch({ requirements });
  };

  const summary = useMemo(
    () => ({
      requirements: intake.requirements?.length ?? 0,
      deliverables: intake.deliverables?.length ?? 0,
      reviews: 3 + (intake.review_configuration?.additional_reviews.length ?? 0),
    }),
    [intake],
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const nextErrors = validateIntakeStep("confirm", intake);
    if (Object.keys(nextErrors).length) {
      setErrors(nextErrors);
      window.setTimeout(() => errorSummaryRef.current?.focus(), 0);
      return;
    }
    createProject.mutate(
      {
        ...intake,
        requirements: normalizeRequirements(intake.requirements ?? []),
        participants: {
          ...intake.participants!,
          project_owner_employee_id: intake.owner_id ?? null,
        },
      },
      {
        onSuccess: (project) => {
          window.localStorage.removeItem(DRAFT_KEY);
          setIntake(createEmptyIntake());
          setStepIndex(0);
          setDirty(false);
          appliedTemplate.current = false;
          onOpenChange(false);
          navigate(`/projects/${project.id}`);
        },
      },
    );
  };

  const errorText = (key: string) => t(`intake.errors.${key}`);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      title={t("intake.title")}
      description={t("intake.description")}
      className="max-h-[92dvh] max-w-5xl overflow-hidden p-0"
    >
      <form onSubmit={submit} className="grid max-h-[82dvh] min-h-[620px] md:grid-cols-[230px_1fr]">
        <aside className="border-b border-border bg-background/40 p-4 md:border-b-0 md:border-r md:p-5">
          <div className="mb-4 flex items-center gap-2 text-[10px] text-muted-foreground">
            <Save className="h-3.5 w-3.5" />
            {t("intake.draftSaved")}
          </div>
          <ol
            className="grid grid-cols-3 gap-1 md:grid-cols-1 md:gap-1.5"
            aria-label={t("intake.stepsLabel")}
          >
            {INTAKE_STEPS.map((item, index) => {
              const done = index < stepIndex;
              const current = item === step;
              return (
                <li key={item}>
                  <button
                    type="button"
                    onClick={() => index < stepIndex && setStepIndex(index)}
                    disabled={index > stepIndex}
                    aria-current={current ? "step" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed",
                      current && "bg-primary/10 font-medium text-primary",
                      done && "text-foreground hover:bg-muted",
                      index > stepIndex && "text-muted-foreground opacity-55",
                    )}
                  >
                    <span
                      className={cn(
                        "flex h-6 w-6 shrink-0 items-center justify-center rounded-lg border font-mono text-[10px]",
                        current && "border-primary/40 bg-primary/15 text-primary",
                        done && "border-success/35 bg-success/10 text-success",
                        index > stepIndex && "border-border",
                      )}
                    >
                      {done ? <Check className="h-3.5 w-3.5" /> : STEP_NUMBER[item]}
                    </span>
                    <span className="truncate">{t(`intake.steps.${item}`)}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          {practicing ? (
            <Button
              type="button"
              variant="outline"
              className="mt-5 w-full"
              onClick={applySnakeTemplate}
              disabled={!snakeTemplate.data}
            >
              <Sparkles className="h-4 w-4" />
              {t("intake.reloadTemplate")}
            </Button>
          ) : null}
        </aside>

        <div className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto p-5 md:p-7">
            <div className="mb-6">
              <p className="type-kicker text-primary">
                {t("intake.stepKicker", { number: STEP_NUMBER[step] })}
              </p>
              <h3 className="mt-2 text-2xl font-semibold">{t(`intake.steps.${step}`)}</h3>
              {practicing ? (
                <p className="mt-2 rounded-xl border border-primary/20 bg-primary/5 px-3 py-2 text-[11px] leading-5 text-primary">
                  {t("intake.templateNotice")}
                </p>
              ) : null}
            </div>
            {Object.keys(errors).length ? (
              <div
                ref={errorSummaryRef}
                tabIndex={-1}
                role="alert"
                className="mb-5 rounded-xl border border-danger/25 bg-danger/7 p-4 outline-none focus:ring-2 focus:ring-danger/40"
              >
                <p className="text-sm font-semibold">{t("intake.errorTitle")}</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                  {Object.entries(errors).map(([field, key]) => (
                    <li key={field}>{errorText(key)}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {/* 教程按这一步的 DOM 打光：只有当前步骤在 DOM 里，所以指引目标唯一 */}
            <div data-tutorial-target={`intake-step-${step}`}>
              {step === "basic" ? (
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.name")} *</span>
                    <Input
                      id="intake-name"
                      value={intake.name}
                      onChange={(event) => patch({ name: event.target.value })}
                      aria-describedby={errors.name ? "intake-name-error" : undefined}
                    />
                    {errors.name ? (
                      <span id="intake-name-error" className="text-xs text-danger">
                        {errorText(errors.name)}
                      </span>
                    ) : null}
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.code")} *</span>
                    <Input
                      id="intake-code"
                      value={intake.code ?? ""}
                      onChange={(event) => patch({ code: event.target.value.toUpperCase() })}
                      placeholder="PROJECT-001"
                    />
                    {errors.code ? (
                      <span className="text-xs text-danger">{errorText(errors.code)}</span>
                    ) : null}
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.priority")}</span>
                    <select
                      className={selectClass}
                      value={intake.priority}
                      onChange={(event) => patch({ priority: event.target.value })}
                    >
                      {PRIORITIES.map((priority) => (
                        <option key={priority} value={priority}>
                          {t(`intake.priority.${priority}`)}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.customer")}</span>
                    <Input
                      value={intake.customer ?? ""}
                      onChange={(event) => patch({ customer: event.target.value })}
                    />
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.owner")} *</span>
                    <select
                      className={selectClass}
                      value={intake.owner_id ?? ""}
                      onChange={(event) => patch({ owner_id: Number(event.target.value) || null })}
                    >
                      <option value="">{t("intake.fields.ownerPlaceholder")}</option>
                      {employees.map((employee) => (
                        <option key={employee.id} value={employee.id}>
                          {employee.name} · {employee.role}
                        </option>
                      ))}
                    </select>
                    {errors.owner_id ? (
                      <span className="text-xs text-danger">{errorText(errors.owner_id)}</span>
                    ) : null}
                  </label>
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.deadline")}</span>
                    <Input
                      type="date"
                      value={intake.deadline?.slice(0, 10) ?? ""}
                      onChange={(event) =>
                        patch({
                          deadline: event.target.value ? `${event.target.value}T23:59:59Z` : null,
                        })
                      }
                    />
                  </label>
                </div>
              ) : null}

              {step === "purpose" ? (
                <div className="space-y-4">
                  <label className="block space-y-1.5">
                    <span className="text-xs font-medium">{t("intake.fields.background")} *</span>
                    <Textarea
                      value={intake.background ?? ""}
                      onChange={(event) => patch({ background: event.target.value })}
                      placeholder={t("intake.fields.backgroundPlaceholder")}
                    />
                    {errors.background ? (
                      <span className="text-xs text-danger">{errorText(errors.background)}</span>
                    ) : null}
                  </label>
                  <LinesField
                    id="project-objectives"
                    label={`${t("intake.fields.objectives")} *`}
                    value={intake.objectives ?? []}
                    onChange={(objectives) => patch({ objectives })}
                    hint={t("intake.fields.objectivesHint")}
                    error={errors.objectives ? errorText(errors.objectives) : undefined}
                  />
                </div>
              ) : null}

              {step === "requirements" ? (
                <div className="space-y-3">
                  {(intake.requirements ?? []).map((requirement, index) => (
                    <article
                      key={index}
                      className="rounded-2xl border border-border bg-background/30 p-4"
                    >
                      <div className="mb-3 flex items-center justify-between">
                        <span className="type-telemetry text-xs text-primary">
                          {requirement.code || `REQ-${String(index + 1).padStart(3, "0")}`}
                        </span>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          aria-label={t("intake.removeRequirement")}
                          disabled={(intake.requirements?.length ?? 0) === 1}
                          onClick={() =>
                            patch({
                              requirements: intake.requirements?.filter(
                                (_, itemIndex) => itemIndex !== index,
                              ),
                            })
                          }
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                      <div className="grid gap-3 sm:grid-cols-[1fr_150px]">
                        <label className="space-y-1">
                          <span className="text-[10px] text-muted-foreground">
                            {t("intake.fields.requirementTitle")} *
                          </span>
                          <Input
                            value={requirement.title}
                            onChange={(event) =>
                              updateRequirement(index, { title: event.target.value })
                            }
                          />
                        </label>
                        <label className="space-y-1">
                          <span className="text-[10px] text-muted-foreground">
                            {t("intake.fields.requirementPriority")}
                          </span>
                          <select
                            className={selectClass}
                            value={requirement.priority}
                            onChange={(event) =>
                              updateRequirement(index, { priority: event.target.value })
                            }
                          >
                            {REQUIREMENT_PRIORITIES.map((priority) => (
                              <option key={priority} value={priority}>
                                {t(`intake.requirementPriority.${priority}`)}
                              </option>
                            ))}
                          </select>
                        </label>
                      </div>
                      <label className="mt-3 block space-y-1">
                        <span className="text-[10px] text-muted-foreground">
                          {t("intake.fields.requirementDescription")}
                        </span>
                        <Textarea
                          className="min-h-20"
                          value={requirement.description}
                          onChange={(event) =>
                            updateRequirement(index, { description: event.target.value })
                          }
                        />
                      </label>
                      <label className="mt-3 block space-y-1">
                        <span className="text-[10px] text-muted-foreground">
                          {t("intake.fields.acceptanceCriteria")} *
                        </span>
                        <Textarea
                          className="min-h-20"
                          value={requirement.acceptance_criteria}
                          onChange={(event) =>
                            updateRequirement(index, { acceptance_criteria: event.target.value })
                          }
                        />
                      </label>
                    </article>
                  ))}
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() =>
                      patch({
                        requirements: [...(intake.requirements ?? []), createEmptyRequirement()],
                      })
                    }
                  >
                    <Plus className="h-4 w-4" />
                    {t("intake.addRequirement")}
                  </Button>
                </div>
              ) : null}

              {step === "delivery" ? (
                <div className="grid gap-5 md:grid-cols-2">
                  <LinesField
                    id="technical-requirements"
                    label={t("intake.fields.technicalRequirements")}
                    value={intake.technical_requirements ?? []}
                    onChange={(technical_requirements) => patch({ technical_requirements })}
                    hint={t("intake.fields.technicalRequirementsHint")}
                  />
                  <LinesField
                    id="constraints"
                    label={t("intake.fields.constraints")}
                    value={intake.constraints ?? []}
                    onChange={(constraints) => patch({ constraints })}
                    hint={t("intake.fields.constraintsHint")}
                  />
                  <LinesField
                    id="deliverables"
                    label={`${t("intake.fields.deliverables")} *`}
                    value={intake.deliverables ?? []}
                    onChange={(deliverables) => patch({ deliverables })}
                    hint={t("intake.fields.deliverablesHint")}
                    error={errors.deliverables ? errorText(errors.deliverables) : undefined}
                  />
                  <LinesField
                    id="milestones"
                    label={t("intake.fields.milestones")}
                    value={(intake.milestones ?? []).map((item) => String(item.name ?? ""))}
                    onChange={(items) => patch({ milestones: items.map((name) => ({ name })) })}
                    hint={t("intake.fields.milestonesHint")}
                  />
                </div>
              ) : null}

              {step === "reviews" ? (
                <div className="space-y-5">
                  <fieldset>
                    <legend className="text-xs font-medium">{t("intake.reviews.mandatory")}</legend>
                    <div className="mt-2 grid gap-2 sm:grid-cols-3">
                      {MANDATORY_REVIEWS.map((key) => (
                        <label
                          key={key}
                          className="flex items-center gap-2 rounded-xl border border-success/20 bg-success/6 px-3 py-3 text-xs"
                        >
                          <input type="checkbox" checked disabled />
                          <Check className="h-3.5 w-3.5 text-success" />
                          {t(`intake.reviews.${key}`)}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                  <fieldset>
                    <legend className="text-xs font-medium">
                      {t("intake.reviews.additional")}
                    </legend>
                    <div className="mt-2 grid gap-2 sm:grid-cols-2">
                      {ADDITIONAL_REVIEWS.map(({ value, key }) => {
                        const selected =
                          intake.review_configuration?.additional_reviews.includes(value) ?? false;
                        return (
                          <label
                            key={value}
                            className="flex cursor-pointer items-center gap-2 rounded-xl border border-border px-3 py-2 text-xs hover:bg-muted"
                          >
                            <input
                              type="checkbox"
                              checked={selected}
                              onChange={() =>
                                patch({
                                  review_configuration: {
                                    ...intake.review_configuration!,
                                    additional_reviews: selected
                                      ? intake.review_configuration!.additional_reviews.filter(
                                          (item) => item !== value,
                                        )
                                      : [...intake.review_configuration!.additional_reviews, value],
                                  },
                                })
                              }
                            />
                            {t(`intake.reviews.additionalLabels.${key}`)}
                          </label>
                        );
                      })}
                    </div>
                  </fieldset>
                  <div className="grid gap-4 sm:grid-cols-2">
                    <label className="space-y-1.5">
                      <span className="text-xs font-medium">
                        {t("intake.fields.customerContact")}
                      </span>
                      <Input
                        value={intake.participants?.customer_contact ?? ""}
                        onChange={(event) =>
                          patch({
                            participants: {
                              ...intake.participants!,
                              customer_contact: event.target.value,
                            },
                          })
                        }
                      />
                    </label>
                    <label className="space-y-1.5">
                      <span className="text-xs font-medium">{t("intake.fields.presenter")}</span>
                      <select
                        className={selectClass}
                        value={intake.participants?.presenter_employee_id ?? ""}
                        onChange={(event) =>
                          patch({
                            participants: {
                              ...intake.participants!,
                              presenter_employee_id: Number(event.target.value) || null,
                            },
                          })
                        }
                      >
                        <option value="">{t("intake.fields.presenterPlaceholder")}</option>
                        {employees.map((employee) => (
                          <option key={employee.id} value={employee.id}>
                            {employee.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  </div>
                </div>
              ) : null}

              {step === "confirm" ? (
                <div className="space-y-5">
                  <div className="grid gap-3 sm:grid-cols-3">
                    {[
                      [ClipboardList, t("intake.summary.requirements"), summary.requirements],
                      [FileCheck2, t("intake.summary.deliverables"), summary.deliverables],
                      [Check, t("intake.summary.reviewGates"), summary.reviews],
                    ].map(([Icon, label, value]) => {
                      const SummaryIcon = Icon as typeof ClipboardList;
                      return (
                        <div
                          key={String(label)}
                          className="rounded-2xl border border-border bg-background/30 p-4"
                        >
                          <SummaryIcon className="h-4 w-4 text-primary" />
                          <p className="type-telemetry mt-4 text-2xl font-semibold">
                            {String(value)}
                          </p>
                          <p className="text-[10px] text-muted-foreground">{String(label)}</p>
                        </div>
                      );
                    })}
                  </div>
                  <article className="rounded-2xl border border-border p-5">
                    <p className="type-kicker text-primary">{t("intake.summary.charter")}</p>
                    <h4 className="mt-3 text-xl font-semibold">{intake.name}</h4>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {intake.code} · {intake.customer || t("intake.summary.internal")} ·{" "}
                      {t(`intake.priority.${intake.priority ?? "medium"}`)}
                    </p>
                    <p className="mt-4 text-sm leading-6">{intake.background}</p>
                    <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                      {intake.objectives?.map((objective) => (
                        <li key={objective}>{objective}</li>
                      ))}
                    </ul>
                  </article>
                  <p className="text-xs leading-5 text-muted-foreground">
                    {t("intake.summary.note")}
                  </p>
                </div>
              ) : null}
            </div>
          </div>

          <footer className="flex items-center justify-between gap-3 border-t border-border bg-card/95 px-5 py-4 md:px-7">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setStepIndex((index) => Math.max(0, index - 1))}
              disabled={stepIndex === 0}
            >
              <ArrowLeft className="h-4 w-4" />
              {t("intake.actions.previous")}
            </Button>
            <div className="flex items-center gap-2">
              <Button type="button" variant="outline" onClick={close}>
                {t("intake.actions.later")}
              </Button>
              {step === "confirm" ? (
                <Button type="submit" disabled={createProject.isPending}>
                  {createProject.isPending
                    ? t("intake.actions.creating")
                    : t("intake.actions.create")}
                  <Check className="h-4 w-4" />
                </Button>
              ) : (
                <Button type="button" onClick={goNext} data-tutorial-target="intake-next">
                  {t("intake.actions.next")}
                  <ArrowRight className="h-4 w-4" />
                </Button>
              )}
            </div>
          </footer>
        </div>
      </form>
    </Dialog>
  );
}
