// Runs in the isolated world of a temporary SoundCloud track tab. Each call takes
// one step so that cancellation and a lost native connection can stop the next click.
function soundcloudPlaylistStep(request, phase) {
  const { track, account, destination, title, sharing } = request;
  if (location.origin + location.pathname !== track) return { error: "Track page changed" };
  const owner = document.querySelector("a.header__userNavUsernameButton");
  if (!owner) return phase === "open" ? null : { error: "SoundCloud account changed" };
  if (owner.href !== account) return { error: "The browser is signed in to a different SoundCloud account" };
  const visible = (e) => e && e.getClientRects().length && !e.disabled;
  const modal = document.querySelector(".addToPlaylistTabs");
  if (phase === "open") {
    const open = document.querySelector(".listenEngagement__actions button.sc-button-addtoset");
    if (!visible(open)) return null;
    open.click();
    return { next: destination ? "add" : "create-tab" };
  }
  if (!modal) return null;
  if (phase === "create-tab") {
    if (visible(modal.querySelector(".createPlaylist__title input"))) return { next: "form" };
    const tabs = modal.querySelectorAll(".tabs__tabs .tabs__tab");
    // SoundCloud's two-tab form declares add first and create second.
    if (tabs.length !== 2 || !visible(tabs[1])) return null;
    tabs[1].click();
    return { next: "form" };
  }
  if (phase === "form" || phase === "save") {
    const form = modal.querySelector(".createPlaylist");
    const name = form?.querySelector(".createPlaylist__title input");
    const choice = form?.querySelector('.createPlaylist__sharing input[type="radio"][value="' + sharing + '"]');
    if (!visible(name) || !choice || choice.disabled) return null;
    if (phase === "form") {
      name.value = title;
      name.dispatchEvent(new Event("input", { bubbles: true }));
      name.dispatchEvent(new Event("change", { bubbles: true }));
      if (!choice.checked) choice.click();
      return { next: "save" };
    }
    const save = form.querySelector("button.createPlaylist__saveButton");
    if (name.value !== title || !choice.checked || !visible(save)) return null;
    save.click();
    return { next: "created" };
  }
  if (phase === "created") {
    const link = modal.querySelector("a.createPlaylist__goToPlaylist");
    return visible(link) ? { destination: link.href } : null;
  }
  const rows = [...modal.querySelectorAll(".addToPlaylistItem")]
    .filter(row => row.querySelector("a.addToPlaylistItem__titleLink")?.href === destination);
  if (rows.length !== 1) return null;
  const button = rows[0].querySelector("button.addToPlaylistButton");
  if (!button) return null;
  // A selected button removes the track. Fail closed on unfamiliar markup:
  // only the documented unchecked state with an empty title may be clicked.
  if (button.classList.contains("sc-button-selected")) return { destination };
  if (phase !== "add" || !visible(button) || button.getAttribute("title") !== "") return null;
  button.click();
  return { next: "added" };
}
