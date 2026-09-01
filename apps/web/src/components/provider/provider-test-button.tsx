import { useEffect, useRef } from "react";
import { Check, Loader2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useTestProvider } from "../../hooks/useProviders";
import { Button } from "../common/button";
import type { ProviderTestResult } from "../../types";

interface ProviderTestButtonProps {
  providerId: number;
  /** Called with the latest result so parents can surface it (e.g. on the card). */
  onResult?: (result: ProviderTestResult) => void;
  disabled?: boolean;
}

/** Runs POST /providers/{id}/test: spinner while pending, then ok/fail with latency. */
export function ProviderTestButton({ providerId, onResult, disabled }: ProviderTestButtonProps) {
  const { t } = useTranslation();
  const test = useTestProvider();
  const onResultRef = useRef(onResult);
  onResultRef.current = onResult;

  useEffect(() => {
    if (test.data) onResultRef.current?.(test.data);
  }, [test.data]);

  return (
    <span className="inline-flex items-center gap-2">
      <Button
        variant="outline"
        size="sm"
        disabled={disabled || test.isPending}
        onClick={() => test.mutate(providerId)}
      >
        {test.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
        {t("provider:actions.test")}
      </Button>
      {test.isSuccess ? (
        test.data.ok ? (
          <span
            data-testid="provider-test-ok"
            className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400"
          >
            <Check className="h-3.5 w-3.5" />
            {t("provider:actions.connected")}
            {test.data.latency_ms != null ? (
              <span className="font-mono text-muted-foreground">{test.data.latency_ms}ms</span>
            ) : null}
          </span>
        ) : (
          <span
            data-testid="provider-test-fail"
            className="inline-flex items-center gap-1 text-xs text-red-600 dark:text-red-400"
            title={test.data.error ?? t("provider:actions.failed")}
          >
            <XCircle className="h-3.5 w-3.5" />
            {t("provider:actions.failed")}
          </span>
        )
      ) : null}
    </span>
  );
}
