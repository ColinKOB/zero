# Zero

Zero is a personal assistant you host yourself. You talk to it from the Zero app on your iPhone or Mac. Behind the app is a small team of AI helpers called Zips, and they use your own Claude or ChatGPT plan.

Get started at [heyzero.app](https://heyzero.app).

## How Do I Get the Mac App?

Download it from [heyzero.app/download](https://heyzero.app/download), or from this repository's [latest release](https://github.com/ColinKOB/zero/releases/latest). The Mac app sets up everything on your Mac, so you need nothing else from this page.

## How Do I Run Zero on a Linux Computer?

Use a computer that stays on and runs Ubuntu 24.04 or another Debian-like Linux. Run this command as your own user, not as root:

```
curl -fsSL https://raw.githubusercontent.com/ColinKOB/zero/main/install.sh | bash
```

When it finishes, it shows a code. Scan that code with the Zero app on your iPhone to pair it. Run `bash ~/.local/share/zero/install.sh pair` for a new code, and `bash ~/.local/share/zero/install.sh` again to update.

## What Is in This Repository?

- `install.sh` installs Zero on a Linux computer.
- `hub/` holds the hub, the service the app talks to.
- `alien/` holds Zero's tools for the Zips, the sandbox each Zip works in, and the files that set up a home.

## License

Zero is released under the MIT License. See [LICENSE](LICENSE).
