import { useTranslation } from "react-i18next";
import { Input } from "../common/input";

interface RuntimeResourceConfigProps {
  cpuLimit: number;
  memoryLimitMb: number;
  onChange: (next: { cpuLimit: number; memoryLimitMb: number }) => void;
  disabled?: boolean;
}

/** CPU/memory limits for a runtime instance (docker deployment). */
export function RuntimeResourceConfig({
  cpuLimit,
  memoryLimitMb,
  onChange,
  disabled,
}: RuntimeResourceConfigProps) {
  const { t } = useTranslation();
  return (
    <div className="grid grid-cols-2 gap-3">
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">
          {t("runtime:resources.cpuLabel")}
        </span>
        <Input
          type="number"
          min={0.5}
          step={0.5}
          value={cpuLimit}
          disabled={disabled}
          onChange={(e) => onChange({ cpuLimit: Number(e.target.value), memoryLimitMb })}
        />
      </label>
      <label className="block">
        <span className="mb-1 block text-xs font-medium text-muted-foreground">
          {t("runtime:resources.memoryLabel")}
        </span>
        <Input
          type="number"
          min={128}
          step={128}
          value={memoryLimitMb}
          disabled={disabled}
          onChange={(e) => onChange({ cpuLimit, memoryLimitMb: Number(e.target.value) })}
        />
      </label>
    </div>
  );
}
