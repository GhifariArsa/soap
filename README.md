<div align="center">

# 🧼 soap

**A terminal reference manager for papers, books, and PDFs — add a source, fetch its metadata, review what's uncertain, and browse it all from a keyboard-driven TUI.**

Point soap at a local file, DOI, arXiv ID, ISBN, directory, or URL. It resolves
the metadata (and, for links, best-effort downloads the PDF), queues anything it
is unsure about for a quick review, and keeps everything in a plain, readable,
version-controllable library on disk. From there you can browse, search, tag, and
open it — all without leaving the terminal.

[![ci](https://github.com/GhifariArsa/soap/actions/workflows/ci.yml/badge.svg)](https://github.com/GhifariArsa/soap/actions/workflows/ci.yml)
[![release](https://img.shields.io/github/v/release/GhifariArsa/soap?label=release&color=4b8bbe)](https://github.com/GhifariArsa/soap/releases)
![python](https://img.shields.io/badge/python-3.14+-4b8bbe)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![pypi](https://img.shields.io/pypi/v/soap-tui?label=pypi&color=3775a9)](https://pypi.org/project/soap-tui/)
[![homebrew](https://img.shields.io/badge/homebrew-GhifariArsa%2Fsoap-fbb040)](https://github.com/GhifariArsa/homebrew-soap)

![soap demo](docs/demo.gif)

</div>

---

## Overview

Keeping a reference library usually means running a heavyweight desktop
application or maintaining a folder of PDFs with names like
`paper (3) final_v2.pdf`. soap is neither. It is a fast, keyboard-first TUI
backed by a library in which **every record is a plain `info.yaml` file on
disk** — so you can read it, diff it, and check it into git.

soap:

- **accepts any source** — a local file, a whole directory, a DOI, a bare arXiv
  ID, an ISBN, or a URL;
- **fetches metadata automatically** from Crossref, arXiv, or Open Library, and
  best-effort **downloads the PDF** for arXiv and direct-PDF links (and
  open-access DOIs);
- **queues uncertain records** so you can accept, correct, or skip them in a
  quick review pass rather than trust a bad guess;
- and lets you **browse, search, tag, and open** the whole library from a TUI —
  or drive the same library from the CLI.

The on-disk record is the source of truth; the SQLite index is only a fast,
rebuildable view. soap never parses the contents of your PDFs.

## Installation

**Homebrew** (recommended — no Python required):

```sh
brew install GhifariArsa/soap/soap-tui
```

This installs a self-contained binary (embedded CPython 3.14 via PyApp), so **no
Python or pip is needed**. Coverage is **Apple-Silicon macOS and Linux
(arm64 / x86_64)**; there is no Intel-macOS binary, so `brew install` on an Intel
mac fails fast with a clear message. Upgrade with `brew upgrade soap-tui`. The
tap and its Intel-mac note live at
[GhifariArsa/homebrew-soap](https://github.com/GhifariArsa/homebrew-soap).

**Standalone binary** (macOS arm64, Linux arm64 / x86_64) — the installer
verifies the download before placing `soap` in `~/.local/bin`:

```sh
curl -fsSL https://raw.githubusercontent.com/GhifariArsa/soap/main/install.sh | sh
```

Set `SOAP_VERSION=v0.1.0` to pin a release or `SOAP_INSTALL_DIR` to change the
install directory. The installer requires a published release.

**From PyPI** (requires [uv](https://docs.astral.sh/uv/) and Python **3.14+**):

```sh
uv tool install soap-tui
```

**From a checkout** — the simplest way to try it:

```sh
git clone https://github.com/GhifariArsa/soap.git
cd soap
uv run soap init
```

<sub>The distribution is named **`soap-tui`** (plain `soap` was taken on PyPI),
but the installed command is always **`soap`**.</sub>

Then set up your library once:

```sh
soap init
```

`init` creates the library, its SQLite index, and a shell export for `SOAP_DIR`.
The default library is `~/.soap`; `SOAP_DIR` changes the default, and `--path
<dir>` overrides both. It writes `config.yaml`, `inbox/`, `documents/`, and
`soap.db`, plus a quoted `SOAP_DIR` export to your shell config (or prints a safe
export line when no shell can be detected). A fresh library sets
`always_review: true`, and re-running `init` never overwrites an existing config.

| Option | Description |
| --- | --- |
| `--path <dir>` | Initialize a different library. |
| `--shell auto\|zsh\|bash\|fish` | Choose the shell config to update. |
| `--force` | Reinitialize an existing library; destructive, but backs up the old database. |

## Usage

The core workflow is straightforward: **add a source, review it, then run
`soap` to browse.**

From a checkout, prefix commands with `uv run`; an installed copy uses `soap`
directly.

```sh
# An arXiv ID resolves metadata and best-effort downloads its PDF.
soap add 1706.03762

# A fresh `soap init` routes adds through the review queue.
soap inbox review

# Then browse the library.
soap
```

For a local PDF, supply an identifier or the metadata yourself:

```sh
soap add ~/papers/paper.pdf --doi 10.1145/3292500.3330701

# Or work completely offline:
soap add ~/papers/paper.pdf --no-fetch \
  --title "Attention Is All You Need" \
  --author "Vaswani, Ashish" --year 2017
```

![adding files to soap](docs/add.gif)

`SOURCE` can be a local file, directory, URL, DOI, or bare arXiv ID; ISBN
metadata comes from `--isbn`, and identifiers can be passed explicitly with
`--doi` or `--arxiv`. The most commonly used options:

| Option | Description |
| --- | --- |
| `--title`, `--author`, `--year`, `--type` | Override metadata. `--author` is repeatable. |
| `--tag`, `--collection` | Add repeatable tags or collections. |
| `--no-fetch` | Skip network metadata lookups. |
| `--recursive` | Include files below a directory source. |
| `--confirm` | Correct the core fields inline before saving. |
| `--edit`, `-e` | Edit the generated `info.yaml` in `$EDITOR`. |
| `--dry-run` | Preview the add without writing anything. |
| `--force` | Add even when a duplicate is detected. |
| `--path <dir>` | Use a library other than `$SOAP_DIR` or `~/.soap`. |

Run `soap add --help` and `soap inbox review --help` to list every option.

### Reviewing the inbox

`soap inbox review` presents one `needs_review` record at a time:

- `a` — accept it as-is
- `c` — correct title, authors, year, type, or venue; Enter keeps a value
- `e` — open the complete `info.yaml` in `$EDITOR`
- `s` — skip it for later
- `d` — delete it and its attached files, after confirmation
- `q` — quit the walk

The TUI review screen shares the same review core: `enter`/`a` files, `c`
corrects, `e` opens `$EDITOR`, `s` skips, and `q`/`esc` finishes. `soap add
--confirm` provides the same guided field correction during an add.

### Keybindings

Run `soap` with no subcommand to open the TUI. Press `?` at any time for the
in-app reference; the compact map for the main screen is below.

```
 j / k · g / G        move · jump to top / bottom
 Ctrl-D / Ctrl-U      half-page down / up
 Tab / Shift-Tab      cycle panes      h / l   focus left / right
 enter / o            open the selected file or URL
 /                    search title, author, tag, or DOI (Enter/Tab → list)
 E                    edit the core fields (title/authors/year/type/venue) in an in-app form
 e                    edit the complete `info.yaml` in $EDITOR (full power option)
 d                    delete the selected document and its files (asks to confirm)
 t                    edit tags (Enter/comma adds · Tab completes · Ctrl-S saves · Esc cancels)
 m                    cycle read status: unread → reading → read
 r                    review the inbox
 Ctrl-R               refresh from disk
 ? / Ctrl-P           keyboard reference / command palette
 Ctrl-T               cycle themes
 q                    quit
```

## How the workflow fits together

1. **Initialize once.** `soap init` creates the library, its SQLite index, and a
   shell export for `SOAP_DIR`.
2. **Add sources.** `soap add` takes a file, directory, DOI, arXiv ID, ISBN, or
   URL. Repeat `--author`, `--tag`, or `--collection` as needed; use
   `--recursive` for a directory.
3. **Review.** `soap inbox review`, or the TUI's `r` action, lets you accept,
   correct, edit, skip, or delete each `needs_review` record. `--confirm` folds
   the same guided correction into `add`.
4. **Browse.** Run `soap` with no subcommand. The sidebar filters all documents,
   the review inbox, read status, tags, and collections; `/` searches.
5. **Open and mark.** `enter`/`o` opens the first attached file (or the recorded
   URL) with the OS default handler. `m` cycles unread → reading → read.

Metadata lookups use Crossref, arXiv, or Open Library as appropriate. arXiv and
direct-PDF URLs download a PDF on a best-effort basis, and an open-access DOI may
too; a paywall or failed download still saves the metadata. soap does **not**
parse PDF contents.

## Configuration and data

The library path resolves in the following order:

1. `--path <dir>` where the option is available (`init`, `add`, `inbox review`)
2. `$SOAP_DIR`
3. `~/.soap`

Its important files are laid out like this:

```text
$SOAP_DIR/
├── config.yaml
├── soap.db                         # rebuildable SQLite index
├── inbox/                          # library directory created by init
└── documents/
    └── <citekey>/
        ├── info.yaml               # authoritative document record
        └── paper.pdf               # attached file(s), if any
```

`info.yaml` is the **source of truth**. Every change writes the document file
first, then synchronizes the SQLite index — the index is only a fast,
denormalized view of the files and metadata. The TUI and CLI therefore read and
mutate the same library, and the on-disk record stays readable and
version-controllable without the index.

The review **inbox is a `needs_review` status**, not a second copy of the
document: records and their attachments remain under `documents/<citekey>/` until
they are filed, skipped, or deleted. A new citekey names both the document folder
and its `info.yaml`; correcting a record during review keeps that citekey, and
only a new add derives a fresh key.

## Themes

Tags are edited from the selected document with `t` and double as sidebar
filters. The TUI ships with the `aqua-slate` (default), `one-dark`, and
`catppuccin-mocha` themes — `Ctrl-T` cycles them and the choice is saved in
`config.yaml`. User themes live in `$SOAP_DIR/themes/`.

See [the theme format](docs/themes.md) and the
[example theme](docs/example-theme.yaml) to build your own.

## Contributing

soap is a standard Python project managed with
[uv](https://docs.astral.sh/uv/). Run the tests with:

```sh
uv run pytest
```

Issues and pull requests are welcome. The distribution is named `soap-tui`; the
installed command is `soap`.

The demo GIFs above are regenerated from seeded, throwaway data with
`uv run python scripts/demo.py` (needs [VHS](https://github.com/charmbracelet/vhs)) —
see [docs/releasing.md](docs/releasing.md#regenerating-the-demo-gifs).

## License

[MIT](LICENSE).
