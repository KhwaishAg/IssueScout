# GitHub App setup (P0.6)

Registering the app happens on your GitHub account, so you do it by hand. Nothing here
goes through Claude, and no secret is ever pasted into chat or committed.

## 1. Create the sandbox repository

New **public** repository, e.g. `issuescout-sandbox`, with a few Python files (any small
project). The 10 scripted issues in P3.11 will be opened here.

## 2. Register the development app

GitHub → Settings → Developer settings → GitHub Apps → **New GitHub App**.

| Field | Value |
| --- | --- |
| Name | Must be globally unique; try `IssueScout`, else `IssueScout-dev-<yourhandle>` (this answers part of SRS Appendix D, question 1) |
| Homepage URL | Your repository URL |
| Webhook | Active. URL: any placeholder such as `https://example.com/webhooks/github` for now (a tunnel or the real URL comes in P3.3 / P4.1) |
| Webhook secret | A random string of 32+ characters (e.g. from a password manager) |
| Where can it be installed | Only on this account |

**Repository permissions (FR-APP-01, nothing else):**

- Contents: **Read-only**
- Issues: **Read and write**
- Pull requests: **Read-only**
- Metadata: **Read-only** (mandatory, selected automatically)

**Subscribe to events:** Issues, Issue comment, Push. (`installation` and
`installation_repositories` are delivered to every app automatically.)

## 3. Store the secrets locally

1. On the app page, click **Generate a private key**. A `.pem` file downloads.
2. Copy `.env.example` to `.env` (git-ignored) and add:

   ```
   GITHUB_APP_ID=<the numeric App ID>
   GITHUB_WEBHOOK_SECRET=<the secret you chose>
   GITHUB_APP_PRIVATE_KEY="<the whole .pem contents, newlines written as \n>"
   ```

   (Put the key on one line with literal `\n` between lines, or keep the `.pem` file
   outside the project folder and tell the P3.2 code its path.)
3. Do **not** keep the `.pem` inside the repository folder. `*.pem` and `.env*` are
   git-ignored as a backstop, not as a plan.

## 4. Install on the sandbox repository

App page → **Install App** → your account → **Only select repositories** → the sandbox.

## 5. Check ("Done when")

- The app appears under Settings → Applications → Installed GitHub Apps.
- No secret in history: `uv run python scripts/check_no_secrets.py` exits 0.

## Rotation (NFR-SEC-04)

Generating a new key or changing the webhook secret on the app page and updating `.env`
is all that is needed; no code reads these values except through `settings.py`.
