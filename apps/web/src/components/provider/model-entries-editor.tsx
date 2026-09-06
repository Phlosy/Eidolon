import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { LoaderCircle, Plus, Radar, X } from "lucide-react";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { fixEntriesDefault, isValidModelName } from "./constants";
import { cn } from "../../utils/cn";
import type { ModelEntry, ProviderProbeResult } from "../../types";

/**
 * 条目式模型编辑器（CC-Switch 风格）：没有预设模型值 ——
 * 条目只来自两处：探测（``probe`` 由调用方给：创建流程走未保存配置的
 * /providers/probe，编辑/换绑流程走已保存 provider 的 /providers/{id}/models）
 * 和手动新增。每行 = [选用 ✓] [显示名] [真实模型名] [默认 ○] [删除 ×]。
 *
 * ``autoProbeSignature``：非空时按签名自动探测一次（创建表单里"填完 key 就探"）。
 */

export interface ModelEntriesValue {
  entries: ModelEntry[];
  /** 默认条目的真实模型名（必须指向一个"选用中"的条目） */
  primaryModel: string;
}

interface ModelEntriesEditorProps {
  value: ModelEntriesValue;
  onChange: (patch: Partial<ModelEntriesValue>) => void;
  probe: () => Promise<ProviderProbeResult>;
  /** 变了才重新自动探测的签名；null 表示不自动探测 */
  autoProbeSignature?: string | null;
  docsUrl?: string | null;
}

export function ModelEntriesEditor({
  value,
  onChange,
  probe,
  autoProbeSignature = null,
  docsUrl = null,
}: ModelEntriesEditorProps) {
  const { t } = useTranslation();
  const [probeState, setProbeState] = useState<"idle" | "pending" | "ok" | "failed" | "empty">(
    "idle",
  );
  const [probeMessage, setProbeMessage] = useState("");
  const autoProbed = useRef("");

  const patchEntry = (index: number, patch: Partial<ModelEntry>) => {
    const entries = value.entries.map((entry, i) => (i === index ? { ...entry, ...patch } : entry));
    onChange(fixEntriesDefault(entries, value.primaryModel));
  };

  const removeEntry = (index: number) => {
    onChange(
      fixEntriesDefault(
        value.entries.filter((_, i) => i !== index),
        value.primaryModel,
      ),
    );
  };

  const addEntry = () => {
    onChange({ entries: [...value.entries, { alias: "", model: "", enabled: true }] });
  };

  const runProbe = async () => {
    setProbeState("pending");
    setProbeMessage("");
    try {
      const result = await probe();
      if (!result.ok) {
        setProbeState("failed");
        setProbeMessage(result.error || t("provider:form.probeFailed"));
        return;
      }
      if (result.models.length === 0) {
        setProbeState("empty");
        return;
      }
      setProbeState("ok");
      setProbeMessage(t("provider:form.probeOk", { count: result.models.length }));
      // 探测到多少就追加多少条目（已有的不重复加）
      const existing = new Set(value.entries.map((entry) => entry.model));
      const fresh = result.models
        .filter((model) => !existing.has(model))
        .map((model) => ({ alias: model, model, enabled: true }));
      if (fresh.length) {
        onChange(fixEntriesDefault([...value.entries, ...fresh], value.primaryModel));
      }
    } catch (error) {
      setProbeState("failed");
      setProbeMessage(error instanceof Error ? error.message : String(error));
    }
  };

  useEffect(() => {
    if (!autoProbeSignature) return;
    if (autoProbed.current === autoProbeSignature) return;
    const timer = window.setTimeout(() => {
      autoProbed.current = autoProbeSignature;
      void runProbe();
    }, 600);
    return () => window.clearTimeout(timer);
  }, [autoProbeSignature]);

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-medium text-muted-foreground">
          {t("provider:form.models")}
        </span>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="h-7 gap-1 px-2 text-[11px]"
          disabled={probeState === "pending"}
          onClick={() => void runProbe()}
        >
          {probeState === "pending" ? (
            <LoaderCircle className="h-3 w-3 animate-spin" />
          ) : (
            <Radar className="h-3 w-3" />
          )}
          {t("provider:form.probe")}
        </Button>
      </div>
      {probeState === "failed" ? (
        <p className="text-xs text-danger" role="alert">
          {t("provider:form.probeKeyFailed", { error: probeMessage })}
        </p>
      ) : null}
      {probeState === "ok" ? (
        <p className="text-xs text-success" role="status">
          {probeMessage}
        </p>
      ) : null}
      {probeState === "empty" ? (
        <p className="text-[11px] text-muted-foreground">{t("provider:form.probeEmpty")}</p>
      ) : null}
      {docsUrl ? (
        <p className="text-[10px] text-muted-foreground/80">
          <a
            href={docsUrl}
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-2 hover:text-foreground"
          >
            {t("provider:form.officialDocs")}
          </a>
        </p>
      ) : null}

      {value.entries.length > 0 ? (
        <div className="space-y-1.5 rounded-md border border-border p-2">
          <div className="grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto_auto] items-center gap-2 px-1 text-[10px] uppercase tracking-wider text-muted-foreground/70">
            <span>{t("provider:form.entryEnabled")}</span>
            <span>{t("provider:form.entryAlias")}</span>
            <span>{t("provider:form.entryModel")}</span>
            <span>{t("provider:form.entryDefault")}</span>
            <span />
          </div>
          {value.entries.map((entry, index) => {
            const isPrimary = entry.enabled && entry.model === value.primaryModel;
            const modelInvalid = entry.model.trim() !== "" && !isValidModelName(entry.model.trim());
            return (
              <div
                key={index}
                className={cn(
                  "grid grid-cols-[auto_minmax(0,1fr)_minmax(0,1fr)_auto_auto] items-center gap-2 rounded-lg px-1 py-1",
                  entry.enabled ? "bg-background/40" : "opacity-50",
                )}
              >
                <input
                  type="checkbox"
                  checked={entry.enabled}
                  onChange={(e) => patchEntry(index, { enabled: e.target.checked })}
                  aria-label={`${t("provider:form.entryEnabled")} ${entry.model || index + 1}`}
                  className="h-3.5 w-3.5 accent-primary"
                />
                <Input
                  value={entry.alias}
                  placeholder={entry.model || t("provider:form.entryAliasPlaceholder")}
                  onChange={(e) => patchEntry(index, { alias: e.target.value })}
                  className="h-8 text-xs"
                />
                <Input
                  value={entry.model}
                  placeholder={t("provider:form.entryModelPlaceholder")}
                  onChange={(e) => patchEntry(index, { model: e.target.value })}
                  className={cn(
                    "h-8 font-mono text-xs",
                    modelInvalid && "border-danger focus-visible:ring-danger/30",
                  )}
                />
                <input
                  type="radio"
                  name="primary-model"
                  checked={isPrimary}
                  disabled={!entry.enabled || !entry.model.trim()}
                  onChange={() => onChange({ primaryModel: entry.model.trim() })}
                  aria-label={`${t("provider:form.entryDefault")} ${entry.model || index + 1}`}
                  className="h-3.5 w-3.5 accent-primary"
                />
                <button
                  type="button"
                  aria-label={`${t("provider:form.removeModel")} ${entry.model || index + 1}`}
                  onClick={() => removeEntry(index)}
                  className="shrink-0 text-muted-foreground hover:text-danger"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </div>
            );
          })}
        </div>
      ) : null}

      <Button type="button" variant="outline" size="sm" className="h-8 w-full" onClick={addEntry}>
        <Plus className="h-3 w-3" />
        {t("provider:form.addEntry")}
      </Button>
    </div>
  );
}
