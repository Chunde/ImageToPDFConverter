# Contributing

Thanks for taking a look. This is a small, dependency-light project — you can
read the whole thing in one sitting, and there is no build step to fight.

## Getting set up

```bash
git clone https://github.com/Chunde/ImageToPDFConverter.git
cd ImageToPDFConverter
python -m pip install -r requirements.txt
python run.py
```

The app is `app/server.py` (stdlib HTTP) serving `web/` (plain
HTML/CSS/JS). Edit a file in `web/` and refresh the browser — no rebuild.

## Before you open a pull request

Run the two suites that need nothing but the runtime dependencies:

```bash
python tests/smoke_test.py
python tests/api_test.py            # needs the app running
```

If you touched `web/`, also run the browser suite, since it is the one that
catches real interaction bugs:

```bash
node tests/ui_test.js
```

All three print `PASS`/`FAIL` per check and exit non-zero on failure. Please keep
that shape if you add checks — they are plain scripts on purpose, so they run
with no test framework installed.

If your change affects the UI, regenerate the README screenshots with
`node tests/screenshots.js` and commit the updated files in `docs/`.

## What is easy to contribute

* **New output formats or page sizes** — `PAGE_SIZES` in `app/pdfbuild.py`.
* **More decoders** (HEIC is already wired up when `pillow-heif` is present).
* **UI polish** — keyboard-accessible reordering would be a genuinely useful
  addition; today dragging and the `◀`/`▶` buttons are the only ways to reorder.
* **Translations** — all interface strings currently live in `web/index.html`
  and the `toast(...)` calls in `web/app.js`.

## Style

* Python: standard library first. The runtime dependencies are `Pillow` and
  `pypdf` and the intent is to keep it that way.
* Match the surrounding code: type hints on public functions, docstrings that
  explain *why* rather than restating the code, comments only where a reader
  would otherwise be puzzled.
* JavaScript: no framework, no bundler, no CDN. Keep it that way.
* Do not add a dependency to fix something the standard library or twenty lines
  of code can do.

## Reporting bugs

Please include your OS, Python version, what you did, what you expected, and what
happened. If it is an ordering or geometry problem, the image dimensions and the
PDF settings you used are the fastest path to a fix. Do not attach real passport
or ID images — a synthetic image of the same size reproduces almost every bug.

## Security

This is a local, loopback-only tool that reads files your account can already
read. If you find a way to make it do something beyond that — or to make it
listen on anything other than `127.0.0.1` — please open an issue rather than a
public pull request.
