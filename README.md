# B2B-Prod-API

Runs the 5 B2B production APIs twice a day (9:00 AM and 9:00 PM IST) using GitHub Actions and posts the result of each run to Slack.

## How it works
1. Generates a fresh access token (tokens expire after 20 minutes, and one run takes only seconds).
2. Calls every API listed in `apis.yaml`.
3. Posts ✅/❌ per API with the status code and response time to Slack. Failures include a response snippet.
   If token generation fails, it sends a single alert instead.

## Setup
1. Create a Slack Incoming Webhook for the channel (Slack → Apps → Incoming Webhooks → Add to Slack → pick channel).
2. In GitHub, go to **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `SLACK_WEBHOOK_URL`
   - every `${...}` variable used in `apis.yaml` (for example `B2B_KEY` and `B2B_SECRET`)
3. Any new secret must also be added to the `env:` block in `.github/workflows/b2b-api-check.yml`.
4. Test it with **Actions → B2B Prod API Check → Run workflow**.

## Run locally
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in values
python run_checks.py
```
Response bodies are saved to `responses/` (gitignored, and uploaded as a workflow artifact in CI).

## Notes
- GitHub's scheduled runs can start a few minutes late during busy periods.
- GitHub disables scheduled workflows after 60 days with no repo activity. Re-enable it from the Actions tab if that happens.
