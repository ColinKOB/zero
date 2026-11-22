export function attachClipboard(rfb, target, callbacks) {
  const maxBytes = 128 * 1024;
  let pending = null;

  const allowed = text => typeof text === "string" && new TextEncoder().encode(text).length <= maxBytes;
  const status = message => callbacks.status(message);
  const releaseKeys = () => rfb._keyboard._allKeysUp();
  const chord = (code, keysym) => {
    // Command is already down on the Mac. Release it before sending Linux's Control shortcuts.
    releaseKeys();
    rfb.sendKey(0xffe3, "ControlLeft", true);
    rfb.sendKey(keysym, code, true);
    rfb.sendKey(keysym, code, false);
    rfb.sendKey(0xffe3, "ControlLeft", false);
  };

  function copy() {
    if (rfb.viewOnly) return;
    releaseKeys();
    callbacks.copy();
  }

  function paste(text) {
    if (rfb.viewOnly) return false;
    if (!allowed(text)) { status("Copy less text and try again."); return false; }
    if (!text) return false;
    if (pending) { status("Wait for this paste to finish."); return false; }
    releaseKeys();
    const operation = { pasteRequested: false };
    pending = operation;
    const ready = () => {
      if (pending !== operation || rfb.viewOnly) return;
      operation.pasteRequested = true;
      chord("KeyV", 0x76);
    };
    const done = supported => {
      if (pending !== operation) return;
      clearTimeout(operation.timer);
      pending = null;
      if (rfb.viewOnly) return;
      if (supported) {
        if (!operation.pasteRequested) chord("KeyV", 0x76);
      } else callbacks.type(text);
      status("Pasted.");
    };
    operation.timer = setTimeout(() => {
      if (pending !== operation) return;
      pending = null;
      status("Paste did not finish. Try again.");
    }, 5000);
    if (!rfb.clipboardPasteFrom(text, done, ready)) {
      clearTimeout(operation.timer);
      pending = null;
      status("The computer is not connected yet.");
      return false;
    }
    return true;
  }

  rfb.addEventListener("disconnect", () => {
    if (pending) clearTimeout(pending.timer);
    pending = null;
  });
  target.addEventListener("keydown", event => {
    if (rfb.viewOnly || !(event.metaKey || event.ctrlKey) || event.altKey || event.shiftKey) return;
    const key = event.key.toLowerCase();
    if (!["c", "v"].includes(key)) return;
    event.preventDefault();
    event.stopImmediatePropagation();
    if (key === "v") callbacks.paste();
    else copy();
  }, { capture: true });
  return { copy, paste };
}
