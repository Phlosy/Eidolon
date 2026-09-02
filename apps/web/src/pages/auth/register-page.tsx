import {
  ArrowRight,
  Building2,
  Check,
  Fingerprint,
  LoaderCircle,
  LockKeyhole,
  Mail,
  UserRound,
} from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { registerAccount } from "../../api/auth";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { AuthShell } from "../../features/auth/auth-shell";
import { passwordIssues } from "../../features/auth/password-policy";

export function RegisterPage() {
  const { t, i18n } = useTranslation("auth");
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (passwordIssues(password).length) return setError(t("errors.passwordLength"));
    if (password !== confirm) return setError(t("errors.passwordMismatch"));
    setPending(true);
    setError("");
    try {
      const result = await registerAccount({
        email,
        password,
        display_name: displayName,
        locale: i18n.language.startsWith("zh") ? "zh-CN" : "en-US",
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
      const query = new URLSearchParams({ email: result.email });
      if (result.development_verification_token)
        query.set("token", result.development_verification_token);
      navigate(`/auth/verify?${query}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("errors.unknown"));
    } finally {
      setPending(false);
    }
  };

  return (
    <AuthShell>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="type-kicker text-primary">{t("register.kicker")}</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-.04em]">{t("register.title")}</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">
            {t("register.description")}
          </p>
        </div>
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <Building2 className="h-5 w-5" />
        </span>
      </div>
      <Link
        to="/auth/login"
        className="mt-7 flex h-12 w-full items-center justify-center gap-2 rounded-xl border border-primary/30 bg-primary/5 text-sm font-medium text-primary transition-colors hover:bg-primary/10"
      >
        <Fingerprint className="h-4 w-4" />
        {t("register.usePasskey")}
      </Link>
      <div className="my-5 flex items-center gap-3 text-[10px] uppercase tracking-[.16em] text-muted-foreground">
        <span className="h-px flex-1 bg-border" />
        {t("login.or")}
        <span className="h-px flex-1 bg-border" />
      </div>
      <form onSubmit={submit} className="space-y-4">
        <AuthField id="register-name" label={t("fields.name")} icon={UserRound}>
          <Input
            id="register-name"
            className="h-11 pl-10"
            autoComplete="name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </AuthField>
        <AuthField id="register-email" label={t("fields.email")} icon={Mail}>
          <Input
            id="register-email"
            className="h-11 pl-10"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </AuthField>
        <AuthField id="register-password" label={t("fields.password")} icon={LockKeyhole}>
          <Input
            id="register-password"
            className="h-11 pl-10"
            type="password"
            autoComplete="new-password"
            minLength={12}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </AuthField>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Check className={`h-3.5 w-3.5 ${password.length >= 12 ? "text-success" : ""}`} />
          {t("register.passwordHint")}
        </div>
        <AuthField id="register-confirm" label={t("fields.confirmPassword")} icon={LockKeyhole}>
          <Input
            id="register-confirm"
            className="h-11 pl-10"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </AuthField>
        {error ? (
          <p
            className="rounded-xl border border-danger/30 bg-danger/10 px-3 py-2 text-xs text-danger"
            role="alert"
          >
            {error}
          </p>
        ) : null}
        <Button className="h-12 w-full" type="submit" disabled={pending}>
          {pending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
          {t("register.submit")}
          <ArrowRight className="h-4 w-4" />
        </Button>
      </form>
      <p className="mt-6 text-center text-xs text-muted-foreground">
        {t("register.haveAccount")}{" "}
        <Link to="/auth/login" className="font-medium text-primary hover:underline">
          {t("register.signIn")}
        </Link>
      </p>
    </AuthShell>
  );
}

function AuthField({
  id,
  label,
  icon: Icon,
  children,
}: {
  id: string;
  label: string;
  icon: typeof Mail;
  children: React.ReactNode;
}) {
  return (
    <label className="block text-xs font-medium" htmlFor={id}>
      {label}
      <span className="relative mt-2 block">
        <Icon className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
        {children}
      </span>
    </label>
  );
}
