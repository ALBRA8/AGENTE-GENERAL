/**
 * Document parser — extrae texto de PDF, Word, Excel, TXT, CSV, MD, JSON.
 *
 * Sin dependencias externas pesadas. Usa solo APIs nativas de Node.js
 * para texto plano y patrones simples para formatos binarios.
 *
 * Para PDF y Word usamos extractores ligeros (pdfjs-dist y mammoth)
 * que se cargan dinámicamente solo cuando se necesitan.
 */

import { readFile } from "fs/promises";
import path from "path";

// Límite de texto extraído: 50KB (suficiente para que el LLM lo procese)
const MAX_EXTRACTED_TEXT = 50 * 1024;

export type ParsedDocument = {
  text: string;
  filename: string;
  mimeType: string;
  fileType: string;
  truncated: boolean;
};

/**
 * Detecta el tipo de archivo por magic bytes (no por extensión).
 */
function detectFileType(buffer: Buffer): string {
  if (buffer.length < 4) return "unknown";

  // PDF: %PDF
  if (buffer[0] === 0x25 && buffer[1] === 0x50 && buffer[2] === 0x44 && buffer[3] === 0x46) {
    return "pdf";
  }

  // ZIP-based (Word .docx, Excel .xlsx, OpenDocument): PK\x03\x04
  if (buffer[0] === 0x50 && buffer[1] === 0x4b && buffer[2] === 0x03 && buffer[3] === 0x04) {
    return "zip";
  }

  // Old Word .doc: D0 CF 11 E0
  if (buffer[0] === 0xd0 && buffer[1] === 0xcf && buffer[2] === 0x11 && buffer[3] === 0xe0) {
    return "doc";
  }

  // RTF: {\rtf
  if (buffer.length >= 5 && buffer.toString("ascii", 0, 5) === "{\\rtf") {
    return "rtf";
  }

  // Plain text: check if it's valid UTF-8
  try {
    buffer.toString("utf8");
    // Heuristic: if first 100 bytes are mostly printable ASCII, it's text
    const sample = buffer.subarray(0, Math.min(100, buffer.length));
    let printable = 0;
    for (const byte of sample) {
      if ((byte >= 0x20 && byte <= 0x7e) || byte === 0x0a || byte === 0x0d || byte === 0x09) {
        printable++;
      }
    }
    if (printable / sample.length > 0.8) {
      // Check for specific text formats
      const text = buffer.toString("utf8", 0, Math.min(1000, buffer.length)).trim();
      if (text.startsWith("{") || text.startsWith("[")) return "json";
      if (text.startsWith("#")) return "markdown";
      if (text.includes(",") && text.split("\n")[0]?.includes(",")) return "csv";
      return "text";
    }
  } catch {
    // Not valid UTF-8
  }

  return "unknown";
}

/**
 * Extract text from a PDF buffer using pdfjs-dist.
 * Dynamic import to avoid loading the library when not needed.
 */
async function extractPdfText(buffer: Buffer): Promise<string> {
  try {
    // pdfjs-dist is a dependency we'll add to package.json
    const pdfjs = await import("pdfjs-dist/legacy/build/pdf.mjs");
    const doc = await pdfjs.getDocument({ data: new Uint8Array(buffer) }).promise;
    const pages: string[] = [];
    const maxPages = Math.min(doc.numPages, 50); // Cap at 50 pages

    for (let i = 1; i <= maxPages; i++) {
      const page = await doc.getPage(i);
      const content = await page.getTextContent();
      const text = content.items
        .map((item) => (item as { str?: string }).str || "")
        .join(" ");
      pages.push(text);
    }

    return pages.join("\n\n--- Página ---\n\n");
  } catch (err) {
    // Fallback: try to extract readable ASCII strings from the PDF
    const text = buffer.toString("latin1");
    // PDF text streams are between BT and ET markers
    const matches = text.match(/BT\s+(.*?)\s+ET/g);
    if (matches) {
      return matches
        .map((m) => m.replace(/BT\s+|\s+ET/g, "").replace(/\((.*?)\)/g, "$1"))
        .join(" ")
        .slice(0, MAX_EXTRACTED_TEXT);
    }
    throw new Error("No se pudo extraer texto del PDF");
  }
}

/**
 * Extract text from a .docx file using mammoth (dynamic import).
 */
async function extractDocxText(buffer: Buffer): Promise<string> {
  try {
    const mammoth = await import("mammoth");
    const result = await mammoth.extractRawText({ buffer });
    return result.value;
  } catch {
    // Fallback: docx is a ZIP — try to read word/document.xml
    try {
      const { execSync } = await import("child_process");
      // Use unzip command if available
      const tmpDir = `/tmp/docx-${Date.now()}`;
      const tmpFile = `${tmpDir}.docx`;
      await import("fs/promises").then((fs) => fs.writeFile(tmpFile, buffer));
      execSync(`unzip -o "${tmpFile}" -d "${tmpDir}"`, { stdio: "ignore" });
      const xml = await readFile(path.join(tmpDir, "word", "document.xml"), "utf8");
      // Extract text between <w:t> tags
      const texts = xml.match(/<w:t[^>]*>([^<]*)<\/w:t>/g);
      if (texts) {
        return texts.map((t) => t.replace(/<[^>]+>/g, "")).join(" ");
      }
      throw new Error("No text found in document.xml");
    } catch {
      throw new Error("No se pudo extraer texto del documento Word");
    }
  }
}

/**
 * Extract text from .xlsx using a simple ZIP parse.
 */
async function extractXlsxText(buffer: Buffer): Promise<string> {
  try {
    const xlsx = await import("xlsx");
    const workbook = xlsx.read(buffer, { type: "buffer" });
    const sheets: string[] = [];
    for (const name of workbook.SheetNames) {
      const sheet = workbook.Sheets[name];
      const csv = xlsx.utils.sheet_to_csv(sheet);
      sheets.push(`=== Hoja: ${name} ===\n${csv}`);
    }
    return sheets.join("\n\n");
  } catch {
    throw new Error("No se pudo extraer texto del archivo Excel");
  }
}

/**
 * Extract text from old .doc format (binary).
 * Uses antiword if available, otherwise extracts readable strings.
 */
async function extractDocText(buffer: Buffer): Promise<string> {
  // Old .doc format — try to extract readable strings
  const text = buffer.toString("latin1");
  // Extract sequences of printable characters
  const matches = text.match(/[\x20-\x7E]{4,}/g);
  if (matches) {
    return matches.join(" ").slice(0, MAX_EXTRACTED_TEXT);
  }
  throw new Error("No se pudo extraer texto del archivo .doc");
}

/**
 * Main entry point: parse any supported document.
 *
 * @param buffer - The file content as a Buffer
 * @param filename - Original filename (for error messages)
 * @param mimeType - MIME type from upload
 * @returns ParsedDocument with extracted text
 */
export async function parseDocument(
  buffer: Buffer,
  filename: string,
  mimeType: string
): Promise<ParsedDocument> {
  const fileType = detectFileType(buffer);

  let text = "";
  let truncated = false;

  try {
    switch (fileType) {
      case "pdf":
        text = await extractPdfText(buffer);
        break;

      case "zip":
        // Could be .docx or .xlsx — check filename/mime
        if (filename.toLowerCase().endsWith(".xlsx") || mimeType.includes("spreadsheet") || mimeType.includes("excel")) {
          text = await extractXlsxText(buffer);
        } else {
          // Default to .docx (most common ZIP-based document)
          text = await extractDocxText(buffer);
        }
        break;

      case "doc":
        text = await extractDocText(buffer);
        break;

      case "rtf":
        // Simple RTF text extraction
        text = buffer.toString("utf8")
          .replace(/\\par[d]?/g, "\n")
          .replace(/\\[a-z]+-?\d+ ?/g, "")
          .replace(/[{}]/g, "")
          .replace(/\\'([0-9a-f]{2})/gi, (_, hex) => String.fromCharCode(parseInt(hex, 16)))
          .trim();
        break;

      case "json":
        // Pretty-print JSON
        text = JSON.stringify(JSON.parse(buffer.toString("utf8")), null, 2);
        break;

      case "markdown":
      case "csv":
      case "text":
        text = buffer.toString("utf8");
        break;

      default:
        // Last resort: try as text
        text = buffer.toString("utf8");
        if (!text.trim()) {
          throw new Error(`Formato no soportado: ${fileType}`);
        }
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    throw new Error(`Error procesando ${filename}: ${message}`);
  }

  // Truncate if too long
  if (text.length > MAX_EXTRACTED_TEXT) {
    text = text.slice(0, MAX_EXTRACTED_TEXT);
    truncated = true;
  }

  return {
    text,
    filename,
    mimeType,
    fileType,
    truncated,
  };
}

/**
 * List of supported MIME types and extensions for upload validation.
 */
export const SUPPORTED_DOCUMENT_TYPES = [
  "application/pdf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document", // .docx
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", // .xlsx
  "application/msword", // .doc
  "application/vnd.ms-excel", // .xls
  "text/plain", // .txt
  "text/csv", // .csv
  "text/markdown", // .md
  "application/json", // .json
  "application/rtf", // .rtf
];

export const SUPPORTED_EXTENSIONS = [
  ".pdf", ".docx", ".doc", ".xlsx", ".xls",
  ".txt", ".csv", ".md", ".json", ".rtf",
];
