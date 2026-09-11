import { useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Loader2, UserPlus } from "lucide-react";
import { Dialog } from "../common/dialog";
import { Button } from "../common/button";
import { Input } from "../common/input";
import { useCompany } from "../../hooks/useSystem";
import { useVacantSlots } from "../../hooks/useOrganizations";
import { useRecruitListing } from "../../hooks/useMarket";
import { ApiError } from "../../api/client";

const selectClass =
  "w-full rounded-md border border-border bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const ROLES = ["engineer", "researcher", "product_manager", "qa_engineer", "ceo"] as const;

function errorText(t: ReturnType<typeof useTranslation>["t"], error: unknown): string {
  const detail = error instanceof ApiError ? error.detail : String(error);
  const key = `market:recruit.reasons.${detail}`;
  const translated = t(key);
  return translated === key ? detail : translated;
}

/**
 * 招募对话框（T2.6 的 UI）：选部门 +（可选）编制，同一事务建人/任职。
 * 成功后给出员工链接 —— 身份/知识/证据都不复制，这里不做任何"预检"式伪造。
 */
export function RecruitDialog({
  open,
  onOpenChange,
  listingId,
  candidateName,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  listingId: number;
  candidateName: string;
}) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const companyQuery = useCompany();
  const vacancies = useVacantSlots();
  const recruit = useRecruitListing();
  const [departmentId, setDepartmentId] = useState<number | null>(null);
  const [slotId, setSlotId] = useState<number | null>(null);
  const [title, setTitle] = useState("");
  const [role, setRole] = useState("");
  const [reason, setReason] = useState("");

  const departments = companyQuery.data?.departments ?? [];
  const slots = (vacancies.data ?? []).filter(
    (slot) => departmentId == null || Number(slot["department_id"]) === departmentId,
  );

  const submit = (event: FormEvent) => {
    event.preventDefault();
    recruit.mutate(
      {
        listingId,
        body: {
          ...(departmentId != null ? { department_id: departmentId } : {}),
          ...(slotId != null ? { position_slot_id: slotId } : {}),
          ...(title.trim() ? { title: title.trim() } : {}),
          ...(role ? { role } : {}),
          ...(reason.trim() ? { reason: reason.trim() } : {}),
        },
      },
      { onSuccess: () => onOpenChange(false) },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("market:recruit.title", { name: candidateName })}
      description={t("market:recruit.description")}
      className="max-w-lg"
    >
      <form onSubmit={submit} className="space-y-3">
        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("market:recruit.department")}
          </span>
          <select
            className={selectClass}
            data-testid="recruit-department"
            value={departmentId ?? ""}
            onChange={(event) => {
              setDepartmentId(event.target.value === "" ? null : Number(event.target.value));
              setSlotId(null);
            }}
          >
            <option value="">{t("market:recruit.departmentNone")}</option>
            {departments.map((department) => (
              <option key={department.id} value={department.id}>
                {department.name}
              </option>
            ))}
          </select>
        </label>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("market:recruit.slot")}
          </span>
          <select
            className={selectClass}
            data-testid="recruit-slot"
            value={slotId ?? ""}
            onChange={(event) =>
              setSlotId(event.target.value === "" ? null : Number(event.target.value))
            }
          >
            <option value="">{t("market:recruit.slotNone")}</option>
            {slots.map((slot) => (
              <option key={String(slot["id"])} value={String(slot["id"])}>
                {String(slot["slot_code"] ?? "")} ·{" "}
                {String(slot["position_name"] ?? slot["position_code"] ?? "")}
              </option>
            ))}
          </select>
          <span className="mt-1 block text-[11px] leading-4 text-muted-foreground">
            {t("market:recruit.slotHint")}
          </span>
        </label>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted-foreground">
              {t("market:recruit.titleLabel")}
            </span>
            <Input
              data-testid="recruit-title"
              value={title}
              placeholder={candidateName}
              onChange={(event) => setTitle(event.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-xs font-medium text-muted-foreground">
              {t("market:recruit.role")}
            </span>
            <select
              className={selectClass}
              data-testid="recruit-role"
              value={role}
              onChange={(event) => setRole(event.target.value)}
            >
              <option value="">{t("market:recruit.roleDerived")}</option>
              {ROLES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>

        <label className="block">
          <span className="mb-1 block text-xs font-medium text-muted-foreground">
            {t("market:recruit.reason")}
          </span>
          <Input
            data-testid="recruit-reason"
            value={reason}
            onChange={(event) => setReason(event.target.value)}
          />
        </label>

        {recruit.isError ? (
          <p className="text-xs text-danger" role="alert">
            {t("market:recruit.error", { error: errorText(t, recruit.error) })}
          </p>
        ) : null}

        {recruit.isSuccess ? (
          <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-3 text-xs">
            <p className="text-emerald-700 dark:text-emerald-400">
              {t("market:recruit.success", { name: recruit.data.identity_id ?? candidateName })}
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="mt-2"
              onClick={() => navigate(`/employees/${recruit.data.employee_id}`)}
            >
              {t("market:recruit.viewEmployee")}
            </Button>
          </div>
        ) : null}

        <div className="flex justify-end gap-2 pt-1">
          <Button type="button" variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t("common:cancel")}
          </Button>
          <Button type="submit" size="sm" disabled={recruit.isPending || recruit.isSuccess}>
            {recruit.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <UserPlus className="h-3.5 w-3.5" />
            )}
            {recruit.isPending ? t("market:recruit.submitting") : t("market:recruit.submit")}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
