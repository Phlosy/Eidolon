import { CheckCircle2, LoaderCircle, MailCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { resendVerificationEmail, verifyEmail } from "../../api/auth";
import { Button } from "../../components/common/button";
import { useAuth } from "../../features/auth/auth-context";

const RESEND_COOLDOWN_SECONDS = 60;

export function VerifyPage() {
  const { t } = useTranslation("auth");
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setAuth } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const token = params.get("token");
  const email = params.get("email");
  const devMode = params.get("dev") === "1";

  // 重发冷却：刚从注册页过来时邮件刚发过，从 60s 开始倒数
  const [cooldown, setCooldown] = useState(RESEND_COOLDOWN_SECONDS);
  const [resending, setResending] = useState(false);
  const [resent, setResent] = useState(false);
  const [resendError, setResendError] = useState("");
  useEffect(() => {
    if (token || cooldown <= 0) return undefined;
    const timer = window.setTimeout(() => setCooldown((value) => value - 1), 1000);
    return () => window.clearTimeout(timer);
  }, [token, cooldown]);

  const verify = async () => {
    if (!token) return;
    setPending(true);
    setError("");
    try {
      const auth = await verifyEmail(token);
      setAuth(auth);
      navigate("/", { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("errors.unknown"));
    } finally {
      setPending(false);
    }
  };

  const resend = async () => {
    if (!email || cooldown > 0) return;
    setResending(true);
    setResendError("");
    try {
      await resendVerificationEmail(email);
      setResent(true);
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (reason) {
      // 429 = 冷却未到（理论上按钮已禁用，兜底提示）
      setResendError(reason instanceof Error ? reason.message : t("errors.unknown"));
    } finally {
      setResending(false);
    }
  };

  return (
    <>
      <div className="text-center">
        <span className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          {token ? <CheckCircle2 className="h-6 w-6" /> : <MailCheck className="h-6 w-6" />}
        </span>
        <p className="type-kicker mt-7 text-primary">{t("verify.kicker")}</p>
        <h2 className="mt-3 text-3xl font-semibold tracking-[-.04em]">{t("verify.title")}</h2>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">
          {token ? t("verify.ready", { email }) : t("verify.sent", { email })}
        </p>
        {error ? (
          <p
            className="mt-5 rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
            role="alert"
          >
            {error}
          </p>
        ) : null}
        {token ? (
          <Button className="mt-7 h-12 w-full" onClick={verify} disabled={pending}>
            {pending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            {t("verify.confirm")}
          </Button>
        ) : (
          <div className="mt-7 space-y-3">
            {devMode ? (
              <p className="rounded-xl border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                {t("verify.devHint")}
              </p>
            ) : null}
            {resent && !resendError ? (
              <p className="text-xs text-success" role="status">
                {t("verify.resent")}
              </p>
            ) : null}
            {resendError ? (
              <p className="text-xs text-danger" role="alert">
                {resendError}
              </p>
            ) : null}
            <Button
              variant="outline"
              className="h-11 w-full"
              onClick={resend}
              disabled={resending || cooldown > 0 || !email}
            >
              {resending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
              {cooldown > 0 ? t("verify.resendIn", { seconds: cooldown }) : t("verify.resend")}
            </Button>
          </div>
        )}
        <Link className="mt-5 inline-block text-xs text-primary hover:underline" to="/auth/login">
          {t("verify.back")}
        </Link>
      </div>
    </>
  );
}
