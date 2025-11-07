# NegahdarBot - Railway deployment guide (step-by-step)

This package is prepared to deploy NegahdarBot on Railway for quick testing.
Keep your sensitive values in Railway Environment Variables (do NOT hard-code them).

## Files included
- `negahdar_bot.py` : main bot file (reads config from env)
- `requirements.txt`
- `Procfile` : instructs Railway to run the worker

## Steps to deploy (quick)
1. Create a new GitHub repository and push these files.
2. Sign in to https://railway.app with GitHub and create a new project -> Deploy from GitHub.
3. Select your repository and connect.
4. Add Environment variables in Railway project's Settings -> Variables:
   - `BOT_API_ID` = your api_id (e.g. 29512591)
   - `BOT_API_HASH` = your api_hash
   - `BOT_TOKEN` = your bot token (starts with 123:...)
   - `ADMIN_ID` = your telegram user id (e.g. 6427804786)
   - `SOURCE_CHANNEL` = @your_channel (or numeric -100... id)
5. Deploy. Check Logs. You should see `NegahdarBot starting...` and then running.
6. Test in Telegram: send `ping` or `/start` to the bot, or send a code like `1` if you imported old data.

## Notes & Troubleshooting
- If Railway restarts the process (free tier sleeps), it may temporarily disconnect; for reliable 24/7 use upgrade or move to Fly.io/DigitalOcean.
- Keep your bot token secret. Regenerate token if it's ever exposed.