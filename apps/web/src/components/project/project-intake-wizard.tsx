import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
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
import { useClassicSnakeTemplate, useTutorial } from "../../hooks/useTutorial";
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

const stepMeta = {
  basic: ["01", "基本信息"],
  purpose: ["02", "背景与目标"],
  requirements: ["03", "结构化需求"],
  delivery: ["04", "技术与交付"],
  reviews: ["05", "评审与参与者"],
  confirm: ["06", "确认立项"],
} as const;

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
        <span className="block text-[10px] text-muted-foreground">每行一项</span>
      )}
    </label>
  );
}

export function ProjectIntakeWizard({ open, onOpenChange }: ProjectIntakeWizardProps) {
  const navigate = useNavigate();
  const employees = useEmployees().data ?? [];
  const tutorial = useTutorial().data;
  const snakeTemplate = useClassicSnakeTemplate(open && tutorial?.status === "active");
  const createProject = useCreateProject();
  const errorSummaryRef = useRef<HTMLDivElement>(null);
  const [stepIndex, setStepIndex] = useState(0);
  const [intake, setIntake] = useState<CreateProjectInput>(createEmptyIntake);
  const [errors, setErrors] = useState<IntakeErrors>({});
  const [dirty, setDirty] = useState(false);
  const step = INTAKE_STEPS[stepIndex];

  useEffect(() => {
    if (!open) return;
    const stored = window.localStorage.getItem(DRAFT_KEY);
    if (stored) {
      try {
        setIntake(hydrateIntake(JSON.parse(stored)));
      } catch {
        window.localStorage.removeItem(DRAFT_KEY);
      }
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
    if (dirty && !window.confirm("项目草稿已自动保存。确定关闭向导吗？")) return;
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
    const owner = employees.find((employee) => employee.role === "ceo") ?? employees[0];
    const template = snakeTemplate.data.intake;
    setIntake(
      hydrateIntake({
        ...template,
        owner_id: owner?.id ?? null,
        participants: {
          ...template.participants,
          project_owner_employee_id: owner?.id ?? null,
          presenter_employee_id: owner?.id ?? null,
        },
      }),
    );
    setDirty(true);
  };

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
          onOpenChange(false);
          navigate(`/projects/${project.id}`);
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      title="Structured Project Intake"
      description="用正式立项信息创建项目、生命周期、评审计划与 Project Charter。"
      className="max-h-[92dvh] max-w-5xl overflow-hidden p-0"
    >
      <form onSubmit={submit} className="grid max-h-[82dvh] min-h-[620px] md:grid-cols-[220px_1fr]">
        <aside className="border-b border-border bg-background/35 p-4 md:border-b-0 md:border-r md:p-5">
          <div className="mb-4 flex items-center gap-2 text-[10px] text-muted-foreground">
            <Save className="h-3.5 w-3.5" />
            草稿自动保存
          </div>
          <ol className="grid grid-cols-3 gap-1 md:grid-cols-1 md:gap-2" aria-label="立项步骤">
            {INTAKE_STEPS.map((item, index) => {
              const [number, label] = stepMeta[item];
              return (
                <li key={item}>
                  <button
                    type="button"
                    onClick={() => index < stepIndex && setStepIndex(index)}
                    disabled={index > stepIndex}
                    aria-current={item === step ? "step" : undefined}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-xl px-2 py-2 text-left text-xs transition-colors disabled:cursor-not-allowed",
                      item === step && "bg-primary/10 text-primary",
                      index < stepIndex && "text-foreground hover:bg-muted",
                      index > stepIndex && "text-muted-foreground opacity-55",
                    )}
                  >
                    <span className="type-telemetry hidden text-[9px] sm:inline">
                      {index < stepIndex ? <Check className="h-3.5 w-3.5" /> : number}
                    </span>
                    <span className="truncate">{label}</span>
                  </button>
                </li>
              );
            })}
          </ol>
          {tutorial?.status === "active" ? (
            <Button
              type="button"
              variant="outline"
              className="mt-5 w-full"
              onClick={applySnakeTemplate}
              disabled={!snakeTemplate.data}
            >
              <Sparkles className="h-4 w-4" />
              载入教学项目
            </Button>
          ) : null}
        </aside>

        <div className="flex min-h-0 flex-col">
          <div className="min-h-0 flex-1 overflow-y-auto p-5 md:p-7">
            <div className="mb-6">
              <p className="type-kicker text-primary">STEP {stepMeta[step][0]}</p>
              <h3 className="mt-2 text-2xl font-semibold">{stepMeta[step][1]}</h3>
            </div>
            {Object.keys(errors).length ? (
              <div
                ref={errorSummaryRef}
                tabIndex={-1}
                role="alert"
                className="mb-5 rounded-xl border border-danger/25 bg-danger/7 p-4 outline-none focus:ring-2 focus:ring-danger/40"
              >
                <p className="text-sm font-semibold">请完成以下信息</p>
                <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                  {Object.values(errors).map((error) => (
                    <li key={error}>{error}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {step === "basic" ? (
              <div className="grid gap-4 sm:grid-cols-2">
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Project Name *</span>
                  <Input
                    id="intake-name"
                    value={intake.name}
                    onChange={(event) => patch({ name: event.target.value })}
                    aria-describedby={errors.name ? "intake-name-error" : undefined}
                  />
                  {errors.name ? (
                    <span id="intake-name-error" className="text-xs text-danger">
                      {errors.name}
                    </span>
                  ) : null}
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Project Code *</span>
                  <Input
                    id="intake-code"
                    value={intake.code ?? ""}
                    onChange={(event) => patch({ code: event.target.value.toUpperCase() })}
                    placeholder="PROJECT-001"
                  />
                  {errors.code ? <span className="text-xs text-danger">{errors.code}</span> : null}
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Priority</span>
                  <select
                    className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
                    value={intake.priority}
                    onChange={(event) => patch({ priority: event.target.value })}
                  >
                    <option value="low">Low</option>
                    <option value="medium">Medium</option>
                    <option value="high">High</option>
                    <option value="critical">Critical</option>
                  </select>
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Customer</span>
                  <Input
                    value={intake.customer ?? ""}
                    onChange={(event) => patch({ customer: event.target.value })}
                  />
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Project Owner *</span>
                  <select
                    className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
                    value={intake.owner_id ?? ""}
                    onChange={(event) => patch({ owner_id: Number(event.target.value) || null })}
                  >
                    <option value="">选择负责人</option>
                    {employees.map((employee) => (
                      <option key={employee.id} value={employee.id}>
                        {employee.name} · {employee.role}
                      </option>
                    ))}
                  </select>
                  {errors.owner_id ? (
                    <span className="text-xs text-danger">{errors.owner_id}</span>
                  ) : null}
                </label>
                <label className="space-y-1.5">
                  <span className="text-xs font-medium">Deadline</span>
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
                  <span className="text-xs font-medium">Project Background *</span>
                  <Textarea
                    value={intake.background ?? ""}
                    onChange={(event) => patch({ background: event.target.value })}
                    placeholder="为什么要做这个项目？客户当前面临什么问题？"
                  />
                  {errors.background ? (
                    <span className="text-xs text-danger">{errors.background}</span>
                  ) : null}
                </label>
                <LinesField
                  id="project-objectives"
                  label="Project Objectives *"
                  value={intake.objectives ?? []}
                  onChange={(objectives) => patch({ objectives })}
                  hint="交付一个可快速运行的 Web 应用"
                  error={errors.objectives}
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
                        aria-label="删除需求"
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
                        <span className="text-[10px] text-muted-foreground">Title *</span>
                        <Input
                          value={requirement.title}
                          onChange={(event) =>
                            updateRequirement(index, { title: event.target.value })
                          }
                        />
                      </label>
                      <label className="space-y-1">
                        <span className="text-[10px] text-muted-foreground">Priority</span>
                        <select
                          className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
                          value={requirement.priority}
                          onChange={(event) =>
                            updateRequirement(index, { priority: event.target.value })
                          }
                        >
                          <option value="must">Must</option>
                          <option value="should">Should</option>
                          <option value="could">Could</option>
                        </select>
                      </label>
                    </div>
                    <label className="mt-3 block space-y-1">
                      <span className="text-[10px] text-muted-foreground">Description</span>
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
                        Acceptance Criteria *
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
                  添加 Requirement
                </Button>
              </div>
            ) : null}

            {step === "delivery" ? (
              <div className="grid gap-5 md:grid-cols-2">
                <LinesField
                  id="technical-requirements"
                  label="Technical Requirements"
                  value={intake.technical_requirements ?? []}
                  onChange={(technical_requirements) => patch({ technical_requirements })}
                  hint="React&#10;响应时间 < 200ms"
                />
                <LinesField
                  id="constraints"
                  label="Constraints"
                  value={intake.constraints ?? []}
                  onChange={(constraints) => patch({ constraints })}
                  hint="必须离线部署&#10;禁止云服务依赖"
                />
                <LinesField
                  id="deliverables"
                  label="Deliverables *"
                  value={intake.deliverables ?? []}
                  onChange={(deliverables) => patch({ deliverables })}
                  hint="Source Code&#10;Production Build&#10;User Manual"
                  error={errors.deliverables}
                />
                <LinesField
                  id="milestones"
                  label="Milestones"
                  value={(intake.milestones ?? []).map((item) => String(item.name ?? ""))}
                  onChange={(items) => patch({ milestones: items.map((name) => ({ name })) })}
                  hint="Requirements Baseline&#10;Design Baseline&#10;Final Delivery"
                />
              </div>
            ) : null}

            {step === "reviews" ? (
              <div className="space-y-5">
                <fieldset>
                  <legend className="text-xs font-medium">Mandatory Reviews</legend>
                  <div className="mt-2 grid gap-2 sm:grid-cols-3">
                    {["Requirements Review", "System Design Review", "Acceptance Review"].map(
                      (label) => (
                        <label
                          key={label}
                          className="flex items-center gap-2 rounded-xl border border-success/20 bg-success/6 px-3 py-3 text-xs"
                        >
                          <input type="checkbox" checked disabled />
                          <Check className="h-3.5 w-3.5 text-success" />
                          {label}
                        </label>
                      ),
                    )}
                  </div>
                </fieldset>
                <fieldset>
                  <legend className="text-xs font-medium">Additional Reviews</legend>
                  <div className="mt-2 grid gap-2 sm:grid-cols-2">
                    {[
                      "Architecture Review",
                      "Prototype Review",
                      "Code Review",
                      "Internal Test Review",
                      "Security Review",
                      "Release Review",
                    ].map((label) => {
                      const selected =
                        intake.review_configuration?.additional_reviews.includes(label) ?? false;
                      return (
                        <label
                          key={label}
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
                                        (item) => item !== label,
                                      )
                                    : [...intake.review_configuration!.additional_reviews, label],
                                },
                              })
                            }
                          />
                          {label}
                        </label>
                      );
                    })}
                  </div>
                </fieldset>
                <div className="grid gap-4 sm:grid-cols-2">
                  <label className="space-y-1.5">
                    <span className="text-xs font-medium">Customer Contact</span>
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
                    <span className="text-xs font-medium">Default Presenter</span>
                    <select
                      className="h-11 w-full rounded-md border border-border bg-background px-3 text-sm"
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
                      <option value="">跟随项目负责人</option>
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
                    [ClipboardList, "Requirements", summary.requirements],
                    [FileCheck2, "Deliverables", summary.deliverables],
                    [Check, "Review Gates", summary.reviews],
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
                  <p className="type-kicker text-primary">PROJECT CHARTER PREVIEW</p>
                  <h4 className="mt-3 text-xl font-semibold">{intake.name}</h4>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {intake.code} · {intake.customer || "Internal"} · {intake.priority}
                  </p>
                  <p className="mt-4 text-sm leading-6">{intake.background}</p>
                  <ul className="mt-4 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
                    {intake.objectives?.map((objective) => (
                      <li key={objective}>{objective}</li>
                    ))}
                  </ul>
                </article>
                <p className="text-xs leading-5 text-muted-foreground">
                  提交后会创建正式 Project、11 个 ProjectPhase、三个不可关闭的用户评审门，并生成
                  Project Charter Markdown 源与 DOCX 正式文档。
                </p>
              </div>
            ) : null}
          </div>

          <footer className="flex items-center justify-between gap-3 border-t border-border bg-card/95 px-5 py-4 md:px-7">
            <Button
              type="button"
              variant="ghost"
              onClick={() => setStepIndex((index) => Math.max(0, index - 1))}
              disabled={stepIndex === 0}
            >
              <ArrowLeft className="h-4 w-4" />
              上一步
            </Button>
            <div className="flex items-center gap-2">
              <Button type="button" variant="outline" onClick={close}>
                稍后继续
              </Button>
              {step === "confirm" ? (
                <Button type="submit" disabled={createProject.isPending}>
                  {createProject.isPending ? "正在建立项目…" : "创建正式项目"}
                  <Check className="h-4 w-4" />
                </Button>
              ) : (
                <Button type="button" onClick={goNext}>
                  下一步
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
