import { ArrowRight, KeyRound, LoaderCircle, LockKeyhole, Mail } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { auth as authApi } from "../../api";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { useAuth } from "../../features/auth/auth-context";

export function LoginPage() {
  const { t } = useTranslation("auth");
  const { setMe } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const destination = (location.state as { from?: string } | null)?.from ?? "/";

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    setPending(true);
    try {
      await authApi.login(email, password);
      setMe(await authApi.me());
      navigate(destination, { replace: true });
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
          <p className="type-kicker text-primary">{t("login.kicker")}</p>
          <h2 className="mt-3 text-3xl font-semibold tracking-[-.04em]">{t("login.title")}</h2>
          <p className="mt-2 text-sm leading-6 text-muted-foreground">{t("login.description")}</p>
        </div>
        <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
          <KeyRound className="h-5 w-5" />
        </span>
      </div>
      <form onSubmit={submit} className="mt-7 space-y-4">
        <label className="block text-xs font-medium" htmlFor="login-email">
          {t("fields.email")}
          <span className="relative mt-2 block">
            <Mail className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              id="login-email"
              className="h-11 pl-10"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
            />
          </span>
        </label>
        <label className="block text-xs font-medium" htmlFor="login-password">
          {t("fields.password")}
          <span className="relative mt-2 block">
            <LockKeyhole className="pointer-events-none absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <Input
              id="login-password"
              className="h-11 pl-10"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </span>
        </label>
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
          {t("login.submit")}
          <ArrowRight className="h-4 w-4" />
        </Button>
      </form>
      <p className="mt-6 text-center text-xs text-muted-foreground">
        {t("login.newHere")}{" "}
        <Link to="/auth/register" className="font-medium text-primary hover:underline">
          {t("login.create")}
        </Link>
      </p>
    </>
  );
}
