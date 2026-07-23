// Client-side image compression, applied before upload. Large phone photos are
// multiple MB; downscaling + re-encoding to WebP typically cuts them to a few
// hundred KB, saving bandwidth and disk.
//
// Every jpeg/png/webp is re-encoded even when that doesn't save bytes, because
// canvas output carries no EXIF — that's what guarantees location (GPS) data
// never leaves the device. The server does no stripping of its own, and uploads
// are served publicly by URL, so this is the only thing standing between a
// photo's GPS tag and anyone holding the link.
//
// Tweak these two knobs to trade quality for size:
export const IMAGE_MAX_DIM = 1920; // longest edge in px; larger images scale down
export const IMAGE_QUALITY = 0.82; // WebP encode quality, 0..1

// Only re-encode these. GIFs are excluded so animation survives; SVG and
// everything non-raster is left untouched. (Neither carries EXIF/GPS.)
const COMPRESSIBLE = new Set(["image/jpeg", "image/png", "image/webp"]);

// When the lossy WebP copy isn't smaller, we still re-encode — in the source's
// own format — so EXIF/GPS is dropped either way. Staying in-format avoids
// ballooning a photo (JPEG→PNG) or softening line art (PNG→lossy).
const FALLBACK_ENCODE = {
  "image/jpeg": ["image/jpeg", 0.92],
  "image/png": ["image/png", undefined], // lossless
  "image/webp": ["image/webp", 0.92],
};

const EXT = { "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp" };

function reFile(original, blob, type) {
  const name = original.name.replace(/\.[^.]+$/, "") + "." + EXT[type];
  return new File([blob], name, {
    type,
    lastModified: original.lastModified,
  });
}

// Returns a re-encoded (usually smaller) File. The original is passed through
// untouched only when it isn't a compressible raster image, when decoding
// fails, or when the browser can't encode at all.
export async function maybeCompressImage(
  file,
  { maxDim = IMAGE_MAX_DIM, quality = IMAGE_QUALITY } = {}
) {
  if (!file || !COMPRESSIBLE.has(file.type)) return file;

  let bitmap;
  try {
    // imageOrientation:"from-image" bakes in EXIF rotation so the re-encoded
    // (metadata-stripped) copy isn't left sideways.
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
  } catch {
    return file;
  }

  try {
    const { width, height } = bitmap;
    const scale = Math.min(1, maxDim / Math.max(width, height));
    const w = Math.max(1, Math.round(width * scale));
    const h = Math.max(1, Math.round(height * scale));

    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return file;
    ctx.drawImage(bitmap, 0, 0, w, h);

    const encode = (type, q) =>
      new Promise((resolve) => canvas.toBlob(resolve, type, q));

    const webp = await encode("image/webp", quality);
    if (webp && webp.size < file.size) return reFile(file, webp, "image/webp");

    // The lossy copy wasn't smaller — but we still re-encode rather than fall
    // back to the original, so location metadata never reaches the server.
    const [type, q] = FALLBACK_ENCODE[file.type];
    const same = await encode(type, q);
    if (same) return reFile(file, same, type);
    return file; // browser can't encode at all; nothing more we can do
  } finally {
    bitmap.close?.();
  }
}
