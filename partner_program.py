"""One affiliate account, independent links for each destination."""
import asyncio

class PartnerProgram:
    def __init__(self, store, api, settings):
        self.store=store
        self.api=api
        self.settings=settings

    def directions(self):
        return {
            'exchange': {'campaign_id':self.settings.tracknow_exchange_campaign_id,
                         'title':'CinCin Exchange', 'description':'Пополнение криптовалютой',
                         'button':'💱 CinCin Exchange — пополнение криптовалютой'},
            'cards': {'campaign_id':self.settings.tracknow_cards_campaign_id,
                      'title':'CinCin Cards', 'description':'Пополнение рублями РФ через СБП',
                      'button':'💳 CinCin Cards — пополнение рублями РФ через СБП'},
        }

    def direction(self, key):
        result=self.directions().get(key)
        if not result or result['campaign_id']<=0:
            raise ValueError('Это направление пока не подключено.')
        return result

    async def issue(self, uid, key, email=None):
        if not self.settings.tracknow_enabled:
            raise ValueError('Партнёрская программа пока не подключена.')
        direction=self.direction(key)
        user=self.store.one('SELECT * FROM users WHERE id=?',(uid,))
        if not user or user['blocked']:
            raise ValueError('Получение ссылки недоступно.')
        affiliate=user['affiliate_id']
        if not affiliate:
            if not email:raise ValueError('Для создания партнёрского аккаунта нужен email.')
            response=await asyncio.to_thread(self.api.register,uid,user['name'],email)
            affiliate=response.get('data',{}).get('id')
            if not isinstance(affiliate,int):
                raise ValueError('Не удалось получить ID партнёра. Обратитесь к менеджеру.')
            self.store.run('UPDATE users SET affiliate_id=? WHERE id=? AND affiliate_id IS NULL',(affiliate,uid))
            self.store.audit(uid,'affiliate_created',affiliate)
        await asyncio.to_thread(self.api.assign,affiliate,direction['campaign_id'])
        result=await asyncio.to_thread(self.api.link,affiliate,direction['campaign_id'])
        link=result.get('link','')
        if not link.startswith(('https://','http://')):
            raise ValueError('Партнёрская ссылка пока недоступна. Аккаунт сохранён, попробуйте ещё раз.')
        self.store.save_partner_link(uid,direction['campaign_id'],link)
        self.store.audit(uid,'partner_link',direction['campaign_id'])
        return direction,link
