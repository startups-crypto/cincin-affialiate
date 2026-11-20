"""Telegram HTML presentation of income; no accounting mutations."""
from html import escape

TURNOVER_NOTE = 'Учитываются все доступные операции по картам, открытым по вашей партнёрской ссылке. Доли цента накапливаются, а баланс округляется уже после суммирования.'

def format_source(data, source, note=True):
    labels = {'views':'📹 Просмотры', 'cpa':'💳 Оформление карт: $10 за карту', 'turnover':'🔄 Оборот по картам: 0,1%'}
    lines = [f'<b>{labels[source]}</b>',
             f"▫️ Начислено за всё время (с учётом отмен): ${data['earned']/100:.2f}",
             f"▫️ Выплачено: ${data['paid']/100:.2f}",
             f"▫️ Остаток: ${data['balance']/100:.2f}"]
    if source == 'views':
        lines += ['', 'Начисления предварительные, до проверки контента менеджером.']
    else:
        lines += [f"▫️ Ожидает подтверждения: ${data['pending_micro']/1000000:.6f}",
                  f"▫️ Тестовые операции: ${data['test_micro']/1000000:.6f} (в баланс не входят)"]
        if source == 'turnover' and note:
            lines += ['', TURNOVER_NOTE]
    return '\n'.join(lines)

def format_partner_report(income, updated):
    return '\n\n'.join([
        '<b>Отчёт по партнёрской программе CinCin Partners</b>\n'+escape(updated),
        'Условия для автора: $10 за каждое подтверждённое оформление карты и дополнительно 0,1% от оборота по всем доступным операциям карт, открытых по его партнёрской ссылке.',
        format_source(income['cpa'],'cpa',False),
        format_source(income['turnover'],'turnover',False),
        'В расчёт входят все доступные операции по картам, открытым по партнёрской ссылке. Доли цента накапливаются, а баланс округляется после суммирования.'
    ])
