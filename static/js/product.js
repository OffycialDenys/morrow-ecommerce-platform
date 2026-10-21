(() => {
  const form = document.querySelector("[data-variant-form]");
  if (!form) return;

  const select = form.querySelector("[data-variant-select]");
  const price = document.querySelector("#selected-price");
  const button = form.querySelector("[data-add-button]");
  const status = form.querySelector("[data-availability-message]");

  const refreshSelection = () => {
    const option = select.selectedOptions[0];
    const available = option?.dataset.available === "true";
    if (price && option) price.textContent = option.dataset.price || "";
    if (status) status.textContent = available ? "Available to add to your bag." : "Availability is not confirmed for this option.";
    button.disabled = !available;
  };

  select.addEventListener("change", refreshSelection);
  refreshSelection();
})();
