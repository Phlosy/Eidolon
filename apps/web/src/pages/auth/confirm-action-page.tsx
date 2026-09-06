import { CheckCircle2, LoaderCircle, MailCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { confirmAccountAction } from "../../api/auth";
import { Button } from "../../components/common/button";
import { useAuth } from "../../features/auth/auth-context";

const KNOWN_ACTIONS = new Set(["change_email", "change_password", "delete_account"]);

/**
 * 账户安全操作的统一确认页：改邮箱 / 改密码 / 注销账号的邮件链接都落到这里。
 * 公开路由 —— 点链接时可能已登录也可能没有；token 本身就是"拥有该邮箱"的证明。
 * 改密码与注销确认后所有会话都被吊销，本地状态也要同步清掉。
 */
export function ConfirmActionPage() {
  const { t } = useTranslation("auth");
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { auth, setAuth, refresh } = useAuth();
  const [pending, setPending] = useState(false);
  const [done, setDone] = useState("");
  const [error, setError] = useState("");
  const token = params.get("token");
  const email = params.get("email");
  const actionParam = params.get("action") ?? "";
  const action = KNOWN_ACTIONS.has(actionParam) ? actionParam : "";

  const confirm = async () => {
    if (!token || !action) return;
    setPending(true);
    setError("");
    try {
      const result = await confirmAccountAction(token);
      // 改密码 / 注销会吊销全部会话（含当前）；改邮箱只需刷新资料
      if (result.action === "change_password" || result.action === "delete_account") {
        setAuth(null);
      } else if (auth) {
        await refresh();
      }
      setDone(result.action);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("errors.unknown"));
    } finally {
      setPending(false);
    }
  };

  const continueTo =
    done === "delete_account"
      ? "/auth/register"
      : done === "change_password"
        ? "/auth/login"
        : auth
          ? "/settings/profile"
          : "/auth/login";

  return (
    <div className="flex min-h-screen items-center justify-center bg-background p-4">
      <div className="w-full max-w-md rounded-2xl border border-border bg-card p-8 text-center shadow-2xl">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          {done ? <CheckCircle2 className="h-6 w-6" /> : <MailCheck className="h-6 w-6" />}
        </span>
        <h2 className="mt-5 text-2xl font-semibold tracking-[-.02em]">
          {done ? t(`accountAction.${done}.done`) : t(`accountAction.${action || "invalid"}.title`)}
        </h2>
        {!done && action ? (
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {t(`accountAction.${action}.ready`, { email })}
          </p>
        ) : null}
        {!done && !action ? (
          <p className="mt-3 text-sm leading-6 text-muted-foreground">
            {t("accountAction.invalid.ready")}
          </p>
        ) : null}
        {error ? (
          <p
            className="mt-5 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
            role="alert"
          >
            {error}
          </p>
        ) : null}
        {token && action && !done ? (
          <Button className="mt-7 h-12 w-full" onClick={confirm} disabled={pending}>
            {pending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            {t(`accountAction.${action}.confirm`)}
          </Button>
        ) : null}
        {done ? (
          <Button
            className="mt-7 h-12 w-full"
            onClick={() => navigate(continueTo, { replace: true })}
          >
            {t("accountAction.continue")}
          </Button>
        ) : null}
        {!done ? (
          <Link
            className="mt-5 inline-block text-xs text-primary hover:underline"
            to={auth ? "/settings/profile" : "/auth/login"}
          >
            {t("accountAction.back")}
          </Link>
        ) : null}
      </div>
    </div>
  );
}
