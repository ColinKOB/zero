# Taffy Backups

## What Is Saved

Every night at 3:30 a.m. ET, `taffy-backup.timer` on alien runs `alien/bin/taffy-backup`. It saves:

- every bot's chat history and Taffy's board (all Hermes and hub SQLite databases, copied with SQLite's backup API)
- memories, SOULs, configs, `.env` files, scheduled jobs and scripts for Taffy and every bot
- saved logins (each bot's `vault/`), the Hermes sign-ins (`auth.json`) and sign-in permissions (`consent.db`)
- `~/taffy/hub-state` (paired devices, call summaries, pictures; saved call transcripts in `calls/` are skipped because they are kept for 30 days only), `~/taffy/work`, `~/taffy/shared`, `~/taffy/state`, `~/taffy/bin`, `~/taffy/plugins`
- the user systemd units and the Hermes commit in use

Since the Zero for Anyone work (Oct 9), a home also holds:

- `~/taffy/home.json`, the home's settings (no passwords or keys);
- `~/taffy/hub-state/google/`, the Google sign-ins made in the app (refresh tokens, mode 600, inside `hub-state`, so they are already in the snapshot);
- `~/.config/himalaya/` entries the app added (between `# zero:begin` and `# zero:end` lines) with their `.pass` files, and `~/.config/zero-calendars/` with calendar passwords and private calendar addresses.

`taffy-backup` must save `~/taffy/home.json` and `~/.config/zero-calendars/` beside `~/.config/himalaya`. A restored Google sign-in may already be refused by Google (seven days in Testing); the app then shows "Google asked you to sign in again." and one tap fixes it.

It skips what can be rebuilt: Hermes itself, virtual environments, caches, logs and browser profiles (bots sign in again).

The snapshot goes into a restic repository at `alien:~/backups/taffy-restic`, which is encrypted, deduplicated and versioned. Restic keeps 7 daily, 4 weekly and 6 monthly snapshots. After each run the script checks the repository, restores that night's databases into a scratch folder, and compares every table's row count with what it saved. The result goes to `~/taffy/hub-state/backup.json`. The hub alerts Colin in the morning if a run failed or none ran for 36 hours.

Every morning at 9:00 (or at the next wake), Colin's Mac pulls the repository to `~/Backups/Taffy/restic` and checks it (`mac/taffy-backup-pull`, launchd job `com.example.taffy-backup-pull`, log `~/Library/Logs/taffy-backup-pull.log`). The hub alerts if the Mac copy is more than three days old.

## Where Is the Password?

The repository is useless without its password. It lives in two places:

- alien: `~/.config/taffy-backup/restic-password` (mode 0600)
- the Mac's login keychain, item "Taffy backup" (account `taffy-backup`)

Never copy `~/.taffy` anywhere unencrypted: each vault's key sits next to its data, so a plain copy is a list of working passwords.

## How Do I Restore?

From the Mac copy (alien gone):

```sh
export RESTIC_REPOSITORY=~/Backups/Taffy/restic
export RESTIC_PASSWORD_COMMAND="security find-generic-password -s 'Taffy backup' -a taffy-backup -w"
restic snapshots
restic restore latest --target /tmp/taffy-restore
```

The restored tree mirrors `/home/jdoe`. Databases are under `taffy/backup-stage/` at their original relative paths (for example `taffy/backup-stage/.taffy/state.db`); copy them back over the live paths with the services stopped. Everything else restores in place.

On a rebuilt alien: install Hermes at the commit in `taffy/backup-stage/hermes-commit.txt`, recreate the venvs, copy the restored files back to `/home/jdoe`, put the databases from `backup-stage` in place, then `systemctl --user daemon-reload` and start `taffy-gateway`, `taffy-hub` and `taffy-backup.timer`. Bots sign in to websites again; their saved logins come back with the vaults.

## What About Other Homes?

Each home on alien (Riley's, the test home) runs the same `taffy-backup` as its own user, into its own encrypted repository (`backup.repo` in its `home.json`) with its own password, which the home wizard prints once for Colin to keep. After each run the script copies the encrypted repository to `/srv/zero/backups/<home>/`, and the Mac pull keeps one `~/Backups/Zero/<home>/` per home. The hub checks each home's `backup.json`; the Mac copy check runs only where `backup.mac_copy_expected` is true in `home.json` (Colin's home).

## What Is Needed Before Taffy Works Again?

A restore brings back Taffy's data, not everything around it. On a rebuilt alien, also do these, or the bots fail on their first turn:

1. Reinstall the tools in `~/.local/bin`: `claude` (Claude Code), `codex` (0.160.1 or newer), `himalaya`, and `restic` (0.19.1, checked against SHA256SUMS).
2. Sign in fresh rather than restoring old tokens, because both rotate: run `claude` and log in to Colin's Claude plan (the bots' brain, connectors and summaries), then `codex login` with Colin's ChatGPT account (the call voice). Hermes's own ChatGPT login for the backup model comes back in `.taffy/auth.json`; if it fails, run `~/taffy/bin/taffy auth add openai-codex`.
3. Mail and the app's notification key come back from the backup (`~/.config/himalaya`, `~/.config/taffy-push`). Check mail with `himalaya envelope list -a personal --page-size 1`; notifications work again once the app is opened and sends its token.
4. Put the browser sandbox profile back, or the bots' browsers won't start: `sudo cp alien/apparmor/taffy-chromium /etc/apparmor.d/ && sudo apparmor_parser -r /etc/apparmor.d/taffy-chromium`.
5. Publish the hub on the tailnet: `tailscale serve --bg --https=6443 http://127.0.0.1:8650`.
6. Turn on linger so the services start at boot: `sudo loginctl enable-linger jdoe`.
7. Pair the apps again only if `hub.db` was not restored; otherwise the existing pairings keep working.

A restore from the Mac copy was tested on 2026-10-07: Taffy's chats, paired devices, SOULs, memories, vaults, jobs and work files all came back.
