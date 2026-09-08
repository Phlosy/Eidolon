import { useEffect, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";
import { Button } from "./button";

interface DialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}

/** Minimal modal dialog: overlay, Escape to close, portal to <body>. */
export function Dialog({
  open,
  onOpenChange,
  title,
  description,
  children,
  className,
}: DialogProps) {
  const { t } = useTranslation();
  useEffect(() => {
    if (!open) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onOpenChange(false);
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [open, onOpenChange]);

  if (!open) return null;

  return createPortal(
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      role="presentation"
      onClick={() => onOpenChange(false)}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        data-testid="dialog-panel"
        className={cn(
          "flex max-h-[calc(100vh-2rem)] w-full max-w-md flex-col rounded-lg border border-border bg-card p-5 shadow-lg",
          "supports-[height:100dvh]:max-h-[calc(100dvh-2rem)]",
          className,
        )}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex shrink-0 items-start justify-between gap-4">
          <div>
            <h2 className="text-sm font-semibold">{title}</h2>
            {description ? (
              <p className="mt-1 text-xs text-muted-foreground">{description}</p>
            ) : null}
          </div>
          <Button
            variant="ghost"
            size="icon"
            aria-label={t("common:close")}
            onClick={() => onOpenChange(false)}
          >
            <X className="h-4 w-4" />
          </Button>
        </div>
        {/* 内容区滚动：小屏（如 14 寸 Mac）下自动检测出很多模型时，确认按钮
            不会被顶出屏幕；头部与关闭按钮始终可见。 */}
        <div
          data-testid="dialog-body"
          className="min-h-0 flex-1 overflow-y-auto overscroll-contain pr-1"
        >
          {children}
        </div>
      </div>
    </div>,
    document.body,
  );
}
