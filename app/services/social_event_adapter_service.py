"""Provider adapters that normalize signed social webhooks into the KLM aggregate."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.models.klm_operations import (
    MessageAuthorType,
    MessageDirection,
    OmnichannelChannel,
)
from app.models.omnichannel_v6 import OmnichannelChannelConnection
from app.schemas.omnichannel_v6 import CanonicalChannelEventIngest

logger = logging.getLogger(__name__)

SOCIAL_INGRESS_PROVIDERS = frozenset(
    {"meta_instagram", "meta_messenger", "tiktok_business"}
)


@dataclass(frozen=True)
class NormalizedSocialEvent:
    payload: CanonicalChannelEventIngest
    source_fragment: dict[str, Any]

    @property
    def canonical_source_bytes(self) -> bytes:
        return json.dumps(
            self.source_fragment,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")


@dataclass(frozen=True)
class SocialNormalizationBatch:
    events: tuple[NormalizedSocialEvent, ...]
    inspected_count: int
    ignored_count: int


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _first(*values: Any) -> str:
    for value in values:
        cleaned = _clean(value)
        if cleaned:
            return cleaned
    return ""


def _stable_id(prefix: str, value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(encoded).hexdigest()}"


def _occurred_at(value: Any) -> datetime | None:
    if value in {None, ""}:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(_clean(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    if numeric > 10_000_000_000:
        numeric /= 1000
    try:
        return datetime.fromtimestamp(numeric, tz=UTC)
    except (OSError, OverflowError, ValueError):
        return None


def _attachment_label(message: dict[str, Any]) -> str:
    attachments = _items(message.get("attachments"))
    if not attachments:
        return ""
    kind = _first(_mapping(attachments[0]).get("type"), "anexo")
    return f"[{kind}]"


def _meta_actor(value: dict[str, Any], external_user_id: str, channel: str) -> str:
    username = _first(value.get("username"), value.get("name"))
    if username:
        return username
    label = "Instagram" if channel == "instagram" else "Messenger"
    return f"Contato {label} {external_user_id[-6:]}"


def _profile_avatar(value: dict[str, Any]) -> str | None:
    picture = _mapping(value.get("picture"))
    picture_data = _mapping(picture.get("data"))
    return _first(
        value.get("profile_pic"),
        value.get("profile_pic_url"),
        value.get("avatar_url"),
        value.get("avatarUrl"),
        picture_data.get("url"),
        picture.get("url"),
    ) or None


class SocialEventAdapterService:
    """Normalize only documented, actionable fields and retain no provider secrets."""

    @classmethod
    def normalize(
        cls,
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
    ) -> SocialNormalizationBatch:
        if connection.provider not in SOCIAL_INGRESS_PROVIDERS:
            raise ValueError("A conexão não aceita eventos sociais oficiais.")
        if connection.provider in {"meta_instagram", "meta_messenger"}:
            return cls._normalize_meta(connection, raw_payload)
        return cls._normalize_tiktok(connection, raw_payload)

    @classmethod
    def _normalize_meta(
        cls,
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
    ) -> SocialNormalizationBatch:
        expected_object = "instagram" if connection.provider == "meta_instagram" else "page"
        object_type = _clean(raw_payload.get("object")).lower()
        if object_type and object_type != expected_object:
            raise ValueError(
                f"Objeto Meta incompatível: esperado {expected_object}, recebido {object_type}."
            )
        channel = (
            OmnichannelChannel.instagram
            if connection.provider == "meta_instagram"
            else OmnichannelChannel.messenger
        )
        normalized: list[NormalizedSocialEvent] = []
        inspected = 0
        for entry_index, entry_value in enumerate(_items(raw_payload.get("entry"))):
            entry = _mapping(entry_value)
            raw_entry_id = _clean(entry.get("id"))
            bound_account_id = _clean(connection.external_account_id)
            opaque_ref = _stable_id(
                "meta-entry", {"connectionKey": connection.connection_key, "entryIndex": entry_index}
            )
            if bound_account_id and raw_entry_id and raw_entry_id != bound_account_id:
                # Achado C3 (cross-tenant): a assinatura do webhook Meta é do app,
                # não do tenant. Um `entry.id` que diverge da conexão local pode ser
                # o payload de OUTRO tenant entregue nesta URL. Descartar a entry
                # inteira em vez de confiar no id do payload.
                discarded = len(_items(entry.get("messaging"))) + len(_items(entry.get("changes")))
                inspected += max(discarded, 1)
                logger.warning(
                    "social_event_adapter.entry_id_mismatch_discarded opaque_ref=%s "
                    "provider=%s connection_key=%s",
                    opaque_ref,
                    connection.provider,
                    connection.connection_key,
                )
                continue
            account_id = raw_entry_id or bound_account_id
            if not bound_account_id:
                # Conexão local sem `external_account_id` cadastrado ainda: aceitar
                # como hoje para não quebrar conexões já existentes, mas avisar que
                # o binding não é verificado (não há como conferir cross-tenant).
                logger.warning(
                    "social_event_adapter.unverified_account_binding opaque_ref=%s "
                    "provider=%s connection_key=%s",
                    opaque_ref,
                    connection.provider,
                    connection.connection_key,
                )
            entry_time = entry.get("time")
            for message_index, event_value in enumerate(_items(entry.get("messaging"))):
                inspected += 1
                event = _mapping(event_value)
                item = cls._meta_message(
                    connection=connection,
                    channel=channel,
                    account_id=account_id,
                    entry_time=entry_time,
                    event=event,
                    source_key=f"entry:{entry_index}:messaging:{message_index}",
                )
                if item is not None:
                    normalized.append(item)
            for change_index, change_value in enumerate(_items(entry.get("changes"))):
                inspected += 1
                change = _mapping(change_value)
                item = cls._meta_change(
                    connection=connection,
                    channel=channel,
                    account_id=account_id,
                    entry_time=entry_time,
                    change=change,
                    source_key=f"entry:{entry_index}:change:{change_index}",
                )
                if item is not None:
                    normalized.append(item)
        return SocialNormalizationBatch(
            events=tuple(normalized),
            inspected_count=inspected,
            ignored_count=max(0, inspected - len(normalized)),
        )

    @classmethod
    def _meta_message(
        cls,
        *,
        connection: OmnichannelChannelConnection,
        channel: OmnichannelChannel,
        account_id: str,
        entry_time: Any,
        event: dict[str, Any],
        source_key: str,
    ) -> NormalizedSocialEvent | None:
        message = _mapping(event.get("message"))
        postback = _mapping(event.get("postback"))
        if not message and not postback:
            return None
        sender = _mapping(event.get("sender"))
        recipient = _mapping(event.get("recipient"))
        is_echo = bool(message.get("is_echo"))
        external_user_id = _first(
            recipient.get("id") if is_echo else sender.get("id"),
            sender.get("id"),
            recipient.get("id"),
        )
        if not external_user_id:
            return None
        text = _first(
            message.get("text"),
            postback.get("title"),
            postback.get("payload"),
            _attachment_label(message),
        )
        if not text:
            return None
        reply_to = _mapping(message.get("reply_to"))
        story = _mapping(reply_to.get("story"))
        referral = _mapping(event.get("referral"))
        event_type = "direct_message"
        if story:
            event_type = "story_reply"
        elif _clean(referral.get("source")).lower() in {"story", "stories"}:
            event_type = "story_mention"
        external_event_id = _first(
            message.get("mid"),
            postback.get("mid"),
            event.get("message_id"),
            _stable_id("meta-message", {"source": source_key, "event": event}),
        )
        external_thread_id = _first(
            event.get("conversation_id"),
            f"social-participant:{external_user_id}",
        )
        public_url = _first(story.get("url"), referral.get("source_url")) or None
        inbound = not is_echo
        sender_profile = sender if inbound else recipient
        display_name = _meta_actor(sender_profile, external_user_id, channel.value)
        correlation = {
            key: value
            for key, value in {
                "adId": _first(referral.get("ad_id"), referral.get("adId")) or None,
                "postId": _first(referral.get("post_id"), referral.get("postId")) or None,
                "storyId": _first(story.get("id"), referral.get("story_id")) or None,
                "referralSource": _first(referral.get("source")) or None,
                # `ref`: código opaco propagado por deep links m.me/<page>?ref=<code>
                # e ig.me/m/<user>?ref=<code> (documentado no `referral` do webhook
                # Messenger/Instagram). Não é um click id de ads validável, mas é a
                # única evidência de origem de uma conversa iniciada por link — sem
                # ele, todo tráfego de m.me/ig.me cai fora do funil de atribuição.
                "ref": _first(referral.get("ref")) or None,
            }.items()
            if value is not None
        }
        payload = CanonicalChannelEventIngest(
            provider=connection.provider,
            connection_key=connection.connection_key,
            channel=channel,
            event_type=event_type,
            external_event_id=external_event_id,
            external_thread_id=external_thread_id,
            external_user_id=external_user_id,
            external_account_id=account_id or connection.external_account_id,
            display_name=display_name,
            profile_avatar_url=_profile_avatar(sender_profile),
            address=_first(sender_profile.get("username"), external_user_id),
            direction=MessageDirection.inbound if inbound else MessageDirection.outbound,
            author_type=MessageAuthorType.client if inbound else MessageAuthorType.human,
            author_label=display_name if inbound else "Equipe KLM",
            body=text,
            content_type=_first(message.get("type"), "text"),
            visibility="client_private",
            public_url=public_url,
            actionability="reply_required" if inbound else "informational",
            occurred_at=_occurred_at(event.get("timestamp") or entry_time),
            correlation=correlation,
            payload={
                "messageId": external_event_id,
                "attachmentCount": len(_items(message.get("attachments"))),
                "postback": bool(postback),
            },
            metadata={
                "source": "signed_social_webhook",
                "providerStatus": "received" if inbound else "sent",
                "originSurface": "customer" if inbound else "provider_echo",
                "sourceKey": source_key,
            },
        )
        return NormalizedSocialEvent(payload=payload, source_fragment=event)

    @classmethod
    def _meta_change(
        cls,
        *,
        connection: OmnichannelChannelConnection,
        channel: OmnichannelChannel,
        account_id: str,
        entry_time: Any,
        change: dict[str, Any],
        source_key: str,
    ) -> NormalizedSocialEvent | None:
        field = _clean(change.get("field")).lower()
        value = _mapping(change.get("value"))
        is_comment = field in {"comments", "live_comments"} or (
            field == "feed" and _clean(value.get("item")).lower() == "comment"
        )
        is_mention = field in {"mentions", "story_mentions"}
        if not is_comment and not is_mention:
            return None
        actor = _mapping(value.get("from") or value.get("user"))
        external_user_id = _first(
            actor.get("id"),
            actor.get("user_id"),
            value.get("from_id"),
        )
        if not external_user_id:
            return None
        verb = _first(value.get("verb"), "add").lower()
        comment_id = _first(value.get("id"), value.get("comment_id"))
        base_id = _first(
            comment_id,
            value.get("mention_id"),
            _stable_id("meta-change", {"source": source_key, "change": change}),
        )
        external_event_id = f"{base_id}:{verb}" if verb != "add" else base_id
        post_id = _first(value.get("post_id"), _mapping(value.get("media")).get("id"))
        parent_id = _first(value.get("parent_id"), value.get("parent_comment_id")) or None
        text = _first(value.get("text"), value.get("message"), "[menção]")
        inbound = external_user_id != _first(account_id, connection.external_account_id)
        display_name = _meta_actor(actor, external_user_id, channel.value)
        if is_mention:
            event_type = "story_mention"
            visibility = "client_private"
        else:
            event_type = (
                "post_comment"
                if channel == OmnichannelChannel.instagram
                else "page_comment"
            )
            visibility = "public"
        payload = CanonicalChannelEventIngest(
            provider=connection.provider,
            connection_key=connection.connection_key,
            channel=channel,
            event_type=event_type if verb == "add" else f"comment.{verb}",
            external_event_id=external_event_id,
            external_thread_id=f"social-participant:{external_user_id}",
            external_user_id=external_user_id,
            external_account_id=account_id or connection.external_account_id,
            display_name=display_name,
            profile_avatar_url=_profile_avatar(actor),
            address=_first(actor.get("username"), external_user_id),
            parent_external_event_id=parent_id,
            direction=MessageDirection.inbound if inbound else MessageDirection.outbound,
            author_type=MessageAuthorType.client if inbound else MessageAuthorType.human,
            author_label=display_name if inbound else "Equipe KLM",
            body=text,
            visibility=visibility,
            public_url=_first(value.get("permalink_url"), value.get("link")) or None,
            actionability=(
                "reply_required" if inbound and verb == "add" else "informational"
            ),
            occurred_at=_occurred_at(value.get("created_time") or entry_time),
            correlation={
                key: item
                for key, item in {
                    "commentId": comment_id or None,
                    "postId": post_id or None,
                    "sourceId": post_id or None,
                    "verb": verb,
                }.items()
                if item is not None
            },
            payload={"commentId": comment_id or None, "field": field, "verb": verb},
            metadata={
                "source": "signed_social_webhook",
                "providerStatus": "received" if inbound else "sent",
                "sourceKey": source_key,
            },
        )
        return NormalizedSocialEvent(payload=payload, source_fragment=change)

    @classmethod
    def _normalize_tiktok(
        cls,
        connection: OmnichannelChannelConnection,
        raw_payload: dict[str, Any],
    ) -> SocialNormalizationBatch:
        candidates = _items(raw_payload.get("events"))
        if not candidates:
            candidates = [raw_payload]
        normalized: list[NormalizedSocialEvent] = []
        for index, candidate_value in enumerate(candidates):
            candidate = _mapping(candidate_value)
            item = cls._tiktok_event(connection, candidate, source_key=f"event:{index}")
            if item is not None:
                normalized.append(item)
        return SocialNormalizationBatch(
            events=tuple(normalized),
            inspected_count=len(candidates),
            ignored_count=max(0, len(candidates) - len(normalized)),
        )

    @classmethod
    def _tiktok_event(
        cls,
        connection: OmnichannelChannelConnection,
        event: dict[str, Any],
        *,
        source_key: str,
    ) -> NormalizedSocialEvent | None:
        raw_content = event.get("content")
        if isinstance(raw_content, str):
            try:
                content = _mapping(json.loads(raw_content))
            except json.JSONDecodeError:
                content = {"text": raw_content}
        else:
            content = _mapping(raw_content) or _mapping(event.get("data"))
        event_name = _first(event.get("event"), event.get("event_type"), content.get("event_type"))
        normalized_name = event_name.casefold().replace("-", "_")
        comment_id = _first(content.get("comment_id"), content.get("commentId"))
        message_id = _first(
            content.get("message_id"),
            content.get("messageId"),
            event.get("message_id"),
        )
        is_comment = "comment" in normalized_name or bool(comment_id)
        is_message = any(token in normalized_name for token in ("message", "direct", "dm")) or bool(
            message_id
        )
        if not is_comment and not is_message:
            return None
        external_user_id = _first(
            event.get("user_openid"),
            content.get("sender_id"),
            content.get("open_id"),
            content.get("user_openid"),
            _mapping(content.get("user")).get("open_id"),
        )
        if not external_user_id:
            return None
        user = _mapping(content.get("user"))
        username = _first(content.get("username"), user.get("display_name"), user.get("username"))
        display_name = username or f"Contato TikTok {external_user_id[-6:]}"
        text = _first(
            content.get("text"),
            content.get("message"),
            content.get("comment_text"),
            content.get("content"),
        )
        if not text:
            text = "[comentário]" if is_comment else "[mensagem]"
        direction_value = _first(content.get("direction"), event.get("direction")).lower()
        inbound = direction_value not in {"outbound", "sent", "business"} and not bool(
            content.get("is_echo")
        )
        external_event_id = _first(
            event.get("event_id"),
            comment_id,
            message_id,
            _stable_id("tiktok-event", {"source": source_key, "event": event}),
        )
        video_id = _first(content.get("video_id"), content.get("item_id"))
        conversation_id = _first(
            content.get("conversation_id"),
            content.get("conversationId"),
            f"social-participant:{external_user_id}",
        )
        event_type = "post_comment" if is_comment else "direct_message"
        visibility = "public" if is_comment else "client_private"
        payload = CanonicalChannelEventIngest(
            provider=connection.provider,
            connection_key=connection.connection_key,
            channel=OmnichannelChannel.tiktok,
            event_type=event_type,
            external_event_id=external_event_id,
            external_thread_id=conversation_id,
            external_user_id=external_user_id,
            external_account_id=connection.external_account_id,
            display_name=display_name,
            profile_avatar_url=_profile_avatar({**user, **content}),
            address=f"@{username}" if username else external_user_id,
            canonical_phone=_first(content.get("phone"), content.get("phone_number")) or None,
            canonical_email=_first(content.get("email")) or None,
            parent_external_event_id=(
                _first(content.get("parent_comment_id"), content.get("parent_id")) or None
            ),
            direction=MessageDirection.inbound if inbound else MessageDirection.outbound,
            author_type=MessageAuthorType.client if inbound else MessageAuthorType.human,
            author_label=display_name if inbound else "Equipe KLM",
            body=text,
            content_type=_first(content.get("message_type"), "text"),
            visibility=visibility,
            public_url=_first(content.get("share_url"), content.get("video_url")) or None,
            actionability="reply_required" if inbound else "informational",
            occurred_at=_occurred_at(
                event.get("create_time") or content.get("create_time") or content.get("timestamp")
            ),
            correlation={
                key: value
                for key, value in {
                    "commentId": comment_id or None,
                    "videoId": video_id or None,
                    "postId": video_id or None,
                    "sourceId": video_id or None,
                    "adId": _first(content.get("ad_id")) or None,
                }.items()
                if value is not None
            },
            payload={
                "messageId": message_id or None,
                "commentId": comment_id or None,
                "providerEvent": event_name,
            },
            metadata={
                "source": "signed_social_webhook",
                "providerStatus": "received" if inbound else "sent",
                "sourceKey": source_key,
            },
        )
        return NormalizedSocialEvent(payload=payload, source_fragment=event)
