import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeftRight,
  ChevronDown,
  LogOut,
  Settings,
  Trash2,
  UserRound,
  type LucideIcon,
} from "lucide-react";
import { logout, requestAccountDeletion } from "../../api/auth";
import { useAuth } from "../../features/auth/auth-context";
import { Button } from "../common/button";
import { Dialog } from "../common/dialog";
import { cn } from "../../utils/cn";

/**
 * 顶栏的账号入口：先展开一张小卡片（身份摘要 + 常用操作），
 * 详细配置统一收进 /settings 的"个人设置 / 系统设置"分区。
 *
 * 三个账号动作的分工：
 * - 退出登录：结束当前会话，回到登录页；
 * - 切换账号：同样结束当前会话，但语义上是"换一个人登录"，落在登录页；
 * - 注销账号：软删除账号（后端撤销全部会话），落在注册页。二次确认后才执行。
 */

function MenuItem({
  icon: Icon,
  label,
  danger,
  disabled,
  onClick,
}: {
  icon: LucideIcon;
  label: string;
  danger?: boolean;
  disabled?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "flex min-h-10 w-full items-center gap-2.5 rounded-xl px-3 text-left text-xs transition-colors disabled:opacity-50",
        danger
          ? "text-danger hover:bg-danger/10"
          : "text-muted-foreground hover:bg-muted hover:text-foreground",
      )}
    >
      <Icon className="h-4 w-4 shrink-0" />
      {label}
    </button>
  );
}

export function UserMenu() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { auth, setAuth } = useAuth();
  const [open, setOpen] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [deleteSent, setDeleteSent] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [pending, setPending] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  // 点卡片外或按 Escape 收起；不监听 scroll —— 顶栏是 sticky，不会错位
  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (!auth) return null;
  const { user } = auth;
  const name = user.display_name || user.email;
  const initial = name.trim().charAt(0).toUpperCase() || "?";
  const avatar = user.avatar ? (
    <img src={user.avatar} alt="" className="h-full w-full rounded-full object-cover" />
  ) : (
    initial
  );

  const go = (to: string) => {
    setOpen(false);
    navigate(to);
  };

  const leave = async () => {
    setPending(true);
    try {
      await logout();
      setAuth(null);
      navigate("/auth/login", { replace: true });
    } finally {
      setPending(false);
    }
  };

  const destroy = async () => {
    setPending(true);
    setDeleteError("");
    try {
      // 注销要走邮件确认：这里只发确认信，账号在点邮件链接后才真正删除
      const result = await requestAccountDeletion();
      setDeleteSent(
        result.development_verification_token
          ? t("nav:userMenu.deleteSentDev")
          : t("nav:userMenu.deleteSent", { email: result.email }),
      );
    } catch (reason) {
      setDeleteError(reason instanceof Error ? reason.message : t("auth:errors.unknown"));
    } finally {
      setPending(false);
    }
  };

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={t("nav:userMenu.open")}
        className="flex h-10 items-center gap-1.5 rounded-xl border border-border bg-surface pl-1.5 pr-2 text-muted-foreground transition-colors hover:border-border-active hover:text-foreground"
      >
        <span className="flex h-7 w-7 items-center justify-center overflow-hidden rounded-full bg-primary/15 text-xs font-semibold text-primary">
          {avatar}
        </span>
        <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", open && "rotate-180")} />
      </button>

      {open ? (
        <div
          role="menu"
          aria-label={t("nav:userMenu.open")}
          className="absolute right-0 top-[calc(100%+8px)] z-50 w-72 rounded-2xl border border-border bg-card p-2 shadow-2xl"
        >
          <div className="flex items-center gap-3 rounded-xl bg-background/60 p-3">
            <span className="flex h-10 w-10 shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/15 text-sm font-semibold text-primary">
              {avatar}
            </span>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">{name}</p>
              <p className="truncate text-[11px] text-muted-foreground">{user.email}</p>
            </div>
            <span className="ml-auto rounded-md border border-border px-1.5 py-0.5 font-mono text-[9px] uppercase tracking-wider text-muted-foreground">
              {auth.membership.role}
            </span>
          </div>
          <div className="mt-2 space-y-0.5">
            <MenuItem
              icon={UserRound}
              label={t("nav:userMenu.profile")}
              onClick={() => go("/settings/profile")}
            />
            <MenuItem
              icon={Settings}
              label={t("nav:userMenu.settings")}
              onClick={() => go("/settings")}
            />
          </div>
          <div className="my-2 h-px bg-border" />
          <div className="space-y-0.5">
            <MenuItem
              icon={LogOut}
              label={t("nav:userMenu.logout")}
              disabled={pending}
              onClick={() => void leave()}
            />
            <MenuItem
              icon={ArrowLeftRight}
              label={t("nav:userMenu.switchAccount")}
              disabled={pending}
              onClick={() => void leave()}
            />
            <MenuItem
              icon={Trash2}
              danger
              label={t("nav:userMenu.deleteAccount")}
              disabled={pending}
              onClick={() => {
                setOpen(false);
                setDeleteSent("");
                setDeleteError("");
                setConfirmingDelete(true);
              }}
            />
          </div>
        </div>
      ) : null}

      <Dialog
        open={confirmingDelete}
        onOpenChange={setConfirmingDelete}
        title={t("nav:userMenu.deleteTitle")}
        description={t("nav:userMenu.deleteBody")}
      >
        {deleteSent ? (
          <p
            className="rounded-xl border border-primary/25 bg-primary/5 px-3 py-2 text-xs leading-5"
            role="status"
          >
            {deleteSent}
          </p>
        ) : (
          <>
            {deleteError ? (
              <p
                className="mb-3 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
                role="alert"
              >
                {deleteError}
              </p>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setConfirmingDelete(false)}>
                {t("common:cancel")}
              </Button>
              <Button
                variant="destructive"
                size="sm"
                disabled={pending}
                onClick={() => void destroy()}
              >
                {t("nav:userMenu.deleteConfirm")}
              </Button>
            </div>
          </>
        )}
      </Dialog>
    </div>
  );
}
