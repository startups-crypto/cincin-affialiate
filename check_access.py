"""Read-only connection checks using config.toml. Never print credentials."""
import asyncio
from telegram import Bot
from services import Sheets, YouTube, Tracknow, checked
from settings import settings

async def main():
    settings.validate()
    failures = 0
    try:
        sheets = Sheets()
        checked(sheets.session.get(sheets.url, params={'fields': 'sheets.properties.title'}, timeout=30))
        print('Google Sheets: OK')
    except Exception as e:
        failures += 1
        print('Google Sheets:', type(e).__name__)
        print('Share the spreadsheet as Editor with:', settings.google_service_account.get('client_email', ''))
    try:
        YouTube().videos(['jNQXAC9IVRw'])
        print('YouTube Data API: OK')
    except Exception as e:
        failures += 1
        print('YouTube Data API:', type(e).__name__)
    try:
        async with Bot(settings.telegram_token) as bot:
            user = await bot.get_me()
            print('Telegram: OK @' + user.username)
    except Exception as e:
        failures += 1
        print('Telegram:', type(e).__name__)
    if settings.tracknow_enabled:
        try:
            api = Tracknow()
            api.get('me')
            for cid in settings.active_campaign_ids:
                api.get(f'campaigns/{cid}')
            print('Tracknow: OK')
        except Exception as e:
            failures += 1
            print('Tracknow:', type(e).__name__)
    else:
        print('Tracknow: disabled in config.toml')
    return failures

if __name__ == '__main__':
    raise SystemExit(1 if asyncio.run(main()) else 0)
