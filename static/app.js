document.querySelectorAll(".copy-response").forEach((button) => {
  button.addEventListener("click", async () => {
    const feedback = button.parentElement.querySelector(".copy-feedback");
    const textarea = document.getElementById(button.dataset.copyTarget);
    try {
      await navigator.clipboard.writeText(textarea.value);
      feedback.textContent = "Copied!";
    } catch {
      feedback.textContent = "Unable to copy. Select the text and copy it manually.";
    }
  });
});
