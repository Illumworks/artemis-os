export function bindComposer(textarea, sendBtn, onSend) {
  const sync = () => {
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 180) + "px";
    sendBtn.disabled = !textarea.value.trim();
  };
  textarea.addEventListener("input", sync, true);
  textarea.addEventListener("keydown", (event) => {
    if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
      event.preventDefault();
      event.stopImmediatePropagation();
      onSend();
    }
  }, true);
  sendBtn.addEventListener("click", (event) => {
    event.preventDefault();
    event.stopImmediatePropagation();
    onSend();
  }, true);
  sync();
}

