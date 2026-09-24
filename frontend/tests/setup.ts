import "@testing-library/jest-dom/vitest";

// jsdom ships <dialog> without showModal/close. The controller opens and closes
// real dialogs, so without these every dialog flow throws instead of running.
if (!HTMLDialogElement.prototype.showModal) {
  HTMLDialogElement.prototype.showModal = function showModal(
    this: HTMLDialogElement,
  ) {
    this.setAttribute("open", "");
  };
}
if (!HTMLDialogElement.prototype.close) {
  HTMLDialogElement.prototype.close = function close(
    this: HTMLDialogElement,
    returnValue?: string,
  ) {
    if (returnValue !== undefined) this.returnValue = returnValue;
    this.removeAttribute("open");
  };
}
