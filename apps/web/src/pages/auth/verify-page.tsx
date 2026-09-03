import { CheckCircle2, LoaderCircle, MailCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { verifyEmail } from "../../api/auth";
import { Button } from "../../components/common/button";
import { useAuth } from "../../features/auth/auth-context";

export function VerifyPage() {
  const { t } = useTranslation("auth");
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { setAuth } = useAuth();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const token = params.get("token");
  const email = params.get("email");

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
        ) : null}
        <Link className="mt-5 inline-block text-xs text-primary hover:underline" to="/auth/login">
          {t("verify.back")}
        </Link>
      </div>
    </>
  );
}
