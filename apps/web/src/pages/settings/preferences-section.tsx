import { Moon, Sun } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../../components/common/button";
import { Card, CardContent, CardHeader, CardTitle } from "../../components/common/card";
import { LanguageSelector } from "../../components/common/language-selector";
import { useThemeStore } from "../../stores/theme";

/** 界面偏好：主题与语言，保存在本地、即时生效。 */
export function PreferencesSection() {
  const { t } = useTranslation();
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);

  return (
    <div className="space-y-5">
      <Card>
        <CardHeader>
          <CardTitle>{t("settings:appearance")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">
            {t("settings:themeLine", { theme: t(`settings:theme.${theme}`) })}
          </p>
          <Button variant="outline" size="sm" onClick={toggleTheme}>
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            {t("settings:switchTheme", {
              theme: t(`settings:theme.${theme === "dark" ? "light" : "dark"}`),
            })}
          </Button>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>{t("settings:language")}</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm text-muted-foreground">{t("settings:languageHint")}</p>
          <LanguageSelector />
        </CardContent>
      </Card>
    </div>
  );
}
