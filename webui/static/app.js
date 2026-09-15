document.addEventListener("DOMContentLoaded", () => {
  // Flash messages: auto-dismiss after 4s, or on the close button.
  document.querySelectorAll(".flash").forEach((flash) => {
    const dismiss = () => flash.remove();
    const closeBtn = flash.querySelector(".flash__close");
    if (closeBtn) closeBtn.addEventListener("click", dismiss);
    setTimeout(dismiss, 4000);
  });

  // Any [data-confirm-dialog="<dialog id>"] trigger opens that <dialog>
  // instead of following its href/submitting directly. showModal()
  // already traps focus and closes on Esc, so no extra wiring needed.
  document.querySelectorAll("[data-confirm-dialog]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const dialog = document.getElementById(trigger.getAttribute("data-confirm-dialog"));
      if (dialog) dialog.showModal();
    });
  });

  document.querySelectorAll("[data-dialog-cancel]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const dialog = btn.closest("dialog");
      if (dialog) dialog.close();
    });
  });

  // Long-running, synchronous actions (run discovery / run matching):
  // disable the button and relabel it on submit so it can't be double-fired.
  document.querySelectorAll("[data-loading-label]").forEach((form) => {
    form.addEventListener("submit", () => {
      const button = form.querySelector("button[type=submit]");
      if (!button) return;
      button.disabled = true;
      button.textContent = form.getAttribute("data-loading-label");
    });
  });
});
