"""Five-part terms reader; existing consent remains valid."""
from telegram import InlineKeyboardButton as Button, InlineKeyboardMarkup as Markup
from terms import RULE_PAGES

class TermsFlow:
    def __init__(self,store): self.store=store

    async def begin(self,message,context,uid):
        context.user_data['terms_page']=-1
        await self.send(message,context,uid,0)

    async def send(self,message,context,uid,index):
        user=self.store.user(uid)
        buttons=[]
        if index < len(RULE_PAGES)-1:
            buttons=[[Button('Далее ➡️',callback_data=f'terms:{index+1}')]]
        elif not user['accepted']:
            buttons=[[Button('✅ Принимаю условия',callback_data='accept')]]
        await message.reply_text(RULE_PAGES[index],parse_mode='HTML',reply_markup=Markup(buttons) if buttons else None)
        context.user_data['terms_page']=index

    async def next(self,update,context):
        q=update.callback_query
        try: index=int(q.data.split(':',1)[1])
        except ValueError: return
        if index != context.user_data.get('terms_page',-1)+1 or not 0<index<len(RULE_PAGES):
            await q.message.reply_text('Откройте условия заново кнопкой «📋 Условия пользования» и читайте по порядку.')
            return
        await self.send(q.message,context,update.effective_user.id,index)
        await q.edit_message_reply_markup(reply_markup=None)

    def require_complete(self,context,uid):
        if not self.store.user(uid)['accepted'] and context.user_data.get('terms_page')!=len(RULE_PAGES)-1:
            raise ValueError('Сначала прочитайте все пять частей условий кнопкой «📋 Условия пользования».')
