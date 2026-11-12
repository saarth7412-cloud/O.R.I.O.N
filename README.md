# O.R.I.O.N. Engine (Agent 2)

This package holds the first two engine files:

- `src/engine/main.py`: the FastAPI server on 127.0.0.1:8765
- `src/engine/vision_core.py`: screen capture and the local Ollama vision model

## Part 1: Put the folder in the right place

1. Unzip the download. You should get a folder named `orion`.
2. Move that folder into your home folder so the path is `~/orion`.
   In Finder, drag it into your user folder (the one with your name and a house icon).
3. Open **Terminal** (press `Cmd + Space`, type `Terminal`, press Enter).
4. Check that the folder is where you expect:
   ```bash
   cd ~/orion/src/engine
   ls
   ```
   You should see `main.py` and `vision_core.py` in the list.

## Part 2: Set up Python (only once)

Copy each line below, paste it into Terminal, and press Enter. Wait for each one to finish before pasting the next.

```bash
cd ~/orion/src/engine
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install "fastapi" "uvicorn[standard]" "pydantic" "requests"
```

If `python3.12` says "command not found," install it with `brew install python@3.12` and try again.

## Part 3: Check that the code works

Make sure the environment is active. Your prompt should start with `(.venv)`. If it doesn't, run `source .venv/bin/activate`.

```bash
python -m py_compile main.py vision_core.py && echo "SYNTAX OK"
```

You should see `SYNTAX OK`.

Start the server in this Terminal window:

```bash
uvicorn main:app --host 127.0.0.1 --port 8765
```

Open a new tab with `Cmd + T`, go to the folder, and check the server:

```bash
curl -s http://127.0.0.1:8765/health
```

Expected output: `{"status":"ok","clients":0}`

Go back to the first tab and press `Ctrl + C` to stop the server.

## Part 4: Check screen capture and Ollama

```bash
cd ~/orion/src/engine
source .venv/bin/activate
python -c "from vision_core import capture_screen_base64 as c; print(len(c()), 'base64 chars')"
curl -s http://localhost:11434/api/tags
```

- A large number of characters means screen capture works.
- If you see a permission error or a tiny number, turn on Screen Recording for your terminal: System Settings → Privacy & Security → Screen Recording. Then quit and reopen Terminal.
- The `curl` command should list `qwen2-vl`. If it doesn't, run `ollama pull qwen2-vl`.

## Part 5: Save this project to GitHub

First, create an empty repository on github.com. Don't add a README or .gitignore there, because this package already has them.

Then run these from `~/orion`:

```bash
cd ~/orion
git status
```

If Git says "not a git repository," run:

```bash
git init -b main
```

If `git remote -v` prints nothing, connect GitHub (replace the placeholder with your own URL):

```bash
git remote add origin https://github.com/<your-username>/<your-repo>.git
```

Stage, commit, and push:

```bash
git add .gitignore README.md src/engine/main.py src/engine/vision_core.py
git status
git commit -m "feat(engine): add FastAPI orchestrator and in-memory screen capture VLM module"
git push -u origin main
```

When GitHub asks for a password, use a Personal Access Token. You can create one at github.com under Settings → Developer settings → Personal access tokens. Or install GitHub CLI with `brew install gh`, then run `gh auth login`.
