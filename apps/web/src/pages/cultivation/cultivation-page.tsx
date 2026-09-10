import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Plus } from "lucide-react";
import { useCultivationCharacters } from "../../hooks/useCultivation";
import { Button } from "../../components/common/button";
import { EmptyState, ErrorState } from "../../components/common/states";
import { Skeleton } from "../../components/common/skeleton";
import { CharacterCard } from "../../components/cultivation/character-card";
import { CreateCharacterDialog } from "../../components/cultivation/create-character-dialog";

/**
 * 角色培养列表：本公司持有的角色（trained / blank）。
 *
 * 发行方生成（issued）与挂牌交易（listed/hired）属 T2，这里只出现玩家自己培养的角色。
 */
export function CultivationPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const charactersQuery = useCultivationCharacters();
  const [createOpen, setCreateOpen] = useState(false);

  const characters = charactersQuery.data ?? [];

  return (
    <div className="space-y-5 panel-enter">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold">{t("cultivation:title")}</h1>
          <p className="mt-1 max-w-2xl text-xs leading-5 text-muted-foreground">
            {t("cultivation:description")}
          </p>
        </div>
        <Button size="sm" data-testid="create-character" onClick={() => setCreateOpen(true)}>
          <Plus className="h-3.5 w-3.5" />
          {t("cultivation:newCharacter")}
        </Button>
      </header>

      {charactersQuery.isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
          <Skeleton className="h-36 w-full" />
        </div>
      ) : charactersQuery.isError ? (
        <ErrorState error={charactersQuery.error} onRetry={() => charactersQuery.refetch()} />
      ) : characters.length === 0 ? (
        <EmptyState title={t("cultivation:emptyTitle")} hint={t("cultivation:emptyHint")} />
      ) : (
        <>
          <p className="text-xs text-muted-foreground">
            {t("cultivation:summary", { count: characters.length })}
          </p>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {characters.map((character) => (
              // 列表接口不返回 program 明细：卡片只用角色本体，阶段进度在详情页看
              <CharacterCard key={character.id} character={character} />
            ))}
          </div>
        </>
      )}

      <CreateCharacterDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
        onCreated={(characterId) => navigate(`/cultivation/${characterId}`)}
      />
    </div>
  );
}
