"""All deployment credentials and integration settings come from one private file."""
from dataclasses import dataclass, field
from pathlib import Path
import os
import tomllib

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / 'config.toml'

@dataclass(frozen=True)
class Settings:
    telegram_token: str = field(default='', repr=False)
    owner_ids: tuple[int, ...] = ()
    youtube_api_key: str = field(default='', repr=False)
    google_spreadsheet_id: str = ''
    google_service_account: dict = field(default_factory=dict, repr=False)
    tracknow_enabled: bool = False
    tracknow_api_key: str = field(default='', repr=False)
    tracknow_campaign_id: int = 0
    tracknow_exchange_campaign_id: int = 338
    tracknow_cards_campaign_id: int = 339
    tracknow_campaign_title: str = 'cincin_banner_test'
    tracknow_base_url: str = 'https://apiv2.tracknow.io/api/v2'
    views_interval: int = 900
    partner_interval: int = 60
    config_supplied: bool = field(default=False, repr=False)
    storage_directory: str = ''
    support_username: str = 'cincinb2b'

    @property
    def support_url(self):
        return 'https://t.me/'+self.support_username.lstrip('@')

    @property
    def data_dir(self):
        raw=os.environ.get('RAILWAY_VOLUME_MOUNT_PATH') or self.storage_directory
        path=Path(raw).expanduser() if raw else ROOT/'data'
        return path if path.is_absolute() else ROOT/path

    @property
    def database_path(self):
        return self.data_dir/'bot.sqlite3'

    @property
    def active_campaign_ids(self):
        return tuple(dict.fromkeys(cid for cid in (self.tracknow_exchange_campaign_id, self.tracknow_cards_campaign_id, self.tracknow_campaign_id) if cid > 0))

    @property
    def spreadsheet_url(self):
        return 'https://docs.google.com/spreadsheets/d/' + self.google_spreadsheet_id

    def validate(self):
        if not self.config_supplied:
            raise ValueError('Заполните CONFIG_TOML в Railway Variables или создайте файл config.toml.')
        missing=[]
        for name,value in [('telegram.token',self.telegram_token),('telegram.owner_ids',self.owner_ids),
                           ('youtube.api_key',self.youtube_api_key),('google_sheets.spreadsheet_id',self.google_spreadsheet_id)]:
            if not value:missing.append(name)
        for key in ('client_email','private_key','project_id'):
            if not self.google_service_account.get(key):missing.append('google_sheets.service_account.'+key)
        if self.tracknow_enabled:
            if not self.tracknow_api_key:missing.append('tracknow.api_key')
            if not self.active_campaign_ids:missing.append('tracknow.exchange_campaign_id / tracknow.cards_campaign_id')
        if missing:raise ValueError('Заполните поля config.toml: '+', '.join(missing))
        if self.views_interval<30 or self.partner_interval<30:
            raise ValueError('Интервалы обновления в config.toml должны быть не меньше 30 секунд.')

def load_settings(path=CONFIG_PATH):
    path=Path(path)
    source=os.environ.get('CONFIG_TOML') if path==CONFIG_PATH else None
    if source is None:
        if not path.exists():return Settings()
        source=path.read_text()
    try:
        data=tomllib.loads(source.lstrip('\ufeff'))
        telegram=data.get('telegram',{});youtube=data.get('youtube',{})
        google=data.get('google_sheets',{});partner=data.get('tracknow',{});polling=data.get('polling',{})
        account={'type':'service_account','token_uri':'https://oauth2.googleapis.com/token','universe_domain':'googleapis.com',**google.get('service_account',{})}
        return Settings(telegram_token=telegram.get('token',''),owner_ids=tuple(int(x) for x in telegram.get('owner_ids',[])),
                        youtube_api_key=youtube.get('api_key',''),google_spreadsheet_id=google.get('spreadsheet_id',''),
                        google_service_account=account,tracknow_enabled=partner.get('enabled',False),
                        tracknow_api_key=partner.get('api_key','').removeprefix('Bearer '),
                        tracknow_campaign_id=int(partner.get('campaign_id',0)),
                        tracknow_exchange_campaign_id=int(partner.get('exchange_campaign_id',338)),
                        tracknow_cards_campaign_id=int(partner.get('cards_campaign_id',339)),
                        tracknow_campaign_title=partner.get('campaign_title','cincin_banner_test'),
                        tracknow_base_url=partner.get('base_url','https://apiv2.tracknow.io/api/v2').rstrip('/'),
                        views_interval=int(polling.get('youtube_seconds',900)),partner_interval=int(polling.get('tracknow_seconds',60)),
                        config_supplied=True,storage_directory=data.get('storage',{}).get('directory',''),
                        support_username=data.get('support',{}).get('username','cincinb2b').lstrip('@'))
    except (tomllib.TOMLDecodeError,ValueError,TypeError,AttributeError):
        raise ValueError('Неверный формат CONFIG_TOML или config.toml. Сверьте содержимое с config.example.toml.') from None

settings=load_settings()
