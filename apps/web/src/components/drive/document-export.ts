import type { DriveNodeDetail } from "../../types";

export type DriveDocumentFormat = "markdown" | "docx" | "pptx" | "pdf";
export type DriveExportFormat = "markdown" | "docx" | "pptx" | "pdf";

export function driveDocumentFormat(
  node: Pick<DriveNodeDetail, "path" | "doc_type"> | null | undefined,
): DriveDocumentFormat {
  const extension = node?.path.split(".").pop()?.toLowerCase();
  if (node?.doc_type === "docx" || extension === "docx") return "docx";
  if (node?.doc_type === "pptx" || extension === "pptx") return "pptx";
  if (node?.doc_type === "pdf" || extension === "pdf") return "pdf";
  return "markdown";
}

export function exportFormatsFor(format: DriveDocumentFormat): DriveExportFormat[] {
  if (format === "markdown") return ["markdown", "docx", "pdf"];
  if (format === "docx") return ["docx", "pdf"];
  if (format === "pptx") return ["pptx"];
  return ["pdf"];
}

function baseName(name: string): string {
  return name.replace(/\.(?:md|markdown|docx|pptx|pdf)$/i, "") || "document";
}

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.style.display = "none";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function plainMarkdown(value: string): string {
  return value
    .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/\[([^\]]+)\]\([^)]*\)/g, "$1")
    .replace(/[*_~`>#]/g, "")
    .trim();
}

async function markdownDocx(markdown: string): Promise<Blob> {
  const { Document, HeadingLevel, Packer, Paragraph, TextRun } = await import("docx");
  const children = markdown.split("\n").map((line) => {
    const heading = line.match(/^(#{1,3})\s+(.+)$/);
    if (heading) {
      const levels = [HeadingLevel.HEADING_1, HeadingLevel.HEADING_2, HeadingLevel.HEADING_3];
      return new Paragraph({
        text: plainMarkdown(heading[2]),
        heading: levels[heading[1].length - 1],
      });
    }
    const bullet = line.match(/^\s*[-*+]\s+(.+)$/);
    if (bullet) return new Paragraph({ text: plainMarkdown(bullet[1]), bullet: { level: 0 } });
    return new Paragraph({
      children: [new TextRun(plainMarkdown(line))],
      spacing: { after: line ? 120 : 60 },
    });
  });
  return Packer.toBlob(new Document({ sections: [{ children }] }));
}

async function renderedPdf(element: HTMLElement): Promise<Blob> {
  const [{ default: html2canvas }, { jsPDF }] = await Promise.all([
    import("html2canvas"),
    import("jspdf"),
  ]);
  const surface = element.cloneNode(true) as HTMLElement;
  surface.removeAttribute("class");
  Object.assign(surface.style, {
    position: "fixed",
    left: "-10000px",
    top: "0",
    width: "794px",
    boxSizing: "border-box",
    padding: "56px 64px",
    backgroundColor: "#ffffff",
    color: "#0f172a",
    fontFamily: "Arial, sans-serif",
    fontSize: "16px",
    lineHeight: "1.7",
  });
  for (const node of surface.querySelectorAll<HTMLElement>("*")) {
    node.removeAttribute("class");
    node.style.color = "#0f172a";
    node.style.borderColor = "#e2e8f0";
    const tag = node.tagName;
    if (tag === "H1")
      Object.assign(node.style, { fontSize: "32px", fontWeight: "700", margin: "8px 0 24px" });
    if (tag === "H2")
      Object.assign(node.style, {
        fontSize: "24px",
        fontWeight: "700",
        margin: "32px 0 12px",
        paddingBottom: "8px",
        borderBottom: "1px solid #e2e8f0",
      });
    if (tag === "H3")
      Object.assign(node.style, { fontSize: "19px", fontWeight: "700", margin: "24px 0 8px" });
    if (tag === "P") node.style.margin = "0 0 16px";
    if (tag === "UL" || tag === "OL")
      Object.assign(node.style, { margin: "0 0 16px", paddingLeft: "28px" });
    if (tag === "BLOCKQUOTE")
      Object.assign(node.style, {
        margin: "20px 0",
        padding: "10px 16px",
        borderLeft: "4px solid #93c5fd",
        backgroundColor: "#eff6ff",
        color: "#475569",
      });
    if (tag === "PRE")
      Object.assign(node.style, {
        margin: "20px 0",
        padding: "16px",
        borderRadius: "10px",
        whiteSpace: "pre-wrap",
        backgroundColor: "#020617",
        color: "#f1f5f9",
      });
    if (tag === "PRE")
      for (const child of node.querySelectorAll<HTMLElement>("*")) child.style.color = "#f1f5f9";
    if (tag === "TABLE")
      Object.assign(node.style, { width: "100%", borderCollapse: "collapse", margin: "20px 0" });
    if (tag === "TH" || tag === "TD")
      Object.assign(node.style, { border: "1px solid #e2e8f0", padding: "8px 12px" });
    if (tag === "A") node.style.color = "#2563eb";
  }
  window.document.body.appendChild(surface);
  try {
    const canvas = await html2canvas(surface, {
      backgroundColor: "#ffffff",
      scale: Math.min(window.devicePixelRatio || 1, 2),
      useCORS: true,
      onclone: (clonedDocument) => {
        for (const styleSheet of Array.from(clonedDocument.styleSheets)) {
          styleSheet.disabled = true;
        }
      },
    });
    const pdf = new jsPDF({ unit: "mm", format: "a4", orientation: "portrait" });
    const pageWidth = pdf.internal.pageSize.getWidth();
    const pageHeight = pdf.internal.pageSize.getHeight();
    const imageHeight = (canvas.height * pageWidth) / canvas.width;
    const image = canvas.toDataURL("image/png");
    let y = 0;
    pdf.addImage(image, "PNG", 0, y, pageWidth, imageHeight);
    let remaining = imageHeight - pageHeight;
    while (remaining > 0) {
      y -= pageHeight;
      pdf.addPage();
      pdf.addImage(image, "PNG", 0, y, pageWidth, imageHeight);
      remaining -= pageHeight;
    }
    return pdf.output("blob");
  } finally {
    surface.remove();
  }
}

interface ExportDocumentInput {
  name: string;
  sourceFormat: DriveDocumentFormat;
  targetFormat: DriveExportFormat;
  markdown?: string | null;
  binary?: Blob | null;
  previewElement?: HTMLElement | null;
}

export async function exportDriveDocument(input: ExportDocumentInput): Promise<void> {
  const filename = `${baseName(input.name)}.${input.targetFormat === "markdown" ? "md" : input.targetFormat}`;
  if (input.sourceFormat === input.targetFormat) {
    const native =
      input.sourceFormat === "markdown"
        ? new Blob([input.markdown ?? ""], { type: "text/markdown;charset=utf-8" })
        : input.binary;
    if (!native) throw new Error("Document content is not available");
    downloadBlob(native, filename);
    return;
  }
  if (input.sourceFormat === "markdown" && input.targetFormat === "docx") {
    downloadBlob(await markdownDocx(input.markdown ?? ""), filename);
    return;
  }
  if (input.targetFormat === "pdf" && input.previewElement) {
    downloadBlob(await renderedPdf(input.previewElement), filename);
    return;
  }
  throw new Error("This export conversion is not supported");
}
