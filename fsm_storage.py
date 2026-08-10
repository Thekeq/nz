import json
from typing import Any, Dict, Optional

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey, StateType


class SQLiteStorage(BaseStorage):
    """FSM-сховище поверх існуючої SQLite-бази.

    На відміну від MemoryStorage, стани переживають рестарт бота:
    юзер посеред авторизації чи оплати не «зависає» після деплою.
    Запити до SQLite — мікросекунди, тому синхронні виклики з event loop ок.
    """

    def __init__(self, db):
        self.db = db

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        self.db.fsm_set_state(self._key(key), value)

    async def get_state(self, key: StorageKey) -> Optional[str]:
        return self.db.fsm_get_state(self._key(key))

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        self.db.fsm_set_data(self._key(key), json.dumps(data, ensure_ascii=False))

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        try:
            return json.loads(self.db.fsm_get_data(self._key(key)))
        except (TypeError, ValueError):
            return {}

    async def close(self) -> None:
        pass
