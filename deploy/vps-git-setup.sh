#!/bin/sh
# One-time setup on the VPS: create a bare repo + post-receive hook so that
# `git push` from your machine checks out the code and rebuilds automatically.
set -e

REPO="$HOME/chat-app.git"
WORKTREE="$HOME/chat-app"

if [ ! -d "$WORKTREE" ]; then
  echo "ERROR: expected working tree at $WORKTREE (must contain your .env.prod)." >&2
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "ERROR: git is not installed. Run: sudo apt-get update && sudo apt-get install -y git" >&2
  exit 1
fi

git init --bare "$REPO"

# The hook has absolute paths baked in (expanded now), so it needs no env.
cat > "$REPO/hooks/post-receive" <<HOOK
#!/bin/sh
set -e
echo "[deploy] checking out latest code..."
git --git-dir="$REPO" --work-tree="$WORKTREE" checkout -f main
cd "$WORKTREE"
echo "[deploy] building & restarting containers..."
docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build
echo "[deploy] done. migrations run automatically on backend start."
HOOK

chmod +x "$REPO/hooks/post-receive"
echo "OK: bare repo at $REPO with auto-deploy hook installed."
