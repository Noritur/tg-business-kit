"""UI strings per language, overridable from the business profile.

A profile may replace any string under `texts`, but only keys that exist here
and only with placeholders the default already uses: a typo in a key or in a
{placeholder} is caught by `kit validate`, not by a client in production.
"""

from datetime import date
from string import Formatter

LANGUAGES = ("uk", "en")

DEFAULTS: dict[str, dict[str, str]] = {
    "uk": {
        "greeting": (
            "Вітаю, це {name} — {tagline}.\n\n{about}\n\n"
            "Оберіть, що цікавить, або просто напишіть питання текстом — я відповім."
        ),
        "btn_book": "Записатись",
        "btn_prices": "Послуги і ціни",
        "btn_schedule": "Розклад",
        "btn_faq": "Часті питання",
        "btn_menu": "Меню",
        "btn_back_menu": "Назад до меню",
        "btn_back": "Назад",
        "btn_confirm": "Так, записатись",
        "btn_other_time": "Обрати інший час",
        "btn_other_questions": "Інші питання",
        "prices_title": "Послуги і ціни:",
        "schedule_title": "Найближчі дні з вільними слотами:",
        "schedule_note": "Час місцевий ({city}). День і час обираються під час запису.",
        "faq_title": "Часті питання — оберіть або напишіть своє текстом:",
        "choose_service": "Що вас цікавить?",
        "choose_day": "Оберіть день (час місцевий, {city}):",
        "choose_time": "{day} — оберіть час:",
        "no_slots": "Наразі вільних слотів нема — напишіть нам, підберемо час вручну.",
        "confirm": "Перевірте запис:\n\n{service}\n{day}, {time}\n\nПідтверджуєте?",
        "booked": (
            "Готово, ви записані:\n\n{service}\n{day}, {time}\n\n"
            "Ми зв'яжемося з вами для підтвердження. "
            "Якщо зручніше телефоном — просто надішліть номер у відповідь."
        ),
        "slot_unavailable": "Цей час уже недоступний — оберіть, будь ласка, інший.",
        "too_many_bookings": (
            "У вас уже є записи на найближчі дні. "
            "Щоб додати ще один або перенести, напишіть нам: {contact}"
        ),
        "save_failed": "Не вдалося зберегти запис — напишіть нам напряму, будь ласка: {contact}",
        "phone_saved": "Номер збережено, дякую. Ми зателефонуємо для підтвердження.",
        "handoff_reply": (
            "Дякую за питання — передаю його команді, вам відпишуть найближчим часом."
        ),
        "owner_new_lead": "Новий запис:\n{service}\n{day}, {time}\nКлієнт: {client}",
        "owner_phone": "Клієнт додав телефон до запису:\n{client}\n{phone}",
        "owner_question": "Питання, на яке бот не відповів:\n{client}\n\n{question}",
        "leads_empty": "Записів поки нема.",
        "leads_title": "Останні записи ({count}):",
        "cmd_start": "Головне меню",
        "cmd_book": "Записатися",
        "cmd_prices": "Послуги і ціни",
        "cmd_faq": "Часті питання",
        "cmd_leads": "Останні записи (для власника)",
    },
    "en": {
        "greeting": (
            "Hi, this is {name} — {tagline}.\n\n{about}\n\n"
            "Pick an option below or just type your question — I'll answer."
        ),
        "btn_book": "Book a visit",
        "btn_prices": "Services and prices",
        "btn_schedule": "Opening times",
        "btn_faq": "FAQ",
        "btn_menu": "Menu",
        "btn_back_menu": "Back to menu",
        "btn_back": "Back",
        "btn_confirm": "Yes, book it",
        "btn_other_time": "Pick another time",
        "btn_other_questions": "Other questions",
        "prices_title": "Services and prices:",
        "schedule_title": "Next days with free slots:",
        "schedule_note": "Times are local ({city}). You pick the day and time when booking.",
        "faq_title": "Frequently asked — pick one or type your own question:",
        "choose_service": "What do you need?",
        "choose_day": "Pick a day (local time, {city}):",
        "choose_time": "{day} — pick a time:",
        "no_slots": "No free slots right now — message us and we'll find a time.",
        "confirm": "Please check your booking:\n\n{service}\n{day}, {time}\n\nConfirm?",
        "booked": (
            "Done, you're booked:\n\n{service}\n{day}, {time}\n\n"
            "We'll get in touch to confirm. "
            "Prefer a call? Just send your phone number."
        ),
        "slot_unavailable": "That time is no longer available — please pick another one.",
        "too_many_bookings": (
            "You already have upcoming bookings. "
            "To add another one or reschedule, contact us: {contact}"
        ),
        "save_failed": "We couldn't save the booking — please contact us directly: {contact}",
        "phone_saved": "Number saved, thank you. We'll call to confirm.",
        "handoff_reply": (
            "Thanks for the question — I'm passing it to the team, they'll reply soon."
        ),
        "owner_new_lead": "New booking:\n{service}\n{day}, {time}\nClient: {client}",
        "owner_phone": "A client added a phone number to their booking:\n{client}\n{phone}",
        "owner_question": "A question the bot could not answer:\n{client}\n\n{question}",
        "leads_empty": "No bookings yet.",
        "leads_title": "Latest bookings ({count}):",
        "cmd_start": "Main menu",
        "cmd_book": "Book a visit",
        "cmd_prices": "Services and prices",
        "cmd_faq": "Frequently asked questions",
        "cmd_leads": "Latest bookings (owner only)",
    },
}

WEEKDAYS_SHORT = {
    "uk": ["пн", "вт", "ср", "чт", "пт", "сб", "нд"],
    "en": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
}
MONTHS_SHORT_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]  # fmt: skip


def placeholders(template: str) -> set[str]:
    return {field for _, field, _, _ in Formatter().parse(template) if field}


def check_overrides(language: str, overrides: dict[str, str]) -> list[str]:
    """Problems with a profile's `texts` section, as readable lines."""
    defaults = DEFAULTS[language]
    problems = []
    for key, value in overrides.items():
        if key not in defaults:
            problems.append(f"texts.{key}: unknown key")
            continue
        extra = placeholders(value) - placeholders(defaults[key])
        if extra:
            allowed = ", ".join(sorted(placeholders(defaults[key]))) or "none"
            problems.append(
                f"texts.{key}: unknown placeholder(s) {', '.join(sorted(extra))}"
                f" (allowed: {allowed})"
            )
    return problems


class Texts:
    def __init__(self, language: str, overrides: dict[str, str] | None = None):
        self.language = language
        self._strings = {**DEFAULTS[language], **(overrides or {})}

    def __call__(self, key: str, **values: object) -> str:
        return self._strings[key].format(**values)

    def day(self, d: date, with_year: bool = False) -> str:
        weekday = WEEKDAYS_SHORT[self.language][d.weekday()]
        if self.language == "en":
            label = f"{weekday} {d.day} {MONTHS_SHORT_EN[d.month - 1]}"
            return f"{label} {d.year}" if with_year else label
        label = f"{weekday} {d:%d.%m}"
        return f"{label}.{d.year}" if with_year else label
