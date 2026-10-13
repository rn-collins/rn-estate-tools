<!-- made-with-rule -->
## Sign every commit with which AI made it (RN, Oct 13, 2026)

Every AI that works in this repo, Claude, ChatGPT or any other, ends every commit message with one line:

`Made-with: <AI> · <product> · account: <account nickname> · chat: "<chat name>" · <chat link>`

- **AI:** `Claude (Anthropic)` or `ChatGPT (OpenAI)`, so it is always clear which one made the change.
- **Product:** for example `Claude Code (cloud, claude.ai/code)`, `Claude Code (desktop app)`, `Cowork`, `Claude in Chrome`, `ChatGPT agent`, `Codex`.
- **Account:** RN's nickname for that account. Never an email address. Ask RN if you don't know it.
- **Chat:** the chat's title and link. If the title doesn't say what the chat does, ask RN to rename it.
- Keep RN's GitHub name as the commit author.
- Example: `Made-with: Claude (Anthropic) · Claude Code (cloud, claude.ai/code) · account: FNM account · chat: "Ask a Neuroscientist studio" · https://claude.ai/code/session_0163VSitu2DhFt9TLCw68gxE`

A check in `.github/workflows/made-with.yml` marks any commit without the line with a red X. The shared rules for every chat are on the message board: `aan-studio/handoff/BOARD.md`, branch `aan-studio-backup-2026-10-06` of rn-collins/rn-content-second-brain.
