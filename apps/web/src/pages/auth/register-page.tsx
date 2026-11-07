import {
  ArrowRight,
  Building2,
  Check,
  LoaderCircle,
  LockKeyhole,
  Mail,
  UserRound,
} from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router-dom";
import { auth as authApi } from "../../api";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { useAuth } from "../../features/auth/auth-context";
import { PASSWORD_MIN_LENGTH, passwordIssues } from "../../features/auth/password-policy";

export function RegisterPage() {
  const { t } = useTranslation("auth");
  const { setMe } = useAuth();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState("");
  const [email, setEmail] = useState("");
  const [personId, setPersonId] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const passwordIsValid = passwordIssues(password).length === 0;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!passwordIsValid) return setError(t("errors.passwordRequirements"));
    if (password !== confirm) return setError(t("errors.passwordMismatch"));
    setPending(true);
    setError("");
    try {
      await authApi.register({
        email,
        password,
        display_name: displayName,
        person_id: personId.trim() || undefined,
      });
      setMe(await authApi.me());
      navigate("/", { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : t("errors.unknown"));
    } finally {
      setPending(false);
    }
  };

  return (
    <>
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
      <form onSubmit={submit} className="mt-7 space-y-4">
        <Field id="register-name" label={t("fields.name")} icon={<UserRound className="icon" />}>
          <Input
            id="register-name"
            className="h-11 pl-10"
            autoComplete="name"
            required
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
          />
        </Field>
        <Field id="register-email" label={t("fields.email")} icon={<Mail className="icon" />}>
          <Input
            id="register-email"
            className="h-11 pl-10"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
        </Field>
        <Field
          id="register-person"
          label={t("fields.personId")}
          icon={<UserRound className="icon" />}
        >
          <Input
            id="register-person"
            className="h-11 pl-10"
            pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
            value={personId}
            onChange={(event) => setPersonId(event.target.value)}
          />
        </Field>
        <p className="text-[11px] text-muted-foreground">{t("register.personHint")}</p>
        <Field
          id="register-password"
          label={t("fields.password")}
          icon={<LockKeyhole className="icon" />}
        >
          <Input
            id="register-password"
            className="h-11 pl-10"
            type="password"
            autoComplete="new-password"
            minLength={PASSWORD_MIN_LENGTH}
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />
        </Field>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <Check className={`h-3.5 w-3.5 ${passwordIsValid ? "text-success" : ""}`} />
          {t("register.passwordHint")}
        </div>
        <Field
          id="register-confirm"
          label={t("fields.confirmPassword")}
          icon={<LockKeyhole className="icon" />}
        >
          <Input
            id="register-confirm"
            className="h-11 pl-10"
            type="password"
            autoComplete="new-password"
            required
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
          />
        </Field>
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
    </>
  );
}

function Field({
  id,
  label,
  icon,
  children,
}: {
  id: string;
  label: string;
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block text-xs font-medium" htmlFor={id}>
      {label}
      <span className="relative mt-2 block [&>.icon]:pointer-events-none [&>.icon]:absolute [&>.icon]:left-3 [&>.icon]:top-3 [&>.icon]:h-4 [&>.icon]:w-4 [&>.icon]:text-muted-foreground">
        {icon}
        {children}
      </span>
    </label>
  );
}
