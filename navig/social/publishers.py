"""Concrete social publishers.

Each ships with credential wiring keyed by the deck's ``vaultProvider`` name.
Where a single token suffices (X, Facebook Page, LinkedIn, Reddit) the publish
call hits the documented API endpoint; the OAuth/app-setup-heavy bits and
platform API limits are surfaced as clear errors rather than silent failures.

All network calls are best-effort and defensive — failures become a
``PublishReceipt`` with an ``error`` rather than raising.
"""

from __future__ import annotations

import logging

from navig.social.base import BasePublisher
from navig.social.credentials import get_config
from navig.social.types import PublishReceipt, RenderedPost

logger = logging.getLogger(__name__)


class TwitterPublisher(BasePublisher):
    name = "twitter"
    char_limit = 280
    supports_media = True

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        try:
            session = await self._session()
            try:
                headers = {"Authorization": f"Bearer {self.token()}", "Content-Type": "application/json"}
                async with session.post(
                    "https://api.twitter.com/2/tweets", json={"text": post.text[:280]}, headers=headers
                ) as resp:
                    data = await resp.json()
                    if resp.status in (200, 201):
                        tid = (data.get("data") or {}).get("id")
                        return PublishReceipt.success(self.name, target, id=tid,
                                                      url=f"https://x.com/i/web/status/{tid}" if tid else None)
                    return PublishReceipt.failure(self.name, target, _api_err(data))
            finally:
                await session.close()
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(self.name, target, str(exc))


class RedditPublisher(BasePublisher):
    name = "reddit"
    char_limit = None
    supports_media = False

    async def list_targets(self):
        # Reddit posts go to a subreddit; default from config if present.
        from navig.social.types import PublishTarget

        sub = get_config("reddit", "subreddit")
        return [PublishTarget(network="reddit", target=sub or "", display=f"r/{sub}" if sub else "reddit (set subreddit)")]

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        sub = (target or get_config("reddit", "subreddit") or "").lstrip("r/").strip("/")
        if not sub:
            return PublishReceipt.failure(self.name, target, "no subreddit — set one in Settings or the post target")
        title, _, selftext = post.text.partition("\n")
        try:
            session = await self._session()
            try:
                headers = {"Authorization": f"Bearer {self.token()}", "User-Agent": "navig-studio/1.0"}
                form = {"sr": sub, "kind": "self", "title": (title or post.text)[:300], "text": selftext}
                async with session.post("https://oauth.reddit.com/api/submit", data=form, headers=headers) as resp:
                    data = await resp.json()
                    url = (((data or {}).get("json") or {}).get("data") or {}).get("url")
                    if resp.status == 200 and not ((data.get("json") or {}).get("errors")):
                        return PublishReceipt.success(self.name, target, url=url)
                    return PublishReceipt.failure(self.name, target, _api_err(data))
            finally:
                await session.close()
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(self.name, target, str(exc))


class LinkedInPublisher(BasePublisher):
    name = "linkedin"
    char_limit = 3000
    supports_media = False  # media needs the asset-upload flow — text/link only here

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        author = target or get_config("linkedin", "author_urn")
        if not author:
            return PublishReceipt.failure(self.name, target, "missing author URN — set linkedin.author_urn in Settings")
        body = {
            "author": author,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": post.text},
                    "shareMediaCategory": "NONE",
                }
            },
            "visibility": {"com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"},
        }
        try:
            session = await self._session()
            try:
                headers = {"Authorization": f"Bearer {self.token()}", "X-Restli-Protocol-Version": "2.0.0",
                           "Content-Type": "application/json"}
                async with session.post("https://api.linkedin.com/v2/ugcPosts", json=body, headers=headers) as resp:
                    if resp.status in (200, 201):
                        return PublishReceipt.success(self.name, target, id=resp.headers.get("x-restli-id"))
                    return PublishReceipt.failure(self.name, target, _api_err(await _safe_json(resp)))
            finally:
                await session.close()
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(self.name, target, str(exc))


class FacebookPagePublisher(BasePublisher):
    name = "facebook"
    char_limit = None
    supports_media = False  # link posts supported; photo upload is a separate endpoint

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        page_id = target or get_config("facebook", "page_id")
        if not page_id:
            return PublishReceipt.failure(self.name, target, "missing page id — set facebook.page_id in Settings")
        params = {"message": post.text, "access_token": self.token() or ""}
        if post.link:
            params["link"] = post.link
        try:
            session = await self._session()
            try:
                async with session.post(f"https://graph.facebook.com/{page_id}/feed", data=params) as resp:
                    data = await _safe_json(resp)
                    if resp.status == 200 and data.get("id"):
                        return PublishReceipt.success(self.name, target, id=data["id"])
                    return PublishReceipt.failure(self.name, target, _api_err(data))
            finally:
                await session.close()
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(self.name, target, str(exc))


class InstagramPublisher(BasePublisher):
    name = "instagram"
    char_limit = 2200
    supports_media = True

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        ig_id = target or get_config("instagram", "ig_user_id")
        image_url = next((m.get("url") for m in post.media if m.get("url")), None)
        if not ig_id:
            return PublishReceipt.failure(self.name, target, "missing IG user id — set instagram.ig_user_id in Settings")
        if not image_url:
            return PublishReceipt.failure(
                self.name, target, "Instagram requires a publicly-reachable image URL (no local upload in this build)"
            )
        token = self.token() or ""
        try:
            session = await self._session()
            try:
                # 1) create media container
                async with session.post(
                    f"https://graph.facebook.com/{ig_id}/media",
                    data={"image_url": image_url, "caption": post.text, "access_token": token},
                ) as resp:
                    cdata = await _safe_json(resp)
                    creation_id = cdata.get("id")
                    if not creation_id:
                        return PublishReceipt.failure(self.name, target, _api_err(cdata))
                # 2) publish container
                async with session.post(
                    f"https://graph.facebook.com/{ig_id}/media_publish",
                    data={"creation_id": creation_id, "access_token": token},
                ) as resp2:
                    pdata = await _safe_json(resp2)
                    if resp2.status == 200 and pdata.get("id"):
                        return PublishReceipt.success(self.name, target, id=pdata["id"])
                    return PublishReceipt.failure(self.name, target, _api_err(pdata))
            finally:
                await session.close()
        except Exception as exc:  # noqa: BLE001
            return PublishReceipt.failure(self.name, target, str(exc))


class YouTubePublisher(BasePublisher):
    name = "youtube"
    char_limit = None
    supports_media = True

    async def publish(self, target: str, post: RenderedPost) -> PublishReceipt:
        if (r := self._require_auth(target)) is not None:
            return r
        # YouTube's public Data API has no community-post endpoint and full video
        # upload is out of scope for this build — surface that clearly.
        return PublishReceipt.failure(
            self.name, target,
            "YouTube text/community posting isn't available via the public API; video upload is out of scope in this build",
        )


# ── helpers ───────────────────────────────────────────────────


async def _safe_json(resp) -> dict:
    try:
        return await resp.json()
    except Exception:  # noqa: BLE001
        return {}


def _api_err(data: dict) -> str:
    if not isinstance(data, dict):
        return "API error"
    err = data.get("error")
    if isinstance(err, dict):
        return err.get("message") or err.get("error_description") or str(err)
    if isinstance(err, str):
        return err
    if data.get("errors"):
        return str(data["errors"])
    return str(data) if data else "API error"


# Every built-in publisher class — consumed by the registry.
BUILTIN_PUBLISHERS = [
    TwitterPublisher,
    RedditPublisher,
    LinkedInPublisher,
    FacebookPagePublisher,
    InstagramPublisher,
    YouTubePublisher,
]
