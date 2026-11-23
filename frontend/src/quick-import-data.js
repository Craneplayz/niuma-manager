import { api } from "./team-store.js";
import { newId } from "./id.js";
import { zonedParts } from "./candidate-data.js";

export function shanghaiNow(value = new Date()) {
  const p = zonedParts(value);
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}`;
}
export function buildTextImport(
  text,
  published = shanghaiNow(),
  id = newId(),
  collectedAt = new Date().toISOString(),
) {
  text = String(text || "").trim();
  if (!text) throw new Error("请先粘贴通知内容");
  if (text.length > 4000) throw new Error("每次最多识别 4000 字，请分段导入");
  let publication = { value: null, precision: "unknown", basis: "unknown" };
  if (published) {
    const dateOnly = /^\d{4}-\d{2}-\d{2}$/.test(published);
    if (
      !dateOnly &&
      !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2})?$/.test(published)
    )
      throw new Error("请填写有效的通知时间");
    const value = dateOnly
      ? published
      : published + (published.length === 16 ? ":00" : "") + "+08:00";
    const stamp = new Date(dateOnly ? value + "T00:00:00+08:00" : value);
    if (
      !Number.isFinite(stamp.getTime()) ||
      (dateOnly
        ? shanghaiNow(stamp).slice(0, 10) !== published
        : shanghaiNow(stamp) !==
          published + (published.length === 16 ? ":00" : ""))
    )
      throw new Error("请填写有效的通知时间");
    publication = {
      value,
      precision: dateOnly ? "date" : "datetime",
      basis: "upstream_unverified",
    };
  }
  return {
    contract_version: "0.2",
    request_id: id,
    records: [
      {
        input_id: "manual:" + id,
        text,
        text_quality: "complete",
        platform: "manual",
        published_at: publication,
        collected_at: collectedAt,
        timezone: "Asia/Shanghai",
        timezone_basis: "caller_default",
        source_url: null,
        source_label: "文本导入",
        source_item_id: null,
        course_or_project: null,
        context: [],
        upstream_hints: null,
      },
    ],
  };
}

const root = "/api/materials/";
const known = new Set(["image/png", "image/jpeg", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);
const unknownDate = () => ({ value: null, precision: "unknown", basis: "unknown" });
export function buildMaterialRequest(receipts, id = "material:" + newId(), now = new Date().toISOString()) {
  return { contract_version: "0.3.1", request_id: id, records: receipts.map((r, index) => {
    const supported = known.has(r.mime_type);
    return { input_id: id + ":" + index, text: "", text_quality: "missing", platform: "wechat",
      published_at: unknownDate(), collected_at: now, timezone: "Asia/Shanghai", timezone_basis: "caller_default",
      source_url: null, source_label: "用户主动上传的文件", source_item_id: null, course_or_project: null,
      context: [], upstream_hints: null, attachments: [{ asset_id: "file:" + index,
        ...Object.fromEntries(["file_name", "mime_type", "size_bytes", "sha256"].map(k => [k, r[k]])),
        file_ref: supported ? r.file_ref : null, origin: "unknown", original_published_at: unknownDate(),
        status: supported ? "available" : "unsupported", content_mode: "file", errors: supported ? [] : [
          { stage: "upload", code: "unsupported_type", message: "当前读取器不支持此格式", retryable: false }] }] };
  }) };
}

export const materialAccept = ".png,.jpg,.jpeg,.pdf,.docx";
export function newMaterialDraft(prefix = "material:") {
  return { id: prefix + newId(), receipts: [], request: null };
}
export async function submitMaterialFiles(files, draft, { limits, signal, valid, text = "" }) {
  if (!limits?.asset_bytes) throw new Error("文件上传设置尚未就绪，请稍后重试");
  if (!files.length || files.length > 20 || files.some(f => !f.size || f.size > limits.asset_bytes))
    throw new Error("最多选择 20 个非空文件，每份最多 " + Math.floor(limits.asset_bytes / 1048576) + " MB");
  if (text && files.length > 10) throw new Error("同一段文字最多搭配 10 个附件，请分批导入");
  for (let i = draft.receipts.length; i < files.length; i++) {
    if (!valid()) throw new Error("本次上传已结束");
    const form = new FormData();
    form.append("file", files[i]);
    const receipt = await api(root + "assets", form, "POST", { formData: true, signal, timeoutMs: 45000 });
    if (receipt.contract_version !== "manual-materials/0.1") throw new Error("文件服务版本不匹配");
    draft.receipts.push(receipt);
  }
  if (!valid()) throw new Error("本次上传已结束");
  if (!draft.request) {
    const body = buildMaterialRequest(draft.receipts, draft.id);
    if (text) {
      const first = body.records[0];
      body.records = [{ ...first, text, text_quality: "complete", attachments: body.records.flatMap(r => r.attachments) }];
    }
    draft.request = body;
  }
  return api(root + "imports", draft.request, "POST", { signal });
}
