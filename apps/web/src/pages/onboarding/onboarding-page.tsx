import { Building2, LoaderCircle } from "lucide-react";
import { useState, type FormEvent } from "react";
import { useTranslation } from "react-i18next";
import { Navigate, useNavigate } from "react-router-dom";
import { auth as authApi, companies as companiesApi } from "../../api";
import { Button } from "../../components/common/button";
import { Input } from "../../components/common/input";
import { ErrorNote } from "../../components/app/widgets";
import { useAuth } from "../../features/auth/auth-context";
import { useCompany } from "../../features/company/company-context";
import { slugify } from "../../utils/slug";

/** A signed-in user with no company founds one; the owner employment follows. */
export function OnboardingPage() {
  const { t } = useTranslation("app");
  const { setMe } = useAuth();
  const { current, select } = useCompany();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [currency, setCurrency] = useState("CNY");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [key] = useState(() => crypto.randomUUID());
  if (current) return <Navigate to="/" replace />;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setPending(true);
    setError(null);
    try {
      const created = await companiesApi.create(
        { name, slug, currency, template_id: "software-studio-v1" },
        key,
      );
      // the identity projection seats the owner; poll /auth/me until it shows
      for (let i = 0; i < 20; i += 1) {
        const me = await authApi.me();
        if (me.companies.some((c) => c.company_id === created.company_id)) {
          setMe(me);
          select(created.company_id);
          navigate("/", { replace: true });
          return;
        }
        await new Promise((r) => setTimeout(r, 500));
      }
      setMe(await authApi.me());
    } catch (reason) {
      setError(reason);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-background px-4">
      <div className="command-panel w-full max-w-lg p-6 md:p-8">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="type-kicker text-primary">{t("onboarding.kicker")}</p>
            <h1 className="mt-3 text-2xl font-semibold tracking-tight">{t("onboarding.title")}</h1>
            <p className="mt-2 text-sm text-muted-foreground">{t("onboarding.description")}</p>
          </div>
          <span className="flex h-11 w-11 shrink-0 items-center justify-center rounded-2xl bg-primary/10 text-primary">
            <Building2 className="h-5 w-5" />
          </span>
        </div>
        <form onSubmit={submit} className="mt-6 space-y-4">
          <label className="block text-xs font-medium" htmlFor="company-name">
            {t("onboarding.name")}
            <Input
              id="company-name"
              className="mt-2 h-11"
              required
              value={name}
              onChange={(e) => {
                setName(e.target.value);
                if (!slug || slug === slugify(name)) setSlug(slugify(e.target.value));
              }}
            />
          </label>
          <label className="block text-xs font-medium" htmlFor="company-slug">
            {t("onboarding.slug")}
            <Input
              id="company-slug"
              className="mt-2 h-11 font-mono"
              required
              pattern="[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?"
              value={slug}
              onChange={(e) => setSlug(e.target.value)}
            />
          </label>
          <label className="block text-xs font-medium" htmlFor="company-currency">
            {t("onboarding.currency")}
            <select
              id="company-currency"
              className="mt-2 h-11 w-full rounded-md border border-border bg-transparent px-3 text-sm [&>option]:bg-card"
              value={currency}
              onChange={(e) => setCurrency(e.target.value)}
            >
              <option value="CNY">CNY</option>
              <option value="USD">USD</option>
            </select>
          </label>
          <p className="text-[11px] text-muted-foreground">
            {t("onboarding.template")}: {t("onboarding.templateHint")}
          </p>
          <ErrorNote error={error} />
          <Button className="h-12 w-full" type="submit" disabled={pending}>
            {pending ? <LoaderCircle className="h-4 w-4 animate-spin" /> : null}
            {t("onboarding.submit")}
          </Button>
        </form>
      </div>
    </div>
  );
}
