import {
  createElement,
  type ComponentPropsWithoutRef,
  type ElementType,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { cn } from "../../utils/cn";

/**
 * 全站文字组件。
 *
 * 规则：页面里不要再写 text-xs / text-[11px] 这类一次性字号；
 * 按“这段话是什么角色”选 variant —— 和 Word 的标题 1/标题 2/正文/表格 一样。
 *
 * 样式定义集中在 design/typography.css 的 .type-* 里，
 * 改字阶、字体、行高只动那一处，全站一起变。
 */

export type TextVariant =
  | "display"
  | "h1"
  | "h2"
  | "h3"
  | "h4"
  | "body"
  | "bodySm"
  | "caption"
  | "overline"
  | "table"
  | "tutorial"
  | "code"
  | "telemetry";

export type TextTone =
  "default" | "muted" | "primary" | "success" | "warning" | "danger" | "inherit";

const VARIANT_CLASS: Record<TextVariant, string> = {
  display: "type-display",
  h1: "type-h1",
  h2: "type-h2",
  h3: "type-h3",
  h4: "type-h4",
  body: "type-body",
  bodySm: "type-body-sm",
  caption: "type-caption",
  overline: "type-overline",
  table: "type-table",
  tutorial: "type-tutorial",
  code: "type-code",
  telemetry: "type-telemetry",
};

const TONE_CLASS: Record<TextTone, string> = {
  default: "",
  muted: "text-muted-foreground",
  primary: "text-primary",
  success: "text-success",
  warning: "text-warning",
  danger: "text-danger",
  inherit: "text-inherit",
};

const DEFAULT_ELEMENT: Record<TextVariant, ElementType> = {
  display: "h1",
  h1: "h1",
  h2: "h2",
  h3: "h3",
  h4: "h4",
  body: "p",
  bodySm: "p",
  caption: "p",
  overline: "p",
  table: "span",
  tutorial: "p",
  code: "code",
  telemetry: "span",
};

export interface TextProps<T extends ElementType = "p"> {
  as?: T;
  variant?: TextVariant;
  tone?: TextTone;
  className?: string;
  children?: ReactNode;
}

export function Text<T extends ElementType = "p">({
  as,
  variant = "body",
  tone = "default",
  className,
  children,
  ...rest
}: TextProps<T> & Omit<ComponentPropsWithoutRef<T>, "as" | "children" | "className">) {
  const Component = (as ?? DEFAULT_ELEMENT[variant]) as ElementType;
  return createElement(
    Component,
    { className: cn(VARIANT_CLASS[variant], TONE_CLASS[tone], className), ...rest },
    children,
  );
}

/** 快捷组件共享的 props：保留原生元素属性 + 语义 tone。 */
type TypographyProps<T extends ElementType> = ComponentPropsWithoutRef<T> & { tone?: TextTone };

/** 语义化快捷组件：读代码时一眼知道这段是几级标题/什么角色。 */
export const Display = (props: TypographyProps<"h1">) => (
  <Text as="h1" variant="display" {...props} />
);
export const H1 = (props: TypographyProps<"h1">) => <Text as="h1" variant="h1" {...props} />;
export const H2 = (props: TypographyProps<"h2">) => <Text as="h2" variant="h2" {...props} />;
export const H3 = (props: TypographyProps<"h3">) => <Text as="h3" variant="h3" {...props} />;
export const H4 = (props: TypographyProps<"h4">) => <Text as="h4" variant="h4" {...props} />;
export const Body = (props: TypographyProps<"p">) => <Text as="p" variant="body" {...props} />;
export const BodySm = (props: TypographyProps<"p">) => <Text as="p" variant="bodySm" {...props} />;
export const Caption = (props: TypographyProps<"p">) => (
  <Text as="p" variant="caption" {...props} />
);
export const Overline = (props: TypographyProps<"p">) => (
  <Text as="p" variant="overline" {...props} />
);
export const TableText = (props: TypographyProps<"span">) => (
  <Text as="span" variant="table" {...props} />
);
export const TutorialText = (props: TypographyProps<"p">) => (
  <Text as="p" variant="tutorial" {...props} />
);
export const CodeText = (props: TypographyProps<"code">) => (
  <Text as="code" variant="code" {...props} />
);
export const Telemetry = (props: TypographyProps<"span">) => (
  <Text as="span" variant="telemetry" {...props} />
);

/**
 * 语言容器：给一段内容打上当前语言标签。
 *
 * 中英混排时浏览器会按 lang 选字形/断行规则；全局 <html lang> 只表示界面语言，
 * 数据里混着的另一种语言（例如中文界面里的英文专有名词）用这个包一层更准确。
 */
export function Lang({
  as: Component = "span",
  className,
  children,
  ...rest
}: {
  as?: ElementType;
  className?: string;
  children?: ReactNode;
} & Record<string, unknown>) {
  const { i18n } = useTranslation();
  return createElement(
    Component,
    {
      lang: i18n.resolvedLanguage ?? i18n.language,
      className: cn("font-sans", className),
      ...rest,
    },
    children,
  );
}
