import { load } from "cheerio";

const ORIGIN = "https://course.pku.edu.cn";

/** Blackboard's read-only teacher file preview, not assignment/submission actions. */
export function isTeacherFilePreviewUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const allowed = new Set(["cmd", "content_id", "course_id", "launch_in_new"]);
    return url.origin === ORIGIN && !url.username && !url.password
      && url.pathname === "/webapps/blackboard/execute/content/file"
      && [...url.searchParams.keys()].every((key) => allowed.has(key) && url.searchParams.getAll(key).length === 1)
      && url.searchParams.get("cmd") === "view"
      && /^_\d+_\d+$/.test(url.searchParams.get("content_id") ?? "")
      && /^_\d+_\d+$/.test(url.searchParams.get("course_id") ?? "")
      && (!url.searchParams.has("launch_in_new") || /^(?:true|false)$/.test(url.searchParams.get("launch_in_new")!));
  } catch { return false; }
}

/** Only teacher content; never send session cookies to arbitrary body links. */
export function isTeacherAttachmentUrl(value: string): boolean {
  try {
    const url = new URL(value);
    const path = decodeURIComponent(url.pathname);
    return url.origin === ORIGIN && !url.username && !url.password
      && !/\/(?:users?|students?|submissions?|attempts?|grades?)(?:\/|$)/i.test(path)
      && (/^\/bbcswebdav\/pid-[\w]+-dt-content-rid-[\w]+(?:\/|$)/i.test(path)
        || /^\/bbcswebdav\/courses\/[^/]+\//i.test(path));
  } catch { return false; }
}

/** Resolve a single embedded teacher file; never follow navigation, scripts or nested previews. */
export function embeddedTeacherAttachment(html: string, previewUrl: string): string | null {
  if (!isTeacherFilePreviewUrl(previewUrl)) return null;
  const $ = load(html);
  const urls = new Set<string>();
  $("iframe[src], embed[src], object[data]").each((_index, node) => {
    const raw = $(node).attr(node.tagName === "object" ? "data" : "src");
    try {
      const url = new URL(raw!, previewUrl).href;
      if (isTeacherAttachmentUrl(url)) urls.add(url);
    } catch { /* Malformed embeds stay unread. */ }
  });
  return urls.size === 1 ? [...urls][0] : null;
}
