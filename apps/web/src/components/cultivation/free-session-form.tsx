import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { BookOpen } from "lucide-react";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { enumLabel } from "../../utils/labels";
import { useFreeSession } from "../../hooks/useCultivation";
import type { CultivationMode, FreeSessionInput } from "../../api/cultivation";

const MODES: CultivationMode[] = ["web_research", "document_study", "knowledge_review"];
const KINDS: Array<FreeSessionInput["kind"]> = [
  "course",
  "exam",
  "project",
  "internship",
  "competition",
];

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

/**
 * 自由养成会话（无模板路径）：逐次指定主题/途径/形式/强度。
 *
 * 与模板培养共用同一条产出链路（学习产出 → 教育证据 → 履历事件），
 * 所以 UI 不提供任何"直接加能力"的入口 —— 强度只决定证据 signal。
 */
export function FreeSessionForm({ characterId }: { characterId: number }) {
  const { t } = useTranslation();
  const session = useFreeSession(characterId);
  const [topic, setTopic] = useState("");
  const [mode, setMode] = useState<CultivationMode>("web_research");
  const [kind, setKind] = useState<FreeSessionInput["kind"]>("course");
  const [signal, setSignal] = useState(60);
  const [error, setError] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = topic.trim();
    if (!trimmed) {
      setError(t("cultivation:freeSession.topicRequired"));
      return;
    }
    setError("");
    session.mutate({ topic: trimmed, mode, kind, signal }, { onSuccess: () => setTopic("") });
  };

  return (
    <section
      className="rounded-2xl border border-border bg-card p-4 shadow-card"
      data-testid="free-session-form"
    >
      <h3 className="text-sm font-semibold">{t("cultivation:freeSession.title")}</h3>
      <p className="mt-1 text-xs text-muted-foreground">
        {t("cultivation:freeSession.description")}
      </p>
      <form onSubmit={submit} className="mt-3 space-y-3">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("cultivation:freeSession.topicLabel")}
          </span>
          <Input
            data-testid="free-session-topic"
            maxLength={500}
            placeholder={t("cultivation:freeSession.topicPlaceholder")}
            value={topic}
            onChange={(event) => setTopic(event.target.value)}
          />
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted-foreground">
              {t("cultivation:freeSession.modeLabel")}
            </span>
            <select
              className={selectClass}
              value={mode}
              onChange={(event) => setMode(event.target.value as CultivationMode)}
            >
              {MODES.map((value) => (
                <option key={value} value={value}>
                  {enumLabel(t, "cultivation:freeSession.mode", value)}
                </option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted-foreground">
              {t("cultivation:freeSession.kindLabel")}
            </span>
            <select
              className={selectClass}
              value={kind}
              onChange={(event) => setKind(event.target.value as FreeSessionInput["kind"])}
            >
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {enumLabel(t, "cultivation:kind", value)}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("cultivation:freeSession.intensityLabel", { value: signal })}
          </span>
          <input
            type="range"
            min={0}
            max={100}
            step={5}
            value={signal}
            onChange={(event) => setSignal(Number(event.target.value))}
            className="w-full accent-primary"
          />
          <span className="mt-1 block text-[11px] leading-4 text-muted-foreground">
            {t("cultivation:freeSession.intensityHint")}
          </span>
        </label>

        {error ? (
          <p className="text-xs text-danger" role="alert">
            {error}
          </p>
        ) : null}
        {session.isError ? (
          <p className="text-xs text-danger" role="alert">
            {session.error.message}
          </p>
        ) : null}

        <Button type="submit" size="sm" disabled={session.isPending}>
          <BookOpen className="h-3.5 w-3.5" />
          {session.isPending
            ? t("cultivation:freeSession.submitting")
            : t("cultivation:freeSession.submit")}
        </Button>
      </form>
    </section>
  );
}
