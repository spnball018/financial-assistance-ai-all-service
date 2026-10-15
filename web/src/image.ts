/**
 * Shrink a phone photo before upload. Tested on TrueMoney history photos (2448x3264, ~2 MB): at 1600 px
 * on the long side (~210 KB) the reading was identical to the full image; at 1200 px it misread a time.
 * Images already small enough, or that the browser cannot decode (some HEIC), are sent unchanged. Some
 * mobile browsers never finish decoding or encoding a large photo (the promise just hangs), so after
 * `timeoutMs` the original is sent instead.
 */
export async function shrinkImage(file: File, maxSide = 1600, quality = 0.85, timeoutMs = 15_000): Promise<File> {
  let timer: number | undefined;
  const timeout = new Promise<File>((resolve) => {
    timer = window.setTimeout(() => resolve(file), timeoutMs);
  });
  try {
    return await Promise.race([shrink(file, maxSide, quality), timeout]);
  } catch {
    return file;
  } finally {
    window.clearTimeout(timer);
  }
}

type Source = { image: CanvasImageSource; width: number; height: number; close: () => void };

/** createImageBitmap where it works; otherwise an <img> (which also applies the photo's EXIF rotation). */
async function decode(file: File): Promise<Source> {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    return { image: bitmap, width: bitmap.width, height: bitmap.height, close: () => bitmap.close() };
  } catch {
    const url = URL.createObjectURL(file);
    const img = new Image();
    img.src = url;
    try {
      await img.decode();
    } catch (e) {
      URL.revokeObjectURL(url);
      throw e;
    }
    return { image: img, width: img.naturalWidth, height: img.naturalHeight, close: () => URL.revokeObjectURL(url) };
  }
}

async function shrink(file: File, maxSide: number, quality: number): Promise<File> {
  const src = await decode(file);
  const scale = maxSide / Math.max(src.width, src.height);
  if (!(scale < 1)) {
    src.close();
    return file;
  }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(src.width * scale);
  canvas.height = Math.round(src.height * scale);
  canvas.getContext("2d")!.drawImage(src.image, 0, 0, canvas.width, canvas.height);
  src.close();
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
  if (!blob || blob.size >= file.size) return file;
  return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
}
