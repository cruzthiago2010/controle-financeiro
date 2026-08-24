<p align="center">
  <a href="CONTRIBUTING.md">Português</a> · <b>English</b>
</p>

# Contributing

FinanCerto is a self-hosted personal finance app, built for anyone to run on
their own server. Every contribution is welcome — including "I installed it and
didn't understand this screen", which is often worth more than code.

## Before writing code

Open an [issue](https://github.com/cruzthiago2010/controle-financeiro/issues)
describing what you intend to do. It avoids duplicated work and settles whether
the idea fits the project. For a small, obvious fix (typo, broken link, wrong
arithmetic), send the pull request directly.

**Security flaws don't go in issues.** See [SECURITY.en.md](SECURITY.en.md).

## Running the project

```bash
git clone https://github.com/cruzthiago2010/controle-financeiro.git
cd controle-financeiro
cp .env.example .env      # fill in ADMIN_PASSWORD and SECRET_KEY
docker compose up -d --build
```

The app comes up at `http://localhost:8420`. The `data/` folder holds the SQLite
database, attachments and the session key — none of it is committed.

To work on the Python without rebuilding the image every time:

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python app.py            # needs tesseract-ocr and libzbar0 on the system
```

## How the code is organized

| Path | What it is |
|---|---|
| `app.py` | the whole Flask backend: REST API, auth, and file serving |
| `static/index.html` | the app itself — a single page, no framework, no build |
| `static/login.html`, `static/registro.html` | login and public sign-up screens |
| `migrations/*.sql` | schema changes, applied in order at startup |
| `android/` | Android app (WebView with fingerprint, notifications, widget) |
| `testes/` | smoke tests for uploads, plus ticker/path escape attempts |
| `docs/screenshots/` | captures used in the README |

There is no frontend build step: plain HTML, CSS and JavaScript, served as-is.
Keeping it that way is deliberate — whoever clones the repo must be able to
start it with `docker compose up` and nothing else.

## Conventions

- **The code is written in Portuguese.** Variable, function and route names and
  comments stay in Portuguese, like the rest of the file. Only the **interface**
  and the **public documentation** are translated (pt-BR and English).
- **Commit messages in Portuguese, imperative**, stating the effect for the
  person using the app. No conventional-commit prefixes (`feat:`, `fix:`).
- **Every schema change is a new migration** in `migrations/`, numbered in
  sequence. Never edit a published migration — someone has already run it
  against a real database.
- **Nothing may depend on anyone's particular server.** No hardcoded domain, no
  IP, no embedded token: anyone must be able to host it. Whatever varies per
  install goes to `.env`, with a sensible default and a line explaining it in
  `.env.example`.
- **What the screen shows must be true.** If the data that exists is "date of
  the last backup", the label says exactly that — don't invent an indirect
  indicator that looks more useful.

## Pull requests

1. Branch off `main`.
2. Check that the container starts from scratch: `docker compose up --build`
   against an empty `data/`, to catch migration errors.
3. If you touched the UI, test it on a phone too — the app is used mostly on
   mobile and installed as a PWA.
4. Describe **what changes for the person using it**, not only what changed in
   the file. For visual changes, attach a before/after capture.
5. CI runs `ruff`, compiles the Python, builds the Docker image and runs the
   attachment tests. They have to pass.

To run the attachment tests locally, with the container already up:

```bash
docker compose cp testes/teste_anexos.py controle-financeiro:/app/teste_anexos.py
docker compose exec -e SENHA=<your password> controle-financeiro python /app/teste_anexos.py
```

It generates a payslip and a receipt on the fly and checks the values read
back — this is the test that catches a Pillow or pypdf update which passes the
`import` and only breaks on a real file.

Large pull requests take longer to review. If the change is big, split it into
parts that stand on their own.

## Using AI

Part of this codebase was written with AI assistance, and AI-assisted
contributions are welcome. The bar is the usual one: whoever signs the pull
request owns its quality. Send what you understand and can defend — not what
came out finished and you never read.

## Code of conduct

The [Code of Conduct](CODE_OF_CONDUCT.md) applies in every project space.

## License

By contributing, you agree that your contribution is distributed under the
[AGPL-3.0](LICENSE), the same license as the project.
