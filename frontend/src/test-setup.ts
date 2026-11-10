import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
afterEach(cleanup);

// jsdom has no native modal behavior; browser tests cover focus trapping / Escape.
HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
};
