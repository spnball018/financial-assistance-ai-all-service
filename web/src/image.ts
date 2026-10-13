/**
 * Shrink a phone photo before upload. Tested on TrueMoney history photos (2448x3264, ~2 MB): at 1600 px
 * on the long side (~210 KB) the reading was identical to the full image; at 1200 px it misread a time.
 * Images already small enough, or that the browser cannot decode (some HEIC), are sent unchanged.
 */
export async function shrinkImage(file: File, maxSide = 1600, quality = 0.85): Promise<File> {
  try {
    const bitmap = await createImageBitmap(file, { imageOrientation: "from-image" });
    const scale = maxSide / Math.max(bitmap.width, bitmap.height);
    if (scale >= 1) {
      bitmap.close();
      return file;
    }
    const canvas = document.createElement("canvas");
    canvas.width = Math.round(bitmap.width * scale);
    canvas.height = Math.round(bitmap.height * scale);
    canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
    bitmap.close();
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
    if (!blob || blob.size >= file.size) return file;
    return new File([blob], file.name.replace(/\.\w+$/, "") + ".jpg", { type: "image/jpeg" });
  } catch {
    return file;
  }
}
