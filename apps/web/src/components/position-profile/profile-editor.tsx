import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useCompetencyDomains } from "../../hooks/useCompetencies";
import { useProfileMutations, useProfileTemplates } from "../../hooks/usePositionProfiles";
import type { ProfileRequirement, PositionCompetencyProfile } from "../../types";

function numberOrNull(value: string): number | null {
  return value === "" ? null : Number(value);
}

/**
 * P7 画像编辑器：Add（从目录选择，不允许手输）、Draft 行删除、Publish/Retire/Clone。
 * 只对状态为 draft 的版本开放；正式标准修改 = 新建版本。
 */
export function ProfileEditor({
  profile,
  positionId,
  definitions,
}: {
  profile: PositionCompetencyProfile;
  positionId: number;
  definitions: Array<{ id: number; code: string; name: string; domain_id: number }>;
}) {
  const { t } = useTranslation();
  const domainsQuery = useCompetencyDomains();
  const templatesQuery = useProfileTemplates();
  const mutations = useProfileMutations(positionId);

  const draftVersion = profile.versions.find((version) => version.status === "draft") ?? null;
  const activeVersion = profile.versions.find((version) => version.status === "active") ?? null;

  const [kind, setKind] = useState<"general" | "professional">("general");
  const [competencyId, setCompetencyId] = useState("");
  const [requirementType, setRequirementType] = useState<"required" | "preferred">("required");
  const [minimum, setMinimum] = useState("");
  const [target, setTarget] = useState("");
  const [confidence, setConfidence] = useState("");
  const [weight, setWeight] = useState("");
  const [critical, setCritical] = useState(false);
  const [templateId, setTemplateId] = useState("");

  const domains = domainsQuery.data ?? [];
  const kindDomains = domains.filter((domain) => domain.kind === kind);
  const already = new Set(profile.general.concat(profile.professional).map((r) => r.code));
  const pickable = definitions.filter(
    (definition) =>
      kindDomains.some((domain) => domain.id === definition.domain_id) &&
      !already.has(definition.code),
  );

  const add = () => {
    if (!draftVersion || !competencyId) return;
    mutations.addRequirement.mutate({
      versionId: draftVersion.id,
      body: {
        competency_definition_id: Number(competencyId),
        requirement_type: requirementType,
        minimum_score: numberOrNull(minimum),
        target_score: numberOrNull(target),
        minimum_confidence: numberOrNull(confidence),
        critical,
        weight: numberOrNull(weight) ?? 1,
      },
    });
    setCompetencyId("");
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-muted-foreground">{t("position:positions.editorHint")}</p>

      {!draftVersion ? (
        <div className="flex flex-wrap gap-2">
          <button
            onClick={() => mutations.createDraft.mutate()}
            className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
          >
            {t("position:positions.createDraft")}
          </button>
          <select
            value={templateId}
            onChange={(event) => setTemplateId(event.target.value)}
            className="rounded-md border border-border bg-background px-2 py-1.5 text-xs"
          >
            <option value="">{t("position:positions.cloneFrom")}…</option>
            {(templatesQuery.data ?? []).map((template) => (
              <option key={template.template_version_id} value={template.template_version_id}>
                {template.position_name} v{template.version} ({template.requirement_count} req)
              </option>
            ))}
          </select>
          {templateId ? (
            <button
              onClick={() => mutations.clone.mutate(Number(templateId))}
              className="rounded-md border border-border px-3 py-1.5 text-xs hover:bg-muted"
            >
              {t("position:positions.clone")}
            </button>
          ) : null}
        </div>
      ) : null}

      {draftVersion ? (
        <>
          <div className="grid gap-2 rounded-md border border-primary/30 bg-primary/5 p-3 sm:grid-cols-2 lg:grid-cols-4">
            <select
              value={kind}
              onChange={(event) => setKind(event.target.value as "general" | "professional")}
              className="rounded-md border border-border px-2 py-1.5 text-xs"
            >
              <option value="general">{t("position:positions.general")}</option>
              <option value="professional">{t("position:positions.professional")}</option>
            </select>
            <select
              value={competencyId}
              onChange={(event) => setCompetencyId(event.target.value)}
              className="rounded-md border border-border px-2 py-1.5 text-xs"
            >
              <option value="">{t("position:positions.selectCompetency")}</option>
              {pickable.map((definition) => (
                <option key={definition.id} value={definition.id}>
                  {definition.name} ({definition.code})
                </option>
              ))}
            </select>
            <select
              value={requirementType}
              onChange={(event) =>
                setRequirementType(event.target.value as "required" | "preferred")
              }
              className="rounded-md border border-border px-2 py-1.5 text-xs"
            >
              <option value="required">{t("position:positions.required")}</option>
              <option value="preferred">{t("position:positions.preferred")}</option>
            </select>
            <div className="flex gap-2">
              <input
                value={minimum}
                onChange={(event) => setMinimum(event.target.value)}
                placeholder={t("position:positions.minimum")}
                className="w-full rounded-md border border-border px-2 py-1.5 text-xs"
              />
              <input
                value={target}
                onChange={(event) => setTarget(event.target.value)}
                placeholder={t("position:positions.target")}
                className="w-full rounded-md border border-border px-2 py-1.5 text-xs"
              />
            </div>
            <div className="flex gap-2">
              <input
                value={confidence}
                onChange={(event) => setConfidence(event.target.value)}
                placeholder={t("position:positions.confidence")}
                className="w-full rounded-md border border-border px-2 py-1.5 text-xs"
              />
              <input
                value={weight}
                onChange={(event) => setWeight(event.target.value)}
                placeholder={t("position:positions.weight")}
                className="w-full rounded-md border border-border px-2 py-1.5 text-xs"
              />
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={critical}
                  onChange={(event) => setCritical(event.target.checked)}
                />
                {t("position:positions.critical")}
              </label>
            </div>
            <button
              onClick={add}
              disabled={!competencyId}
              className="rounded-md border border-primary/40 px-3 py-1.5 text-xs text-primary disabled:opacity-40"
            >
              {t("position:positions.addRequirement")}
            </button>
          </div>

          <div className="flex flex-wrap gap-2">
            <button
              onClick={() =>
                mutations.publish.mutate({
                  versionId: draftVersion.id,
                  note: t("position:positions.confirmPublish"),
                })
              }
              className="rounded-md bg-primary px-3 py-1.5 text-xs text-primary-foreground"
            >
              {t("position:positions.publish")}
            </button>
            <div className="flex flex-wrap gap-1">
              {profile.general
                .concat(profile.professional)
                .map((requirement: ProfileRequirement) => (
                  <button
                    key={requirement.id}
                    onClick={() =>
                      mutations.removeRequirement.mutate({
                        versionId: draftVersion.id,
                        requirementId: requirement.id,
                      })
                    }
                    className="rounded-md border border-border px-2 py-1 text-[11px] text-muted-foreground hover:text-destructive"
                  >
                    × {requirement.code}
                  </button>
                ))}
            </div>
          </div>
        </>
      ) : null}

      {activeVersion ? (
        <button
          onClick={() => mutations.retire.mutate({ versionId: activeVersion.id, note: "" })}
          className="rounded-md border border-border px-3 py-1.5 text-xs text-muted-foreground hover:text-destructive"
        >
          {t("position:positions.retire")}
        </button>
      ) : null}
    </div>
  );
}
