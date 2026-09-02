import { Pause, Play } from "lucide-react";
import {
  type Dispatch,
  type PointerEvent,
  type ReactNode,
  type SetStateAction,
  useState,
} from "react";
import { useTranslation } from "react-i18next";

type CharacterId = "engineer" | "operations" | "project";
const characterIds = [
  "engineer",
  "operations",
  "project",
] as const satisfies readonly CharacterId[];

type PixelCharacterProps = {
  activeCharacter: CharacterId | null;
  children: ReactNode;
  className?: string;
  id: CharacterId;
};

function PixelCharacter({ activeCharacter, children, className = "", id }: PixelCharacterProps) {
  const isSpeaking = activeCharacter === id;

  return (
    <g className={`pixel-character ${className}${isSpeaking ? " is-speaking" : ""}`}>{children}</g>
  );
}

type CharacterHotspotProps = {
  activeCharacter: CharacterId | null;
  id: CharacterId;
  label: string;
  setActiveCharacter: Dispatch<SetStateAction<CharacterId | null>>;
};

function CharacterHotspot({
  activeCharacter,
  id,
  label,
  setActiveCharacter,
}: CharacterHotspotProps) {
  const isSpeaking = activeCharacter === id;
  const showDialogue = () => setActiveCharacter(id);
  const hideDialogue = () => {
    setActiveCharacter((current) => (current === id ? null : current));
  };

  const handlePointerLeave = (event: PointerEvent<HTMLButtonElement>) => {
    if (document.activeElement !== event.currentTarget) hideDialogue();
  };

  return (
    <button
      type="button"
      className={`pixel-character-hotspot pixel-character-hotspot-${id}${isSpeaking ? " is-speaking" : ""}`}
      aria-label={label}
      aria-expanded={isSpeaking}
      onBlur={hideDialogue}
      onClick={showDialogue}
      onFocus={showDialogue}
      onPointerEnter={showDialogue}
      onPointerLeave={handlePointerLeave}
    />
  );
}

export function PixelOfficeScene() {
  const { t } = useTranslation("auth");
  const [manuallyPaused, setManuallyPaused] = useState(false);
  const [activeCharacter, setActiveCharacter] = useState<CharacterId | null>(null);
  const characters = {
    engineer: {
      name: t("shell.office.characters.engineer.name"),
      role: t("shell.office.characters.engineer.role"),
      quote: t("shell.office.characters.engineer.quote"),
      label: t("shell.office.characters.engineer.label"),
    },
    operations: {
      name: t("shell.office.characters.operations.name"),
      role: t("shell.office.characters.operations.role"),
      quote: t("shell.office.characters.operations.quote"),
      label: t("shell.office.characters.operations.label"),
    },
    project: {
      name: t("shell.office.characters.project.name"),
      role: t("shell.office.characters.project.role"),
      quote: t("shell.office.characters.project.quote"),
      label: t("shell.office.characters.project.label"),
    },
  };
  const speaker = activeCharacter ? characters[activeCharacter] : null;

  return (
    <figure
      className={`pixel-office-frame${manuallyPaused ? " is-paused" : ""}`}
      data-testid="pixel-office-scene"
      aria-label={t("shell.office.alt")}
    >
      <div className="pixel-office-hud">
        <span className="pixel-office-status-dot" aria-hidden="true" />
        <span>{t("shell.office.status")}</span>
        <span className="pixel-office-hint">{t("shell.office.hint")}</span>
        <button
          type="button"
          className="pixel-office-motion-toggle"
          aria-label={t(manuallyPaused ? "shell.office.play" : "shell.office.pause")}
          aria-pressed={manuallyPaused}
          onClick={() => setManuallyPaused((value) => !value)}
        >
          {manuallyPaused ? (
            <Play className="h-3 w-3" aria-hidden="true" />
          ) : (
            <Pause className="h-3 w-3" aria-hidden="true" />
          )}
        </button>
      </div>

      <div
        className={`pixel-office-dialogue${speaker ? " is-visible" : ""}`}
        aria-live="polite"
        aria-hidden={!speaker}
      >
        {speaker ? (
          <>
            <p className="pixel-office-dialogue-name">
              {speaker.name} <span>{speaker.role}</span>
            </p>
            <p>{speaker.quote}</p>
          </>
        ) : null}
      </div>

      <div className="pixel-character-hotspots">
        {characterIds.map((id) => (
          <CharacterHotspot
            key={id}
            id={id}
            label={characters[id].label}
            activeCharacter={activeCharacter}
            setActiveCharacter={setActiveCharacter}
          />
        ))}
      </div>

      <svg className="pixel-office" viewBox="0 0 760 420" aria-hidden="true">
        <rect className="pixel-office-wall" width="760" height="226" />
        <path className="pixel-wallpaper" d="M0 26h760M0 74h760M0 122h760M0 170h760" />
        <rect className="pixel-wall-trim-dark" y="208" width="760" height="12" />
        <rect className="pixel-wall-trim-light" y="220" width="760" height="8" />

        <g className="pixel-window">
          <rect className="pixel-window-frame" x="26" y="24" width="190" height="126" />
          <rect className="pixel-window-sky" x="38" y="36" width="166" height="102" />
          <circle className="pixel-window-sun" cx="164" cy="65" r="18" />
          <path className="pixel-window-hills-back" d="M38 112l38-32 32 24 34-38 62 52v20H38z" />
          <path className="pixel-window-hills-front" d="M38 124l34-26 34 20 30-28 68 42v6H38z" />
          <path
            className="pixel-window-cloud"
            d="M54 58h12v-8h28v8h14v10H54zm60 22h10v-8h20v8h12v8h-42z"
          />
          <path className="pixel-window-cross" d="M116 32v110M32 88h178" />
          <path className="pixel-curtain pixel-curtain-left" d="M20 18h34v126H38l-18-18z" />
          <path className="pixel-curtain pixel-curtain-right" d="M188 18h34v108l-18 18h-16z" />
        </g>

        <g className="pixel-noticeboard">
          <rect className="pixel-noticeboard-frame" x="270" y="32" width="196" height="110" />
          <rect className="pixel-noticeboard-cork" x="280" y="42" width="176" height="90" />
          <path className="pixel-note pixel-note-a" d="M294 52h48v34h-48z" />
          <path className="pixel-note pixel-note-b" d="M360 64h34v48h-34z" />
          <path className="pixel-note pixel-note-c" d="M410 48h34v32h-34z" />
          <path
            className="pixel-note-lines"
            d="M302 62h30m-30 10h22m64 2h18m-18 10h18m30-26h20m-20 10h14"
          />
          <rect className="pixel-pin" x="312" y="48" width="6" height="6" />
          <rect className="pixel-pin" x="374" y="60" width="6" height="6" />
          <rect className="pixel-pin" x="424" y="44" width="6" height="6" />
        </g>

        <g className="pixel-clock">
          <rect x="500" y="42" width="54" height="54" />
          <rect className="pixel-clock-face" x="508" y="50" width="38" height="38" />
          <path className="pixel-clock-hands" d="M527 56v14l10 8" />
        </g>

        <g className="pixel-bookshelf">
          <rect className="pixel-shelf-frame" x="584" y="22" width="150" height="184" />
          <path className="pixel-shelf-board" d="M592 78h134v10H592zm0 58h134v10H592z" />
          <path
            className="pixel-book pixel-book-a"
            d="M602 40h14v38h-14zm18 8h14v30h-14zm20-12h18v42h-18z"
          />
          <path
            className="pixel-book pixel-book-b"
            d="M666 46h14v32h-14zm18-8h16v40h-16zm20 16h12v24h-12z"
          />
          <path
            className="pixel-book pixel-book-c"
            d="M602 100h18v36h-18zm22 12h14v24h-14zm18-18h16v42h-16z"
          />
          <rect className="pixel-storage-box" x="676" y="104" width="38" height="32" />
          <path
            className="pixel-book pixel-book-a"
            d="M604 158h14v38h-14zm18-8h18v46h-18zm22 14h14v32h-14z"
          />
          <path className="pixel-book pixel-book-b" d="M674 154h14v42h-14zm18 12h18v30h-18z" />
        </g>

        <rect className="pixel-office-floor" y="228" width="760" height="192" />
        <path
          className="pixel-floor-planks"
          d="M0 274h760M0 322h760M0 370h760M92 228v46m148 0v48m-92 0v48m150-142v46m152 0v48m-78 0v48m150-142v46m130 0v48m-66 0v48"
        />
        <rect className="pixel-rug-border" x="192" y="346" width="376" height="66" />
        <rect className="pixel-rug" x="204" y="356" width="352" height="46" />
        <path className="pixel-rug-pattern" d="M224 378h46l14-14 14 14h164l14-14 14 14h46" />

        <g className="pixel-plant">
          <path
            className="pixel-plant-leaves"
            d="M22 218h18v-28h14v28h18v-48h16v48h18v-32h16v48H22z"
          />
          <rect className="pixel-plant-pot-rim" x="34" y="234" width="70" height="14" />
          <path className="pixel-plant-pot" d="M42 248h54l-8 54H50z" />
        </g>

        <g className="pixel-desk pixel-desk-a">
          <path className="pixel-desk-edge" d="M64 284h248v20H64z" />
          <path className="pixel-desk-front" d="M72 304h232v16H72z" />
          <path className="pixel-desk-legs" d="M80 320h18v78H80zm188 0h18v78h-18z" />
          <rect className="pixel-monitor-shell" x="102" y="218" width="100" height="66" />
          <rect className="pixel-monitor-screen" x="112" y="228" width="80" height="44" />
          <path
            className="pixel-monitor-code"
            d="M120 238h24v6h-24zm0 12h56v6h-56zm0 12h38v5h-38z"
          />
          <path className="pixel-monitor-stand" d="M146 284v-12h14v12z" />
          <rect className="pixel-keyboard" x="174" y="278" width="74" height="10" />
          <rect className="pixel-mug" x="274" y="264" width="20" height="20" />
        </g>

        <g className="pixel-desk pixel-desk-b">
          <path className="pixel-desk-edge" d="M384 284h248v20H384z" />
          <path className="pixel-desk-front" d="M392 304h232v16H392z" />
          <path className="pixel-desk-legs" d="M402 320h18v78h-18zm186 0h18v78h-18z" />
          <rect className="pixel-monitor-shell" x="454" y="218" width="100" height="66" />
          <rect className="pixel-monitor-screen" x="464" y="228" width="80" height="44" />
          <path
            className="pixel-monitor-chart"
            d="M474 258v-10h10v10h8v-20h10v20h8v-14h10v14h12v6h-58z"
          />
          <path className="pixel-monitor-stand" d="M498 284v-12h14v12z" />
          <rect className="pixel-keyboard" x="530" y="278" width="66" height="10" />
          <rect className="pixel-paper-stack" x="400" y="264" width="42" height="20" />
        </g>

        <PixelCharacter id="engineer" activeCharacter={activeCharacter}>
          <g className="pixel-character-body">
            <path className="pixel-chair-back" d="M118 274h72v82h-16v-18h-56z" />
            <path className="pixel-trousers" d="M138 310h46v42h-18v-24h-10v24h-18z" />
            <path className="pixel-shirt pixel-shirt-engineer" d="M130 252h62v64h-62z" />
            <path className="pixel-neck" d="M150 242h20v18h-20z" />
            <path className="pixel-skin" d="M136 204h48v42h-8v10h-32v-10h-8z" />
            <path
              className="pixel-hair pixel-hair-engineer"
              d="M130 202h52v12h8v22h-12v-16h-10v-8h-26v10h-12z"
            />
            <rect className="pixel-eye" x="172" y="226" width="6" height="6" />
            <path className="pixel-arm pixel-arm-a" d="M184 266h16v12h28v10h-44z" />
            <path className="pixel-arm pixel-arm-b" d="M178 286h16v10h28v10h-44z" />
          </g>
        </PixelCharacter>

        <PixelCharacter id="operations" activeCharacter={activeCharacter}>
          <g className="pixel-character-body pixel-character-body-delay">
            <path className="pixel-chair-back" d="M548 274h66v82h-56v-18h-10z" />
            <path className="pixel-trousers" d="M540 310h46v42h-18v-24h-10v24h-18z" />
            <path className="pixel-shirt pixel-shirt-operations" d="M532 252h62v64h-62z" />
            <path className="pixel-neck" d="M552 242h20v18h-20z" />
            <path className="pixel-skin" d="M538 204h48v42h-8v10h-32v-10h-8z" />
            <path
              className="pixel-hair pixel-hair-operations"
              d="M532 202h60v12h-8v10h-8v-12h-34v26h-10z"
            />
            <rect className="pixel-eye" x="540" y="226" width="6" height="6" />
            <path className="pixel-arm pixel-arm-a" d="M530 266h-16v12h-28v10h44z" />
            <path className="pixel-arm pixel-arm-b" d="M536 286h-16v10h-28v10h44z" />
          </g>
        </PixelCharacter>

        <PixelCharacter
          id="project"
          activeCharacter={activeCharacter}
          className="pixel-character-walker"
        >
          <ellipse className="pixel-walker-shadow" cx="42" cy="398" rx="34" ry="8" />
          <path className="pixel-walker-leg pixel-walker-leg-a" d="M26 364h18v34H22z" />
          <path className="pixel-walker-leg pixel-walker-leg-b" d="M48 364h18l4 34H48z" />
          <path className="pixel-shirt pixel-shirt-project" d="M20 324h50v48H20z" />
          <path className="pixel-neck" d="M36 316h18v14H36z" />
          <path className="pixel-skin" d="M24 286h42v36H24z" />
          <path
            className="pixel-hair pixel-hair-project"
            d="M18 284h52v16H60v10H50v-16H28v12H18z"
          />
          <rect className="pixel-eye" x="58" y="306" width="6" height="6" />
          <path className="pixel-walker-arm" d="M66 330h12v34H66z" />
          <path className="pixel-briefcase" d="M72 354h34v30H72zm8-8h18v8H80z" />
        </PixelCharacter>
      </svg>
    </figure>
  );
}
