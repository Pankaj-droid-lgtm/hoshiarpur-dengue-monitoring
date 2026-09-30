async function prepareImage(file) {
  if (!file || !file.type.startsWith("image/")) return file;
  if (!("createImageBitmap" in window)) return file;
  const bitmap = await createImageBitmap(file);
  const max = 1600;
  const scale = Math.min(1, max / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d").drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  const blob = await new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.82));
  if (!blob) throw new Error("Image compression failed.");
  return new File([blob], `${file.name.replace(/\.[^.]+$/, "") || "photo"}.jpg`, {type: "image/jpeg"});
}

document.querySelectorAll("form[data-upload-form]").forEach((form) => {
  form.addEventListener("submit", async (event) => {
    const input = form.querySelector('input[type="file"]');
    if (!input || !input.files.length) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    const status = form.querySelector("[data-upload-status]");
    try {
      if (button) button.disabled = true;
      if (status) status.textContent = "Preparing photo…";
      const data = new FormData(form);
      const image = await prepareImage(input.files[0]);
      data.set(input.name, image);
      if (status) status.textContent = "Uploading…";
      const response = await fetch(form.action || window.location.href, {
        method: form.method || "POST", body: data, credentials: "same-origin",
        headers: {"X-CSRF-Token": data.get("csrf_token") || "", "X-Requested-With": "XMLHttpRequest"},
      });
      if (!response.ok) throw new Error(`Upload failed (${response.status}).`);
      window.location.assign(response.url);
    } catch (error) {
      if (status) status.textContent = `${error.message} Please try a smaller JPEG or PNG image.`;
      if (button) button.disabled = false;
    }
  });
});
