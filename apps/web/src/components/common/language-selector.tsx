import { Languages } from "lucide-react";
import { useTranslation } from "react-i18next";
import { SUPPORTED_LANGUAGES } from "../../i18n";
import { cn } from "../../utils/cn";

interface LanguageSelectorProps {
  /** Compact (header) variant: icon + bare select, no border padding extras. */
  compact?: boolean;
  className?: string;
}

/**
 * Reusable language switcher. Changing the language applies immediately
 * (react-i18next re-renders) and is persisted by the LanguageDetector's
 * `caches: ["localStorage"]` under `eidolon-language`.
 */
export function LanguageSelector({ compact = false, className }: LanguageSelectorProps) {
  const { i18n, t } = useTranslation();
  const resolved = i18n.resolvedLanguage ?? i18n.language ?? "zh-CN";
  const current = SUPPORTED_LANGUAGES.some((lang) => lang.code === resolved)
    ? resolved
    : resolved.toLowerCase().startsWith("zh")
      ? "zh-CN"
      : "en-US";

  return (
    <label
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-border text-muted-foreground transition-colors hover:bg-muted",
        compact ? "px-2 py-1.5" : "px-3 py-2",
        className,
      )}
    >
      <Languages className="h-4 w-4 shrink-0" />
      <select
        aria-label={t("common:language.label")}
        value={current}
        onChange={(e) => void i18n.changeLanguage(e.target.value)}
        className="cursor-pointer bg-transparent text-xs font-medium text-foreground focus-visible:outline-none [&>option]:bg-card [&>option]:text-foreground"
      >
        {SUPPORTED_LANGUAGES.map((lang) => (
          <option key={lang.code} value={lang.code}>
            {lang.label}
          </option>
        ))}
      </select>
    </label>
  );
}
