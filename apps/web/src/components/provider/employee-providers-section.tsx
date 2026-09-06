import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Pencil, Plus, Star, X } from "lucide-react";
import {
  useAddEmployeeBinding,
  useCreateEmployeeProvider,
  useDeleteBinding,
  useEmployeeBindings,
  useEmployeeProviders,
  useSetPrimaryBinding,
  useUpdateBinding,
} from "../../hooks/useProviders";
import { listProviderModels } from "../../api/providers";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { EmptyState, ErrorState } from "../common/states";
import { Skeleton } from "../common/skeleton";
import { ProviderCard } from "./provider-card";
import { ProviderPresetFields, type ProviderPresetValue } from "./provider-preset-fields";
import { ModelEntriesEditor } from "./model-entries-editor";
import { isValidModelName } from "./constants";
import { cn } from "../../utils/cn";
import type { ModelBinding, ModelEntry, Provider } from "../../types";

/**
 * Employee → Runtime tab: manages THE EMPLOYEE's own provider accounts
 * (GET/POST /employees/{id}/providers). Company-shared accounts appear in the
 * list with a scope badge but are owned elsewhere. The API key field is
 * write-only; only the masked credential is ever rendered.
 */
export function EmployeeProvidersSection({ employeeId }: { employeeId: number }) {
  const { t } = useTranslation();
  const providersQuery = useEmployeeProviders(employeeId);
  const bindingsQuery = useEmployeeBindings(employeeId);
  const setPrimary = useSetPrimaryBinding(employeeId);
  const removeBinding = useDeleteBinding(employeeId);
  const addBinding = useAddEmployeeBinding(employeeId);
  const updateBinding = useUpdateBinding(employeeId);
  const create = useCreateEmployeeProvider(employeeId);

  // 编辑某 provider 的模型条目：初始值 = 现有绑定
  const [editProvider, setEditProvider] = useState<Provider | null>(null);
  const [editEntries, setEditEntries] = useState<ModelEntry[]>([]);
  const [editPrimary, setEditPrimary] = useState("");
  const [editError, setEditError] = useState("");
  const [editSaving, setEditSaving] = useState(false);

  const openEdit = (provider: Provider) => {
    const existing = (bindingsQuery.data ?? []).filter((b) => b.provider_id === provider.id);
    setEditEntries(existing.map((b) => ({ alias: b.alias, model: b.model, enabled: true })));
    setEditPrimary(existing.find((b) => b.is_primary)?.model ?? "");
    setEditError("");
    setEditProvider(provider);
  };

  /** 用条目编辑器的终态对现有绑定做 diff：增、删、改显示名、切默认。 */
  const saveEdit = async () => {
    if (!editProvider) return;
    const entries = editEntries.filter((entry) => entry.enabled && entry.model.trim());
    const invalid = entries.find((entry) => !isValidModelName(entry.model.trim()));
    if (invalid) {
      setEditError(t("provider:form.modelInvalid"));
      return;
    }
    setEditSaving(true);
    setEditError("");
    try {
      const current = (bindingsQuery.data ?? []).filter((b) => b.provider_id === editProvider.id);
      const nextModels = new Set(entries.map((entry) => entry.model.trim()));
      // 顺序有依赖：先删（腾位置），再增，再改显示名，最后切默认
      for (const binding of current) {
        if (!nextModels.has(binding.model)) {
          await removeBinding.mutateAsync(binding.id);
        }
      }
      const currentModels = new Map(current.map((b) => [b.model, b]));
      for (const entry of entries) {
        const existing = currentModels.get(entry.model.trim());
        if (!existing) {
          await addBinding.mutateAsync({
            provider_id: editProvider.id,
            model: entry.model.trim(),
            alias: entry.alias.trim(),
          });
        } else if (existing.alias !== entry.alias.trim()) {
          await updateBinding.mutateAsync({
            bindingId: existing.id,
            alias: entry.alias.trim(),
          });
        }
      }
      const currentPrimary = current.find((b) => b.is_primary)?.model;
      if (editPrimary && editPrimary !== currentPrimary && nextModels.has(editPrimary)) {
        // 默认目标的绑定可能刚建出来：重新拉一次拿 id
        const fresh = await bindingsQuery.refetch();
        const target = (fresh.data ?? []).find(
          (b) => b.provider_id === editProvider.id && b.model === editPrimary,
        );
        if (target && !target.is_primary) await setPrimary.mutateAsync(target.id);
      }
      setEditProvider(null);
    } catch (error) {
      setEditError(error instanceof Error ? error.message : String(error));
    } finally {
      setEditSaving(false);
    }
  };

  const [addOpen, setAddOpen] = useState(false);
  const [formError, setFormError] = useState("");
  const [form, setForm] = useState<ProviderPresetValue>({
    name: "",
    providerType: "openai",
    baseUrl: "",
    apiKey: "",
    model: "",
    entries: [],
    primaryModel: "",
  });

  const resetForm = () => {
    setForm({
      name: "",
      providerType: "openai",
      baseUrl: "",
      apiKey: "",
      model: "",
      entries: [],
      primaryModel: "",
    });
    setFormError("");
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!form.name.trim()) return;
    // 只有"选用中且真实模型名合法"的条目会被提交
    const entries = form.entries.filter((entry) => entry.enabled && entry.model.trim());
    const invalid = entries.find((entry) => !isValidModelName(entry.model.trim()));
    if (invalid) {
      setFormError(t("provider:form.modelInvalid"));
      return;
    }
    setFormError("");
    create.mutate(
      {
        name: form.name.trim(),
        provider_type: form.providerType,
        ...(form.baseUrl.trim() ? { base_url: form.baseUrl.trim() } : {}),
        ...(form.apiKey ? { api_key: form.apiKey } : {}),
        ...(entries.length
          ? {
              models: entries.map((entry) => ({
                model: entry.model.trim(),
                alias: entry.alias.trim(),
              })),
              primary_model: form.primaryModel || undefined,
            }
          : {}),
      },
      {
        onSuccess: () => {
          setAddOpen(false);
          resetForm();
        },
      },
    );
  };

  const providers = providersQuery.data ?? [];

  return (
    <div>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {t("provider:employeeSection.title")}
          </h4>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("provider:employeeSection.description")}
          </p>
        </div>
        {/* 教程打光在这里：员工级"新建服务商"才是 mock 运行时也能走通的绑定入口
            （带 model 的创建会顺手建好 ModelBinding，教程门禁认的就是它）。
            运行时卡片上的"更换服务商"对 mock 运行时的 supported_providers 是空列表，
            指过去只会让用户对着一个永远没有选项的下拉框。 */}
        <Button
          size="sm"
          data-tutorial-target="employee-provider-create"
          onClick={() => setAddOpen(true)}
        >
          <Plus className="h-3.5 w-3.5" />
          {t("provider:employeeSection.add")}
        </Button>
      </div>

      {providersQuery.isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-24 w-full" />
        </div>
      ) : providersQuery.isError ? (
        <ErrorState error={providersQuery.error} onRetry={() => providersQuery.refetch()} />
      ) : providers.length === 0 ? (
        <EmptyState
          title={t("provider:employeeSection.emptyTitle")}
          hint={t("provider:employeeSection.emptyHint")}
        />
      ) : (
        <div className="space-y-3">
          {providers.map((provider) => {
            const providerBindings = (bindingsQuery.data ?? []).filter(
              (binding) => binding.provider_id === provider.id,
            );
            return (
              <div key={provider.id} className="space-y-1.5">
                <ProviderCard provider={provider} />
                <div className="ml-2 space-y-1 border-l-2 border-border pl-3">
                  {providerBindings.map((binding) => (
                    <BindingRow
                      key={binding.id}
                      binding={binding}
                      busy={setPrimary.isPending || removeBinding.isPending}
                      onSetPrimary={() => setPrimary.mutate(binding.id)}
                      onRemove={() => removeBinding.mutate(binding.id)}
                    />
                  ))}
                  <button
                    type="button"
                    onClick={() => openEdit(provider)}
                    className="flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[11px] text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
                  >
                    <Pencil className="h-3 w-3" />
                    {t("provider:bindings.edit")}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={addOpen}
        onOpenChange={setAddOpen}
        title={t("provider:employeeSection.add")}
        description={t("provider:employeeSection.addDescription")}
        className="max-w-2xl"
      >
        <form onSubmit={submit} className="space-y-3">
          <ProviderPresetFields
            modelMode="multi"
            value={form}
            onChange={(patch) => setForm((current) => ({ ...current, ...patch }))}
          />
          {formError ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {formError}
            </p>
          ) : null}
          {create.isError ? (
            <p className="text-xs text-red-600 dark:text-red-400" role="alert">
              {create.error.message}
            </p>
          ) : null}
          <div className="flex justify-end gap-2 pt-1">
            <Button type="button" variant="outline" size="sm" onClick={() => setAddOpen(false)}>
              {t("common:cancel")}
            </Button>
            <Button type="submit" size="sm" disabled={create.isPending || !form.name.trim()}>
              {create.isPending ? t("runtime:wizard.saving") : t("provider:form.addProvider")}
            </Button>
          </div>
        </form>
      </Dialog>

      {/* 编辑某 provider 的模型条目：探测已保存账号的真实模型，diff 落库 */}
      <Dialog
        open={editProvider !== null}
        onOpenChange={(open) => !open && setEditProvider(null)}
        title={t("provider:bindings.editTitle", { name: editProvider?.name ?? "" })}
        className="max-w-2xl"
      >
        {editProvider ? (
          <div className="space-y-3">
            <ModelEntriesEditor
              value={{ entries: editEntries, primaryModel: editPrimary }}
              onChange={(patch) => {
                if (patch.entries !== undefined) setEditEntries(patch.entries);
                if (patch.primaryModel !== undefined) setEditPrimary(patch.primaryModel);
              }}
              probe={async () => {
                try {
                  const result = await listProviderModels(editProvider.id);
                  return { ok: true, error: null, models: result.models };
                } catch (error) {
                  return {
                    ok: false,
                    error: error instanceof Error ? error.message : String(error),
                    models: [],
                  };
                }
              }}
            />
            {editError ? (
              <p className="text-xs text-red-600 dark:text-red-400" role="alert">
                {editError}
              </p>
            ) : null}
            <div className="flex justify-end gap-2 pt-1">
              <Button variant="outline" size="sm" onClick={() => setEditProvider(null)}>
                {t("common:cancel")}
              </Button>
              <Button size="sm" disabled={editSaving} onClick={() => void saveEdit()}>
                {editSaving ? t("runtime:wizard.saving") : t("common:save")}
              </Button>
            </div>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}

/** 一条模型绑定：星标切换默认启动模型，× 解除绑定。 */
function BindingRow({
  binding,
  busy,
  onSetPrimary,
  onRemove,
}: {
  binding: ModelBinding;
  busy: boolean;
  onSetPrimary: () => void;
  onRemove: () => void;
}) {
  const { t } = useTranslation();
  return (
    <div className="flex items-center gap-2 rounded-lg border border-border bg-background/40 px-2.5 py-1.5">
      <button
        type="button"
        disabled={busy || binding.is_primary}
        title={t("provider:bindings.makePrimary")}
        aria-label={`${t("provider:bindings.makePrimary")} ${binding.model}`}
        onClick={onSetPrimary}
        className={cn(
          "shrink-0",
          binding.is_primary ? "text-primary" : "text-muted-foreground/40 hover:text-primary",
        )}
      >
        <Star className="h-3.5 w-3.5" fill={binding.is_primary ? "currentColor" : "none"} />
      </button>
      <span className="min-w-0 flex-1 truncate text-xs">
        {binding.alias ? (
          <>
            {binding.alias}{" "}
            <span className="font-mono text-[10px] text-muted-foreground">{binding.model}</span>
          </>
        ) : (
          <span className="font-mono">{binding.model}</span>
        )}
      </span>
      {binding.is_primary ? (
        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] text-primary">
          {t("provider:bindings.primary")}
        </span>
      ) : null}
      <button
        type="button"
        disabled={busy}
        aria-label={`${t("provider:bindings.remove")} ${binding.model}`}
        onClick={onRemove}
        className="shrink-0 text-muted-foreground hover:text-danger"
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
