document.querySelectorAll("form").forEach((form) => {
  form.addEventListener("submit", (event) => {
    const button = event.submitter;
    const loadingLabel = button?.dataset.loadingLabel;
    if (!loadingLabel) return;
    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }
    if (button.hasAttribute("formaction")) form.action = button.formAction;
    form.dataset.submitting = "true";
    button.disabled = true;
    button.setAttribute("aria-busy", "true");
    button.textContent = loadingLabel;
  });
});

document.querySelectorAll(".copy-response").forEach((button) => {
  button.addEventListener("click", async () => {
    const feedback = button.parentElement.querySelector(".copy-feedback");
    const textarea = document.getElementById(button.dataset.copyTarget);
    const originalLabel = button.dataset.defaultLabel || button.textContent;
    button.dataset.defaultLabel = originalLabel;
    try {
      await navigator.clipboard.writeText(textarea.value);
      button.textContent = "Copied ✓";
      feedback.textContent = "";
      window.setTimeout(() => {
        button.textContent = originalLabel;
      }, 2000);
    } catch {
      button.textContent = originalLabel;
      feedback.textContent = "Unable to copy. Select the text and copy it manually.";
    }
  });
});
