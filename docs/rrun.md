# rrun

Zero-escaping remote and cross-shell execution. It is vendored into this repo as
a git submodule at `rrun/`, the same way `claude-statusline-grid` is: pinned to a
commit rather than copied, so its history stays its own.

It is not a plugin. Plugins install through the marketplace and ship skills,
agents and hooks; rrun is a program with its own Windows installer, and it earns
a place here because the rule it enforces is one an agent session breaks daily.

## The problem it solves

Every interpreter layer eats one level of quoting. A command that crosses
PowerShell, Git Bash, WSL and a remote shell crosses four parsers, and
hand-counting which one consumes each `$` and each quote fails routinely. The
failure is quiet: a quote is eaten, the payload still runs, and it runs
something slightly different from what was written.

rrun removes the counting. The payload never appears inside a quoted string. It
travels as base64, which has no metacharacters in any shell, and is decoded only
by the shell that executes it.

**The rule worth adopting: more than one level of quoting means switch
patterns.** Write the payload to a file and run the file, or use rrun.

## Install

The submodule is not cloned by a plain `git clone`. Fetch it first:

```sh
git submodule update --init rrun
```

On Windows, run the installer from the submodule:

```powershell
& .\rrun\install.ps1
```

It is idempotent and self-updating, records an ownership manifest at
`~/.rrun/install-state.json` before it modifies anything, and finishes by running
its own test suite. `& .\rrun\uninstall.ps1` reverses it from that manifest.

On a pure Linux machine there is no installer to run: copy `rrun/bin/rrun` to
`~/.local/bin/` and make it executable.

Restart terminals and Claude Code sessions afterwards, because PATH, `BASH_ENV`
and `PYTHONIOENCODING` only take effect in new ones.

### What Windows needs first

The installer writes rrun's core **inside WSL** and leaves a shim on the Windows
side that forwards to it. So WSL is a hard requirement, and it has to be a real
distro that is also the default one:

```powershell
wsl --install -d Ubuntu
wsl --set-default Ubuntu
```

Three things are worth knowing before you hit them:

- **`docker-desktop` does not count.** It is a distro in `wsl -l`, but it has no
  `/mnt/c`, so the installer cannot read its own source file and step 2 fails
  with `WSL core install failed`. If it is your default, the install fails no
  matter how many real distros are also installed.
- **An old WSL cannot boot a new distro.** WSL 2.4.13 installed Ubuntu and then
  failed to start it with `Wsl/Service/E_UNEXPECTED`, as root and as any user.
  `wsl --update` fixed it. Check `wsl --version` before blaming the distro.
- **A tar-installed distro has no user account.** It runs as root with
  `HOME=/root`, which works, but rrun's core lands in `/root/.local/bin`. Create
  a normal user later and you have to re-run the installer.

## Use

```
rrun [-s ps|bash|sh|wsl] [-J jumps] [-n] <host[,hop2,...]|local|adb[:serial]> <script | - | -c "cmds">
```

| Command | What it replaces |
|---|---|
| `rrun -s bash HOST -c '...'` | `ssh HOST "..."` against a Linux or macOS host |
| `rrun HOST -c 'Get-Process'` | the same against Windows. `-s ps` is the default |
| `rrun -s bash local -c '...'` | a nested `powershell -Command "..."` |
| `rrun -s wsl HOST -c '...'` | bash inside a Windows host's WSL |
| `rrun -s bash a,b -c '...'` | `ssh a "ssh b ..."`, re-armored per hop |
| `rrun adb -c '...'` | `adb shell "..."`. Defaults to `-s sh` |
| `rrun -n ...` | nothing. Prints the composed command instead of running it |

The default `-s` is `ps`, which is the one that surprises people: a Linux target
needs `-s bash` or a `.sh` script.

`adb` is legal only as the last hop. Alone, the adb client runs on your machine;
as the tail of a chain such as `gw,adb:SERIAL`, it runs on the host before it, so
the device can hang off a different machine.

One case rrun cannot take: it forces `BatchMode=yes`, so a host that still needs
password authentication has to be bootstrapped with
`ssh HOST 'bash -s' < script.sh` first.

## The advisory hook

rrun ships a `PreToolUse` hook that warns when a command hand-crosses a boundary
rrun already covers. It never blocks, it fails open on any error, and `# no-rrun`
in a command silences it.

Read the advisory as a prompt to re-check, not as noise. You pick a tool by the
shape of your goal, so "am I hand-quoting?" is a question that never gets asked
on its own.

**It can collide with this kit.** Each `PreToolUse` entry costs a process launch
on every Bash and PowerShell tool call, which on an antivirus-heavy Windows box
was measured at roughly 0.38 s. If you have consolidated your hooks behind one
dispatcher, note that rrun's installer adds its own entry back on every run:
the advisory then fires twice and the per-call cost doubles. Pass
`-SkipClaudeHook` to keep your own wiring.
