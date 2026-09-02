declare module "mammoth/mammoth.browser" {
  interface ConversionResult {
    value: string;
    messages: Array<{ type: string; message: string }>;
  }

  export function convertToHtml(input: { arrayBuffer: ArrayBuffer }): Promise<ConversionResult>;
}
