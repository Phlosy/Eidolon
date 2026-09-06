import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  useOnboardEmployee,
  useAccessPackages,
  usePositions,
  useProvisioningPreview,
} from "../../hooks/useLifecycle";
import { useCompany } from "../../hooks/useSystem";
import { useEmployees } from "../../hooks/useEmployees";
import { useRuntimeTypes } from "../../hooks/useRuntimes";
import { useProviders } from "../../hooks/useProviders";
import { listProviderModels } from "../../api/providers";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { Input } from "../common/input";
import { Badge } from "../common/badge";
import { Skeleton } from "../common/skeleton";
import { BehaviorPreview } from "./behavior-preview";
import { ProvisioningPreviewList } from "./provisioning-preview";
import { ProviderPresetFields } from "../provider/provider-preset-fields";
import { ModelEntriesEditor } from "../provider/model-entries-editor";
import {
  canAdvanceHire,
  defaultPackageIds,
  deriveSlug,
  HIRE_WIZARD_STEPS,
  INITIAL_HIRE_STATE,
  type HireWizardState,
  type HireWizardStep,
} from "./hire-wizard-steps";
import { cn } from "../../utils/cn";
import { enumLabel } from "../../utils/labels";
import type { EmployeeRole, OnboardEmployeeInput, RuntimeType } from "../../types";

const STEP_TITLE_KEY: Record<HireWizardStep, string> = {
  identity: "wizard.steps.identity",
  department: "wizard.steps.department",
  manager: "wizard.steps.manager",
  runtime: "wizard.steps.runtime",
  provider: "wizard.steps.provider",
  brain: "wizard.steps.brain",
  packages: "wizard.steps.packages",
  preview: "wizard.steps.preview",
  hire: "wizard.steps.hire",
};

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";

interface HireWizardProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  presetRole?: EmployeeRole;
  presetManagerEmployeeId?: number | null;
}

/** Guided hire wizard backed by the real lifecycle, runtime, provider and brain domains. */
export function HireWizard({
  open,
  onOpenChange,
  presetRole,
  presetManagerEmployeeId,
}: HireWizardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [stepIndex, setStepIndex] = useState(0);
  const [state, setState] = useState<HireWizardState>(INITIAL_HIRE_STATE);
  const [packagesInitialized, setPackagesInitialized] = useState(false);

  const companyQuery = useCompany();
  const positionsQuery = usePositions(state.departmentId);
  const employeesQuery = useEmployees();
  const runtimeTypesQuery = useRuntimeTypes();
  const providersQuery = useProviders();
  const packagesQuery = useAccessPackages();
  const onboard = useOnboardEmployee();

  const step = HIRE_WIZARD_STEPS[stepIndex];
  const departments = companyQuery.data?.departments ?? [];
  const positions = positionsQuery.data ?? [];
  const packages = packagesQuery.data ?? [];
  const runtimeTypes = (runtimeTypesQuery.data ?? []).filter((rt) => rt.implemented);
  const companyProviders = providersQuery.data ?? [];

  useEffect(() => {
    if (!open || !presetRole || departments.length === 0) return;
    const departmentSlug =
      presetRole === "ceo" ? "executive" : presetRole === "qa_engineer" ? "qa" : "engineering";
    const department = departments.find((item) => item.slug === departmentSlug);
    setState((current) => ({
      ...current,
      role: presetRole,
      title:
        current.title ||
        (presetRole === "ceo"
          ? "Chief Executive Officer"
          : presetRole === "qa_engineer"
            ? "QA Engineer"
            : "Software Engineer"),
      departmentId: current.departmentId ?? department?.id ?? null,
      managerEmployeeId: presetManagerEmployeeId ?? current.managerEmployeeId,
      personality:
        presetRole === "ceo"
          ? "果断、全局视角、主动澄清风险"
          : presetRole === "qa_engineer"
            ? "严谨、挑剔、关注边界条件"
            : "务实、结构化、重视可维护性",
      goals:
        presetRole === "ceo"
          ? "确保公司持续交付客户价值，并协调关键决策"
          : presetRole === "qa_engineer"
            ? "守住交付质量底线，并沉淀可复用测试知识"
            : "交付可靠实现，并持续积累工程知识",
    }));
  }, [departments, open, presetManagerEmployeeId, presetRole]);

  // Role-default packages are pre-checked once the package list first loads.
  useEffect(() => {
    if (!packagesInitialized && packages.length > 0) {
      setState((s) => ({ ...s, packageIds: defaultPackageIds(packages, s.role) }));
      setPackagesInitialized(true);
    }
  }, [packagesInitialized, packages]);

  const previewInput = useMemo(
    () =>
      step === "preview" && state.departmentId != null
        ? {
            department_id: state.departmentId,
            ...(state.positionId != null ? { position_id: state.positionId } : {}),
            ...(state.packageIds.length > 0 ? { access_package_ids: state.packageIds } : {}),
          }
        : null,
    [step, state.departmentId, state.positionId, state.packageIds],
  );
  const previewQuery = useProvisioningPreview(previewInput);

  const advanceable = canAdvanceHire(step, state);

  const close = () => {
    onOpenChange(false);
    setStepIndex(0);
    setState(INITIAL_HIRE_STATE);
    setPackagesInitialized(false);
    onboard.reset();
  };

  const patch = (partial: Partial<HireWizardState>) => setState((s) => ({ ...s, ...partial }));

  // 已保存 provider 的探测：key 在后端 SecretStore，前端只拿模型清单
  const probeSavedProvider = async (providerId: number) => {
    try {
      const result = await listProviderModels(providerId);
      return { ok: true, error: null, models: result.models };
    } catch (error) {
      return {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
        models: [],
      };
    }
  };

  const submit = () => {
    if (state.departmentId == null || state.runtimeType == null) return;
    const input: OnboardEmployeeInput = {
      name: state.name.trim(),
      ...(state.slug.trim() ? { slug: state.slug.trim() } : {}),
      title: state.title.trim(),
      role: state.role,
      department_id: state.departmentId,
      ...(state.positionId != null ? { position_id: state.positionId } : {}),
      ...(state.managerEmployeeId != null ? { manager_employee_id: state.managerEmployeeId } : {}),
      runtime_type: state.runtimeType,
      ...(state.providerMode === "existing" && state.providerId != null
        ? { provider_id: state.providerId }
        : {}),
      ...(state.providerMode === "new"
        ? {
            provider_name: state.providerName.trim(),
            provider_type: state.providerType,
            ...(state.providerBaseUrl.trim()
              ? { provider_base_url: state.providerBaseUrl.trim() }
              : {}),
            ...(state.providerApiKey ? { provider_api_key: state.providerApiKey } : {}),
          }
        : {}),
      ...(state.providerMode !== "none"
        ? {
            model: state.model.trim(),
            models: state.providerEntries
              .filter((entry) => entry.enabled && entry.model.trim())
              .map((entry) => ({ model: entry.model.trim(), alias: entry.alias.trim() })),
          }
        : {}),
      personality: state.personality.trim(),
      goals: state.goals.trim(),
      learning_enabled: state.learningEnabled,
      curiosity: state.curiosity,
      ...(state.packageIds.length > 0 ? { access_package_ids: state.packageIds } : {}),
    };
    onboard.mutate(input, {
      onSuccess: (result) => {
        close();
        // The detail page shows the job panel live (see EmployeeDetailPage).
        navigate(`/employees/${result.employee.id}`, { state: { jobId: result.job.id } });
      },
    });
  };

  const togglePackage = (id: number) =>
    setState((s) => ({
      ...s,
      packageIds: s.packageIds.includes(id)
        ? s.packageIds.filter((p) => p !== id)
        : [...s.packageIds, id],
    }));

  const managerName =
    state.managerEmployeeId != null
      ? (employeesQuery.data?.find((e) => e.id === state.managerEmployeeId)?.name ?? "—")
      : "—";

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      title={t("lifecycle:wizard.title")}
      description={t("lifecycle:wizard.stepOf", {
        current: stepIndex + 1,
        total: HIRE_WIZARD_STEPS.length,
        step: t(`lifecycle:${STEP_TITLE_KEY[step]}`),
      })}
      className="max-w-2xl"
    >
      {step === "identity" ? (
        <div className="space-y-3" data-tutorial-target="wizard-identity">
          <Input
            data-testid="hire-name"
            placeholder={t("lifecycle:wizard.namePlaceholder")}
            value={state.name}
            onChange={(e) =>
              setState((s) => ({
                ...s,
                name: e.target.value,
                slug: s.slugTouched ? s.slug : deriveSlug(e.target.value),
              }))
            }
          />
          <div>
            <Input
              data-testid="hire-slug"
              placeholder={t("lifecycle:wizard.slugPlaceholder")}
              value={state.slug}
              onChange={(e) => patch({ slug: e.target.value, slugTouched: true })}
            />
            <p className="mt-1 text-[11px] text-muted-foreground">
              {t("lifecycle:wizard.slugHint")}
            </p>
          </div>
          <Input
            data-testid="hire-title"
            placeholder={t("lifecycle:wizard.titlePlaceholder")}
            value={state.title}
            onChange={(e) => patch({ title: e.target.value })}
          />
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              {t("lifecycle:wizard.roleLabel")}
            </span>
            <select
              data-testid="hire-role"
              className={selectClass}
              value={state.role}
              onChange={(e) => {
                const role = e.target.value as HireWizardState["role"];
                setState((s) => ({ ...s, role, packageIds: defaultPackageIds(packages, role) }));
              }}
            >
              {(["ceo", "product_manager", "researcher", "engineer", "qa_engineer"] as const).map(
                (role) => (
                  <option key={role} value={role}>
                    {enumLabel(t, "employee:role", role)}
                  </option>
                ),
              )}
            </select>
          </label>
        </div>
      ) : null}

      {step === "department" ? (
        <div className="space-y-3">
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              {t("lifecycle:wizard.departmentLabel")}
            </span>
            <select
              data-testid="hire-department"
              className={selectClass}
              value={state.departmentId ?? ""}
              onChange={(e) =>
                patch({
                  departmentId: e.target.value === "" ? null : Number(e.target.value),
                  positionId: null,
                })
              }
            >
              <option value="" disabled>
                —
              </option>
              {departments.map((dept) => (
                <option key={dept.id} value={dept.id}>
                  {dept.name}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              {t("lifecycle:wizard.positionLabel")}
            </span>
            <select
              data-testid="hire-position"
              className={selectClass}
              value={state.positionId ?? ""}
              disabled={state.departmentId == null || positionsQuery.isLoading}
              onChange={(e) =>
                patch({ positionId: e.target.value === "" ? null : Number(e.target.value) })
              }
            >
              <option value="">{t("lifecycle:wizard.noPosition")}</option>
              {positions.map((position) => (
                <option key={position.id} value={position.id}>
                  {position.title}
                  {position.level ? ` · ${position.level}` : ""}
                </option>
              ))}
            </select>
          </label>
        </div>
      ) : null}

      {step === "manager" ? (
        <div className="space-y-2">
          <label className="block">
            <span className="mb-1 block text-xs text-muted-foreground">
              {t("lifecycle:wizard.managerLabel")}
            </span>
            <select
              data-testid="hire-manager"
              className={selectClass}
              value={state.managerEmployeeId ?? ""}
              onChange={(e) =>
                patch({ managerEmployeeId: e.target.value === "" ? null : Number(e.target.value) })
              }
            >
              <option value="">{t("lifecycle:wizard.noManager")}</option>
              {(employeesQuery.data ?? []).map((employee) => (
                <option key={employee.id} value={employee.id}>
                  {employee.name}
                </option>
              ))}
            </select>
          </label>
          <p className="text-[11px] text-muted-foreground">{t("lifecycle:wizard.managerHint")}</p>
        </div>
      ) : null}

      {step === "runtime" ? (
        <div className="space-y-2" data-tutorial-target="wizard-runtime">
          <p className="text-xs text-muted-foreground">{t("lifecycle:wizard.runtimeHint")}</p>
          {(runtimeTypes.length > 0 ? runtimeTypes : [{ type: "mock" as RuntimeType }]).map(
            (typeInfo) => (
              <button
                key={typeInfo.type}
                type="button"
                aria-pressed={state.runtimeType === typeInfo.type}
                onClick={() => patch({ runtimeType: typeInfo.type })}
                className={cn(
                  "w-full rounded-md border border-border px-3 py-2 text-left text-sm transition-colors",
                  state.runtimeType === typeInfo.type
                    ? "border-foreground/40 bg-muted"
                    : "hover:bg-muted/50",
                )}
              >
                <span className="font-medium">{enumLabel(t, "runtime:type", typeInfo.type)}</span>
              </button>
            ),
          )}
        </div>
      ) : null}

      {step === "provider" ? (
        <div className="space-y-4" data-tutorial-target="wizard-provider">
          <p className="text-xs leading-relaxed text-muted-foreground">
            {t("lifecycle:wizard.providerHint")}
          </p>
          <div className="grid grid-cols-3 gap-2">
            {(["existing", "new", "none"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                aria-pressed={state.providerMode === mode}
                onClick={() => patch({ providerMode: mode })}
                className={cn(
                  "rounded-md border px-3 py-2 text-xs font-medium transition-colors",
                  state.providerMode === mode
                    ? "border-foreground/40 bg-muted text-foreground"
                    : "border-border text-muted-foreground hover:bg-muted/50",
                )}
              >
                {t(`lifecycle:wizard.providerModes.${mode}`)}
              </button>
            ))}
          </div>

          {state.providerMode === "existing" ? (
            <div className="space-y-3">
              <label className="block">
                <span className="mb-1 block text-xs text-muted-foreground">
                  {t("lifecycle:wizard.providerLabel")}
                </span>
                <select
                  className={selectClass}
                  value={state.providerId ?? ""}
                  onChange={(event) =>
                    patch({ providerId: event.target.value ? Number(event.target.value) : null })
                  }
                >
                  <option value="" disabled>
                    {providersQuery.isLoading
                      ? t("common:loading")
                      : t("lifecycle:wizard.selectProvider")}
                  </option>
                  {companyProviders.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name} · {provider.provider_type}
                    </option>
                  ))}
                </select>
              </label>
              {companyProviders.length === 0 && !providersQuery.isLoading ? (
                <p className="rounded-md border border-dashed border-border p-3 text-xs text-muted-foreground">
                  {t("lifecycle:wizard.noCompanyProvider")}
                </p>
              ) : null}
              {state.providerId != null ? (
                <ModelEntriesEditor
                  value={{ entries: state.providerEntries, primaryModel: state.model }}
                  onChange={(next) =>
                    patch({
                      ...(next.entries !== undefined ? { providerEntries: next.entries } : {}),
                      ...(next.primaryModel !== undefined ? { model: next.primaryModel } : {}),
                    })
                  }
                  probe={() => probeSavedProvider(state.providerId!)}
                  autoProbeSignature={`saved-${state.providerId}`}
                />
              ) : null}
            </div>
          ) : null}

          {state.providerMode === "new" ? (
            <div className="space-y-3 rounded-md border border-border p-3">
              <ProviderPresetFields
                modelMode="multi"
                value={{
                  name: state.providerName,
                  providerType: state.providerType,
                  baseUrl: state.providerBaseUrl,
                  apiKey: state.providerApiKey,
                  model: state.model,
                  entries: state.providerEntries,
                  primaryModel: state.model,
                }}
                onChange={(next) =>
                  patch({
                    ...(next.name !== undefined ? { providerName: next.name } : {}),
                    ...(next.providerType !== undefined ? { providerType: next.providerType } : {}),
                    ...(next.baseUrl !== undefined ? { providerBaseUrl: next.baseUrl } : {}),
                    ...(next.apiKey !== undefined ? { providerApiKey: next.apiKey } : {}),
                    ...(next.entries !== undefined ? { providerEntries: next.entries } : {}),
                    ...(next.primaryModel !== undefined ? { model: next.primaryModel } : {}),
                  })
                }
              />
              <p className="text-[11px] text-muted-foreground">
                {t("lifecycle:wizard.apiKeyHint")}
              </p>
            </div>
          ) : null}

          {state.providerMode === "none" ? (
            <p className="rounded-md bg-muted/60 p-3 text-xs text-muted-foreground">
              {t("lifecycle:wizard.providerLaterHint")}
            </p>
          ) : null}
        </div>
      ) : null}

      {step === "brain" ? (
        <div className="space-y-4">
          <label className="block">
            <span className="mb-1 block text-xs font-medium">
              {t("lifecycle:wizard.personalityLabel")}
            </span>
            <textarea
              className={`${selectClass} min-h-20 resize-y`}
              value={state.personality}
              onChange={(event) => patch({ personality: event.target.value })}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium">
              {t("lifecycle:wizard.goalsLabel")}
            </span>
            <textarea
              className={`${selectClass} min-h-20 resize-y`}
              value={state.goals}
              onChange={(event) => patch({ goals: event.target.value })}
            />
          </label>
          <label className="flex items-center justify-between rounded-md border border-border p-3 text-sm">
            <span>
              <span className="block font-medium">{t("lifecycle:wizard.learningLabel")}</span>
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {t("lifecycle:wizard.learningHint")}
              </span>
            </span>
            <input
              type="checkbox"
              checked={state.learningEnabled}
              onChange={(event) => patch({ learningEnabled: event.target.checked })}
            />
          </label>
          <label className="block rounded-md border border-border p-3">
            <span className="flex items-center justify-between text-xs font-medium">
              {t("lifecycle:wizard.curiosityLabel")}
              {/* 档位由后端算：前端不再保留第二套阈值（§3.3） */}
              <span className="font-mono text-muted-foreground">{state.curiosity.toFixed(1)}</span>
            </span>
            <input
              className="mt-3 w-full accent-foreground"
              type="range"
              min="0"
              max="1"
              step="0.1"
              value={state.curiosity}
              onChange={(event) => patch({ curiosity: Number(event.target.value) })}
            />
            <BehaviorPreview curiosity={state.curiosity} learningEnabled={state.learningEnabled} />
          </label>
        </div>
      ) : null}

      {step === "packages" ? (
        <div className="space-y-2" data-tutorial-target="wizard-packages">
          <p className="text-xs text-muted-foreground">{t("lifecycle:wizard.packagesHint")}</p>
          {packagesQuery.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : (
            packages.map((pkg) => {
              const checked = state.packageIds.includes(pkg.id);
              return (
                <label
                  key={pkg.id}
                  data-testid="package-card"
                  className={cn(
                    "flex cursor-pointer items-start gap-3 rounded-md border border-border px-3 py-2 text-sm transition-colors",
                    checked ? "border-foreground/40 bg-muted" : "hover:bg-muted/50",
                  )}
                >
                  <input
                    type="checkbox"
                    className="mt-1"
                    checked={checked}
                    onChange={() => togglePackage(pkg.id)}
                  />
                  <span className="min-w-0 flex-1">
                    <span className="flex items-center gap-2 font-medium">
                      {pkg.name}
                      {pkg.built_in ? (
                        <Badge variant="muted">{t("lifecycle:packages.builtIn")}</Badge>
                      ) : null}
                    </span>
                    {pkg.description ? (
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {pkg.description}
                      </span>
                    ) : null}
                  </span>
                </label>
              );
            })
          )}
        </div>
      ) : null}

      {step === "preview" ? (
        <div className="space-y-3">
          <p className="text-xs text-muted-foreground">{t("lifecycle:wizard.previewHint")}</p>
          {previewQuery.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : previewQuery.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400">
              {previewQuery.error instanceof Error
                ? previewQuery.error.message
                : t("common:errorFallback")}
            </p>
          ) : (previewQuery.data?.steps ?? []).length === 0 ? (
            <p className="text-xs text-muted-foreground">{t("lifecycle:wizard.previewEmpty")}</p>
          ) : (
            <ProvisioningPreviewList steps={previewQuery.data!.steps} />
          )}
        </div>
      ) : null}

      {step === "hire" ? (
        <dl className="space-y-1.5 text-sm">
          <SummaryRow label={t("lifecycle:wizard.summary.name")} value={state.name || "—"} />
          <SummaryRow label={t("lifecycle:wizard.summary.slug")} value={state.slug || "—"} mono />
          <SummaryRow label={t("lifecycle:wizard.summary.title")} value={state.title || "—"} />
          <SummaryRow
            label={t("lifecycle:wizard.summary.role")}
            value={enumLabel(t, "employee:role", state.role)}
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.department")}
            value={departments.find((d) => d.id === state.departmentId)?.name ?? "—"}
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.position")}
            value={positions.find((p) => p.id === state.positionId)?.title ?? "—"}
          />
          <SummaryRow label={t("lifecycle:wizard.summary.manager")} value={managerName} />
          <SummaryRow
            label={t("lifecycle:wizard.summary.runtime")}
            value={state.runtimeType ? enumLabel(t, "runtime:type", state.runtimeType) : "—"}
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.provider")}
            value={
              state.providerMode === "none"
                ? t("lifecycle:wizard.providerModes.none")
                : state.providerMode === "existing"
                  ? (companyProviders.find((provider) => provider.id === state.providerId)?.name ??
                    "—")
                  : state.providerName || "—"
            }
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.model")}
            value={state.providerMode === "none" ? "—" : state.model || "—"}
            mono
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.learning")}
            value={
              state.learningEnabled
                ? `${t("lifecycle:wizard.enabled")} · ${Math.round(state.curiosity * 100)}%`
                : t("lifecycle:wizard.disabled")
            }
          />
          <SummaryRow
            label={t("lifecycle:wizard.summary.packages")}
            value={
              packages
                .filter((p) => state.packageIds.includes(p.id))
                .map((p) => p.name)
                .join(", ") || "—"
            }
          />
        </dl>
      ) : null}

      {onboard.isError ? (
        <p className="mt-3 text-xs text-red-600 dark:text-red-400">{onboard.error.message}</p>
      ) : null}

      <div className="mt-5 flex justify-between">
        <Button
          variant="outline"
          size="sm"
          disabled={stepIndex === 0 || onboard.isPending}
          onClick={() => setStepIndex((i) => Math.max(0, i - 1))}
        >
          {t("common:back")}
        </Button>
        {step === "hire" ? (
          <Button
            size="sm"
            disabled={!advanceable || onboard.isPending}
            onClick={submit}
            data-tutorial-target="wizard-confirm"
            data-testid="wizard-hire"
          >
            {onboard.isPending ? t("lifecycle:wizard.hiring") : t("lifecycle:wizard.hire")}
          </Button>
        ) : (
          <Button
            size="sm"
            disabled={!advanceable}
            data-tutorial-target="wizard-next"
            data-testid="wizard-next"
            onClick={() => setStepIndex((i) => Math.min(HIRE_WIZARD_STEPS.length - 1, i + 1))}
          >
            {t("common:next")}
          </Button>
        )}
      </div>
    </Dialog>
  );
}

function SummaryRow({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className={mono ? "font-mono text-xs" : ""}>{value}</dd>
    </div>
  );
}
