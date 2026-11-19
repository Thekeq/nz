"""Батьківський режим: батьки отримують нові оцінки дитини і можуть оплатити їй VIP.

Навіщо: за 4 місяці VIP не купив ніхто. У школяра немає картки, а Stars
купуються через App Store / Google Play. Сповіщення «нова оцінка» цінніші
батькам, ніж самій дитині, — і платити можуть саме вони.

Потік: дитина відкриває /parents → пересилає батькам одноразове посилання
t.me/<bot>?start=p_<token> → батьки підключаються, бачать статус і кнопки
оплати. Рахунок має payload vip:<план>:<id дитини>, тож successful_payment
видає VIP дитині, а не тому, хто платив (handlers/vip.successful_payment).
"""
import datetime
import logging
import time
from urllib.parse import quote

from aiogram import Router, F
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Message,
)

from handlers.vip import VIP_PLANS
from keyboards import payment_keyboard
from loader import db, BOT_USERNAME
from services import rich
from utils import answer_rich, safe_send

router = Router()
logger = logging.getLogger(__name__)

INVITE_PREFIX = "p_"
MONO_JAR_URL = "https://send.monobank.ua/jar/3bXsmYAcTp"
MONO_CARD = "4874 1000 2294 2034"


def child_vip_expires(child_id: int) -> int | None:
    """Коли закінчується VIP дитини: None — немає, 0 — назавжди."""
    vip_flag, expires = db.get_vip_status(child_id)
    if bool(vip_flag) and (expires == 0 or expires > int(time.time())):
        return expires
    return None


def _date(expires: int | None) -> str:
    if not expires:
        return ""
    return datetime.datetime.fromtimestamp(expires).strftime("%d.%m.%Y")


def parent_kb(child_id: int, active: bool) -> InlineKeyboardMarkup:
    verb = "Продовжити" if active else "Увімкнути"
    rows = [
        [InlineKeyboardButton(
            text=f"{verb} на {plan['label']} — {plan['stars']} ⭐️",
            callback_data=f"pa:buy:{key}:{child_id}",
        )]
        for key, plan in VIP_PLANS.items()
    ]
    rows.append([InlineKeyboardButton(text="🐈 Оплатити карткою (Monobank)",
                                      callback_data=f"pa:mono:{child_id}")])
    rows.append([InlineKeyboardButton(text="❌ Відписатися від оцінок",
                                      callback_data=f"pa:leave:{child_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _status_classic(child_name: str, expires: int | None) -> str:
    if expires is None:
        state = "⏸ Сповіщення на паузі — у дитини немає VIP. Оформіть його кнопками нижче."
    elif expires == 0:
        state = "✅ Сповіщення про нові оцінки увімкнені назавжди."
    else:
        state = f"✅ Сповіщення про нові оцінки увімкнені до <b>{_date(expires)}</b>."
    return f"👨‍👩‍👧 <b>{child_name or 'Дитина'}</b>\n{state}"


async def send_parent_status(message: Message, child_id: int, child_name: str):
    expires = child_vip_expires(child_id)
    await answer_rich(
        message,
        rich.parent_status(child_name=child_name, expires=expires, expires_text=_date(expires)),
        _status_classic(rich.esc(child_name), expires),
        reply_markup=parent_kb(child_id, expires is not None),
        parse_mode="HTML",
    )


# ===== Дитина: запросити батьків =====

def _invite_kb(invite_link: str, parents: list[tuple[int, str, str]]) -> InlineKeyboardMarkup:
    share_text = "Підключись до моїх оцінок у NZ — нові будуть приходити тобі в Telegram 👇"
    rows = [[InlineKeyboardButton(
        text="📤 Надіслати батькам",
        url=f"https://t.me/share/url?url={quote(invite_link)}&text={quote(share_text)}",
    )]]
    for parent_id, name, _ in parents:
        rows.append([InlineKeyboardButton(
            text=f"❌ Відключити: {name or 'без імені'}"[:60],
            callback_data=f"pa:unlink:{parent_id}",
        )])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _invite_view(user) -> tuple[str, str, InlineKeyboardMarkup]:
    token = db.get_or_create_parent_invite(user.id, user.first_name or "")
    link = f"https://t.me/{BOT_USERNAME}?start={INVITE_PREFIX}{token}"
    parents = db.get_parents(user.id)
    classic = (
        "👨‍👩‍👧 <b>Для батьків</b>\n\n"
        "Підключи маму чи тата — і твої нові оцінки приходитимуть їм у Telegram. "
        "Пароль від щоденника не передається. Батьки можуть оплатити тобі VIP.\n\n"
        f"Посилання (одноразове, 7 днів):\n<code>{link}</code>"
    )
    return rich.parent_invite(invite_link=link, parents=parents), classic, _invite_kb(link, parents)


@router.message(Command("parents"))
async def parents_cmd(message: Message):
    user = message.from_user
    db.ensure_user(user.id)
    children = db.get_children(user.id)
    for child_id, child_name in children:
        await send_parent_status(message, child_id, child_name)
    # батькам без власного щоденника меню запрошення ні до чого
    if children and not db.has_credentials(user.id):
        return
    db.record_command_metric("funnel:parent_menu", 0)
    rich_html, classic, kb = _invite_view(user)
    await answer_rich(message, rich_html, classic, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data == "pa:menu")
async def parents_menu_cb(callback: CallbackQuery):
    await callback.answer()
    db.record_command_metric("funnel:parent_menu", 0)
    rich_html, classic, kb = _invite_view(callback.from_user)
    await answer_rich(callback.message, rich_html, classic, reply_markup=kb, parse_mode="HTML")


@router.callback_query(F.data.startswith("pa:unlink:"))
async def unlink_parent_cb(callback: CallbackQuery):
    parent_id = int(callback.data.rsplit(":", 1)[1])
    child = callback.from_user
    if not db.unlink_parent(parent_id, child.id):
        await callback.answer("Вже відключено", show_alert=True)
        return
    await callback.answer("Відключено ✅", show_alert=True)
    await safe_send(parent_id, f"ℹ️ {rich.esc(child.first_name or 'Дитина')} відключив(ла) вас від своїх оцінок.")
    await callback.message.edit_reply_markup(reply_markup=_invite_view(child)[2])


# ===== Батьки =====

@router.message(CommandStart(deep_link=True, magic=F.args.startswith(INVITE_PREFIX)))
async def parent_start(message: Message, command: CommandObject):
    parent = message.from_user
    db.ensure_user(parent.id)
    token = command.args[len(INVITE_PREFIX):]
    linked = db.redeem_parent_invite(token, parent.id, parent.full_name or "")
    if not linked:
        await message.answer(
            "😕 Це посилання вже використане або застаріло.\n"
            "Попросіть дитину надіслати нове: команда /parents у боті."
        )
        return

    child_id, child_name = linked
    db.record_command_metric("funnel:parent_linked", 0)
    logger.info("Parent %s linked to child %s", parent.id, child_id)
    await safe_send(
        child_id,
        f"👨‍👩‍👧 <b>{rich.esc(parent.full_name or 'Батьки')}</b> тепер отримує твої нові оцінки.\n"
        "Керувати — /parents",
        parse_mode="HTML",
    )
    await answer_rich(
        message, rich.parent_welcome(child_name=child_name),
        f"👋 Ви підключилися до щоденника <b>{rich.esc(child_name or 'дитини')}</b>. "
        "Нові оцінки приходитимуть сюди.",
        parse_mode="HTML",
    )
    await send_parent_status(message, child_id, child_name)


def _linked_child(callback: CallbackQuery) -> tuple[int, str] | None:
    child_id = int(callback.data.rsplit(":", 1)[1])
    name = db.get_child_name(callback.from_user.id, child_id)
    return None if name is None else (child_id, name)


@router.callback_query(F.data.startswith("pa:buy:"))
async def parent_buy_cb(callback: CallbackQuery):
    _, _, plan_key, _ = callback.data.split(":")
    plan = VIP_PLANS.get(plan_key)
    linked = _linked_child(callback)
    if not plan or not linked:
        await callback.answer("⚠️ Ви вже не підключені до цієї дитини.", show_alert=True)
        return
    child_id, child_name = linked
    db.record_command_metric(f"funnel:invoice:{plan_key}", 0)
    db.record_command_metric("funnel:parent_invoice", 0)
    title = f"VIP для {child_name or 'дитини'}"
    await callback.message.answer_invoice(
        title=title[:32],
        description=(f"Нові оцінки — вам і дитині одразу, як їх поставили. "
                     f"Плюс нагадування про уроки і ШІ-помічник. Термін: {plan['label']}."),
        payload=f"vip:{plan_key}:{child_id}",
        provider_token="",
        currency="XTR",
        prices=[LabeledPrice(label=plan["title"], amount=plan["stars"])],
        reply_markup=payment_keyboard(plan["stars"]),
    )
    await callback.answer()


@router.callback_query(F.data.startswith("pa:mono:"))
async def parent_mono_cb(callback: CallbackQuery):
    linked = _linked_child(callback)
    if not linked:
        await callback.answer("⚠️ Ви вже не підключені до цієї дитини.", show_alert=True)
        return
    child_id, _ = linked
    await callback.answer()
    db.record_command_metric("funnel:parent_mono", 0)
    prices = " / ".join(str(p["stars"]) for p in VIP_PLANS.values())
    labels = " / ".join(p["label"] for p in VIP_PLANS.values())
    await callback.message.answer(
        "🐈 <b>Оплата карткою через Monobank</b>\n\n"
        f"Суми: <b>{prices} грн</b> — {labels}\n"
        f"Банка: {MONO_JAR_URL}\n"
        f"Номер: <code>{MONO_CARD}</code>\n\n"
        f"⚠️ <b>Обов'язково</b> вкажіть у коментарі ID дитини: <code>{child_id}</code>\n"
        "<i>Без нього платіж не знайти. VIP вмикаємо вручну, зазвичай протягом дня.</i>",
        parse_mode="HTML", disable_web_page_preview=True,
    )


@router.callback_query(F.data.startswith("pa:leave:"))
async def parent_leave_cb(callback: CallbackQuery):
    linked = _linked_child(callback)
    if not linked or not db.unlink_parent(callback.from_user.id, linked[0]):
        await callback.answer("Ви вже відписані", show_alert=True)
        return
    await callback.answer("Готово ✅")
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer(
        "Ви відписалися від оцінок. Підключитися знову — попросіть у дитини нове посилання."
    )
