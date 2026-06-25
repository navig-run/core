"""navig.social — social publishing framework for the Studio scheduler.

A one-to-many sibling of the 1:1 ``navig.messaging`` layer: composers create a
:class:`PostContent`, the :class:`PublishDispatcher` fans it out to the selected
targets — messaging adapters (Telegram/Discord/WhatsApp/SMS) for chats and
channels, and :class:`SocialPublisher` plugins (X, LinkedIn, Reddit, Facebook,
Instagram, YouTube) for social feeds.
"""

from __future__ import annotations

from navig.social.types import PostContent, PublishReceipt, PublishTarget
from navig.social.dispatcher import PublishDispatcher
from navig.social.registry import get_publisher_registry

__all__ = [
    "PostContent",
    "PublishReceipt",
    "PublishTarget",
    "PublishDispatcher",
    "get_publisher_registry",
]
