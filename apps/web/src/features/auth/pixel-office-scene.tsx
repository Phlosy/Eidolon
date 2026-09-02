import { Pause, Play } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";

export function PixelOfficeScene() {
  const { t } = useTranslation("auth");
  const [manuallyPaused, setManuallyPaused] = useState(false);

  return (
    <figure
      className={`pixel-office-frame${manuallyPaused ? " is-paused" : ""}`}
      data-testid="pixel-office-scene"
      aria-label={t("shell.office.alt")}
    >
      <div className="pixel-office-hud">
        <span className="pixel-office-status-dot" aria-hidden="true" />
        <span>{t("shell.office.status")}</span>
        <span className="ml-auto font-mono" aria-hidden="true">
          09:41
        </span>
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
      <svg className="pixel-office" viewBox="0 0 760 420" aria-hidden="true">
        <rect className="pixel-office-wall" width="760" height="420" />
        <rect className="pixel-office-window" x="28" y="34" width="704" height="132" />
        <path
          className="pixel-office-skyline"
          d="M28 136h62v-34h34v22h48V78h42v58h58V94h48v42h66V66h52v70h58v-24h46v24h58V86h48v50h84v30H28z"
        />
        <path
          className="pixel-office-window-grid"
          d="M28 98h704M202 34v132M378 34v132M554 34v132"
        />

        <g className="pixel-office-light">
          <rect x="104" y="12" width="112" height="10" />
          <rect x="472" y="12" width="112" height="10" />
        </g>

        <rect className="pixel-office-floor" y="166" width="760" height="254" />
        <path
          className="pixel-office-floor-grid"
          d="M0 326h760M0 374h760M120 166L66 420M262 166l-18 254M498 166l18 254M640 166l54 254"
        />

        <g className="pixel-office-door">
          <rect className="pixel-office-door-frame" x="666" y="184" width="66" height="142" />
          <rect className="pixel-office-door-panel" x="674" y="194" width="50" height="124" />
          <rect className="pixel-office-door-glass" x="684" y="206" width="30" height="54" />
          <rect className="pixel-office-door-handle" x="708" y="274" width="6" height="16" />
          <rect className="pixel-office-door-sensor" x="646" y="204" width="10" height="18" />
        </g>

        <g className="pixel-desk pixel-desk-a">
          <rect className="pixel-desk-top" x="70" y="278" width="250" height="18" />
          <rect className="pixel-desk-leg" x="84" y="296" width="14" height="82" />
          <rect className="pixel-desk-leg" x="292" y="296" width="14" height="82" />
          <rect className="pixel-monitor" x="116" y="210" width="82" height="58" />
          <rect className="pixel-monitor-screen" x="124" y="218" width="66" height="42" />
          <path className="pixel-monitor-code" d="M132 230h20v6h-20zm0 14h42v6h-42z" />
          <rect className="pixel-monitor" x="218" y="220" width="68" height="48" />
          <rect className="pixel-monitor-screen" x="226" y="228" width="52" height="32" />
          <path
            className="pixel-monitor-code pixel-monitor-code-delay"
            d="M234 238h28v6h-28zm0 12h18v5h-18z"
          />
          <rect className="pixel-monitor-stand" x="151" y="268" width="12" height="10" />
          <rect className="pixel-monitor-stand" x="246" y="268" width="12" height="10" />
          <rect className="pixel-keyboard" x="170" y="274" width="72" height="8" />
        </g>

        <g className="pixel-worker pixel-worker-a">
          <rect className="pixel-chair" x="124" y="296" width="68" height="54" />
          <rect className="pixel-chair" x="138" y="350" width="10" height="28" />
          <rect className="pixel-shirt-a" x="138" y="246" width="52" height="66" />
          <rect className="pixel-skin" x="146" y="208" width="42" height="42" />
          <path className="pixel-hair" d="M142 208h42v8h8v16h-10v-8h-40z" />
          <rect className="pixel-face" x="178" y="226" width="6" height="6" />
          <rect className="pixel-skin pixel-typing-arm-a" x="184" y="270" width="42" height="10" />
          <rect className="pixel-skin pixel-typing-arm-b" x="174" y="284" width="44" height="10" />
        </g>

        <g className="pixel-desk pixel-desk-b">
          <rect className="pixel-desk-top" x="392" y="278" width="228" height="18" />
          <rect className="pixel-desk-leg" x="406" y="296" width="14" height="82" />
          <rect className="pixel-desk-leg" x="592" y="296" width="14" height="82" />
          <rect className="pixel-monitor" x="454" y="208" width="92" height="60" />
          <rect className="pixel-monitor-screen" x="462" y="216" width="76" height="44" />
          <path
            className="pixel-monitor-code pixel-monitor-code-delay"
            d="M470 228h42v6h-42zm0 14h24v6h-24z"
          />
          <rect className="pixel-monitor-stand" x="494" y="268" width="12" height="10" />
          <rect className="pixel-keyboard" x="494" y="274" width="68" height="8" />
          <rect className="pixel-coffee" x="576" y="256" width="20" height="22" />
          <path className="pixel-coffee-steam" d="M582 250v-16m8 16v-12" />
        </g>

        <g className="pixel-worker pixel-worker-b">
          <rect className="pixel-chair" x="536" y="296" width="66" height="54" />
          <rect className="pixel-chair" x="580" y="350" width="10" height="28" />
          <rect className="pixel-shirt-b" x="536" y="246" width="52" height="66" />
          <rect className="pixel-skin" x="536" y="208" width="42" height="42" />
          <path className="pixel-hair-b" d="M532 208h50v10h-8v8h-42z" />
          <rect className="pixel-face" x="538" y="226" width="6" height="6" />
          <rect className="pixel-skin pixel-typing-arm-a" x="512" y="270" width="42" height="10" />
          <rect className="pixel-skin pixel-typing-arm-b" x="520" y="284" width="44" height="10" />
        </g>

        <g className="pixel-plant">
          <rect x="34" y="278" width="34" height="50" />
          <path d="M50 278v-48m0 24l-18-16m18 2l18-20m-18 8L38 208" />
        </g>

        <g className="pixel-walker">
          <rect className="pixel-walker-shadow" x="4" y="374" width="50" height="8" />
          <rect className="pixel-shirt-walker" x="14" y="310" width="32" height="44" />
          <rect className="pixel-skin" x="16" y="282" width="28" height="30" />
          <path className="pixel-hair" d="M12 282h34v10H20v8h-8z" />
          <rect className="pixel-skin pixel-walker-arm" x="42" y="318" width="10" height="34" />
          <rect className="pixel-walker-leg-a" x="16" y="354" width="10" height="28" />
          <rect className="pixel-walker-leg-b" x="34" y="354" width="10" height="28" />
          <rect className="pixel-briefcase" x="48" y="344" width="24" height="24" />
        </g>
      </svg>
    </figure>
  );
}
