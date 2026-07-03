"""Use case: create and drive an interactive design session."""

from __future__ import annotations

import uuid

from application.agents.design_agent import DesignAgent
from application.agents.events import AgentObserver, NullObserver
from application.design_toolkit import DesignToolkit
from application.session import DesignSession
from application.tools.base import ToolContext
from config.settings import Settings
from domain.models.conversation import Message


class SessionHandle:
    """A live session: holds conversation history + tool context, exposes `send`."""

    def __init__(self, *, agent: DesignAgent, ctx: ToolContext) -> None:
        self._agent = agent
        self._ctx = ctx
        self._history: list[Message] = []

    @property
    def session_id(self) -> str:
        return self._ctx.session.session_id

    @property
    def session(self) -> DesignSession:
        return self._ctx.session

    def send(self, user_text: str, observer: AgentObserver | None = None) -> None:
        self._history = self._agent.run_turn(
            ctx=self._ctx,
            history=self._history,
            user_text=user_text,
            observer=observer or NullObserver(),
        )


class DesignSessionCoordinator:
    """Factory that assembles a `SessionHandle` from injected dependencies."""

    def __init__(
        self,
        *,
        agent: DesignAgent,
        toolkit: DesignToolkit,
        settings: Settings,
    ) -> None:
        self._agent = agent
        self._toolkit = toolkit
        self._settings = settings

    def new_session(self, session_id: str | None = None) -> SessionHandle:
        sid = session_id or uuid.uuid4().hex[:12]
        ctx = ToolContext(
            session=DesignSession(session_id=sid),
            toolkit=self._toolkit,
            settings=self._settings,
        )
        return SessionHandle(agent=self._agent, ctx=ctx)
