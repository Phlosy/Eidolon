import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Dialog } from "../common/dialog";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { cn } from "../../utils/cn";
import { CULTIVATION_TEMPLATES } from "./templates";
import { useCreateCultivationCharacter } from "../../hooks/useCultivation";
import type { CharacterOrigin, CultivationTemplateId } from "../../api/cultivation";

/**
 * 建角色：来源（玩家自训 / 空白养成）+ 模板（自由养成 = 不选模板）。
 * 创建成功即跳转详情页 —— 角色一落地就有终身身份 ID，履历从这一刻开始累积。
 */
export function CreateCharacterDialog({
  open,
  onOpenChange,
  onCreated,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onCreated: (characterId: number) => void;
}) {
  const { t } = useTranslation();
  const create = useCreateCultivationCharacter();
  const [name, setName] = useState("");
  const [origin, setOrigin] = useState<CharacterOrigin>("trained");
  const [template, setTemplate] = useState<CultivationTemplateId | null>(null);

  const close = () => {
    onOpenChange(false);
    setName("");
    setOrigin("trained");
    setTemplate(null);
    create.reset();
  };

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) return;
    create.mutate(
      {
        name: trimmed,
        origin,
        // 空白养成 = 自由路径，不开模板 program
        ...(origin === "trained" && template ? { template } : {}),
      },
      {
        onSuccess: (character) => {
          onCreated(character.id);
          close();
        },
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => (next ? onOpenChange(true) : close())}
      title={t("cultivation:create.dialogTitle")}
      description={t("cultivation:create.dialogDescription")}
      className="max-w-lg"
    >
      <form onSubmit={submit} className="space-y-4">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("cultivation:create.nameLabel")}
          </span>
          <Input
            required
            maxLength={200}
            data-testid="character-name"
            placeholder={t("cultivation:create.namePlaceholder")}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </label>

        <div>
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("cultivation:create.originLabel")}
          </span>
          <div className="grid grid-cols-2 gap-2">
            {(["trained", "blank"] as const).map((value) => (
              <button
                key={value}
                type="button"
                aria-pressed={origin === value}
                onClick={() => {
                  setOrigin(value);
                  if (value === "blank") setTemplate(null);
                }}
                className={cn(
                  "rounded-xl border px-3 py-2 text-left text-xs transition-colors",
                  origin === value
                    ? "border-border-active bg-muted text-foreground"
                    : "border-border text-muted-foreground hover:bg-muted/50",
                )}
              >
                <span className="block font-medium">{t(`cultivation:origin.${value}`)}</span>
                <span className="mt-0.5 block text-[11px] text-muted-foreground">
                  {t(`cultivation:origin.${value}Hint`)}
                </span>
              </button>
            ))}
          </div>
        </div>

        {origin === "trained" ? (
          <div>
            <span className="mb-1 block text-xs font-medium text-muted-foreground">
              {t("cultivation:create.templateLabel")}
            </span>
            <div className="space-y-2">
              {[null, ...CULTIVATION_TEMPLATES].map((value) => {
                const key = value ?? "none";
                return (
                  <button
                    key={key}
                    type="button"
                    aria-pressed={template === value}
                    data-testid={`template-${key}`}
                    onClick={() => setTemplate(value)}
                    className={cn(
                      "w-full rounded-xl border px-3 py-2 text-left text-xs transition-colors",
                      template === value
                        ? "border-border-active bg-muted text-foreground"
                        : "border-border text-muted-foreground hover:bg-muted/50",
                    )}
                  >
                    <span className="block font-medium">{t(`cultivation:template.${key}`)}</span>
                    <span className="mt-0.5 block text-[11px] text-muted-foreground">
                      {t(`cultivation:template.${key}Description`)}
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        ) : null}

        {create.isError ? (
          <p className="text-xs text-danger" role="alert">
            {create.error.message}
          </p>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={close}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" size="sm" disabled={create.isPending || !name.trim()}>
            {create.isPending ? t("runtime:wizard.saving") : t("cultivation:create.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
