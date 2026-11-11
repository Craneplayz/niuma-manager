// Account images stay in the authenticated session; never load external avatar URLs.
export function avatarContent(user, escapeHtml) {
  const avatar = user?.avatar;
  if (typeof avatar === "string" && /^data:image\/png;base64,[A-Za-z0-9+/]+=*$/.test(avatar))
    return `<img src="${avatar}" alt="" draggable="false">`;
  return escapeHtml(Array.from(user?.name || "我")[0]);
}

export async function readAvatarFile(file) {
  if (!["image/png", "image/jpeg", "image/webp"].includes(file.type))
    throw new Error("请选择 PNG、JPG 或 WebP 图片");
  if (!file.size || file.size > 5 * 1024 * 1024)
    throw new Error("请选择不超过 5 MB 的图片");
  const url = URL.createObjectURL(file);
  try {
    const image = new Image();
    image.src = url;
    await image.decode();
    if (!image.naturalWidth || !image.naturalHeight ||
        image.naturalWidth > 8192 || image.naturalHeight > 8192 ||
        image.naturalWidth * image.naturalHeight > 20_000_000)
      throw new Error("请选择尺寸较小的图片，最长边不超过 8192 像素，总像素不超过 2000 万");
    return image;
  } catch (error) {
    if (error.message.startsWith("请选择")) throw error;
    throw new Error("请选择有效的 PNG、JPG 或 WebP 图片");
  } finally {
    URL.revokeObjectURL(url);
  }
}

export function drawAvatar(canvas, image, { zoom = 1, x = 0, y = 0 } = {}) {
  const side = Math.min(image.naturalWidth, image.naturalHeight) / zoom;
  const left = (image.naturalWidth - side) * (x + 100) / 200;
  const top = (image.naturalHeight - side) * (y + 100) / 200;
  const context = canvas.getContext("2d");
  context.clearRect(0, 0, 256, 256);
  context.imageSmoothingQuality = "high";
  context.drawImage(image, left, top, side, side, 0, 0, 256, 256);
}
