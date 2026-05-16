// Skills surface compatibility shim.
// The legacy right-panel marketplace has been replaced by the main-canvas Skills view.

import { getState, setState } from "../core/store.js";
import { registerCommand } from "../ui/commands.js";

export function openSkillsSurface() {
  if (getState("sessionId")) {
    setState("sessionId", null);
  }
  setState("view", "skills");
}

registerCommand("skills", {
  category: "app",
  description: "Open the Skills surface",
  execute() {
    openSkillsSurface();
  },
});
