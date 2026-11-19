// A <Shot> opens larger in the one dialog in Base.astro; without this script it stays a
// plain link to the large image. The large image is fetched only when it is opened.
const dialog = document.querySelector("dialog.lightbox");
const image = dialog?.querySelector("img");
let opener = null;

if (dialog && image) {
  for (const link of document.querySelectorAll("a[data-zoom]"))
    link.addEventListener("click", (event) => {
      // A new tab or window, as the visitor asked.
      if (event.button || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      opener = link;
      const [width, height] = [Number(link.dataset.width), Number(link.dataset.height)];
      image.width = width;
      image.height = height;
      image.style.setProperty("--ratio", String(width / height));
      image.style.setProperty("--w", String(width));
      image.alt = link.querySelector("img")?.alt ?? "";
      image.src = link.href;
      dialog.showModal();
    });
  dialog.querySelector("[data-close]")?.addEventListener("click", () => dialog.close());
  // A click on the backdrop (the dialog itself) or on the image closes it.
  dialog.addEventListener("click", (event) => {
    if (event.target === dialog || event.target === image) dialog.close();
  });
  dialog.addEventListener("close", () => {
    image.removeAttribute("src");
    opener?.focus();
    opener = null;
  });
}
