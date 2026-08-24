# Future Features

## ~~Clipboard access~~ — implemented 2026-08-24

`get_clipboard` / `set_clipboard` tools on the remote machine, via `pyperclip`. See
`docs/MCP_SERVER.md`'s "Clipboard access" section and `docs/CHANGELOG.md`.

---

## `list_windows` + `focus_window(title)`

Return the list of open top-level window titles, and bring a named window to the
foreground without needing a taskbar click at guessed pixel coordinates.

Solves the recurring problem of a terminal stealing keyboard focus from a browser
window — instead of trying to click the taskbar icon at the right pixel, an LLM can
call `focus_window("Google Chrome")` and know it worked.

Implementation note: `pygetwindow` (Windows + macOS) or `win32gui` (Windows-only)
for enumerating and activating windows.

---

## ~~Region / crop screenshot~~ — implemented 2026-08-24

`take_screenshot`'s `fx1`/`fy1`/`fx2`/`fy2` (fractional, not the pixel `x`/`y`/
`width`/`height` shape originally sketched here — kept consistent with `fx`/`fy`'s
existing reasoning). See `docs/MCP_SERVER.md`'s "Cropping a screenshot" section and
`docs/CHANGELOG.md`.
