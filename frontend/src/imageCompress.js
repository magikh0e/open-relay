// Client-side image compression, applied before upload. Large phone photos are
// multiple MB; downscaling + re-encoding to WebP typically cuts them to a few
// hundred KB, saving bandwidth and disk, and strips EXIF (incl. GPS) as a bonus.
//
// Tweak these two knobs to trade quality for size:
export const IMAGE_MAX_DIM = 1920; // longest edge in px; larger images scale down
export const IMAGE_QUALITY = 0.82; // WebP encode quality, 0..1

// Only lossy-recompress these. GIFs are excluded so animation survives; SVG and
// everything non-raster is left untouched.
const COMPRESSIBLE = new Set(["image/jpeg", "image/png", "image/webp"]);

// Returns a (possibly) smaller File. Falls back to the original whenever the
// input isn't a compressible raster image, decoding fails, the browser can't
// encode WebP, or re-encoding wouldn't actually save space.
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

    const blob = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/webp", quality)
    );
    // Keep the original if encoding failed or didn't shrink the file.
    if (!blob || blob.size >= file.size) return file;

    const name = file.name.replace(/\.[^.]+$/, "") + ".webp";
    return new File([blob], name, {
      type: "image/webp",
      lastModified: file.lastModified,
    });
  } finally {
    bitmap.close?.();
  }
}
