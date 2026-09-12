import datetime as dt

from app.models.slack_channel_cache import SlackChannelCache


def refresh_channel(
    session, slack_client, channel_id: str, *, max_age_seconds: int = 3600
) -> SlackChannelCache:
    row = session.get(SlackChannelCache, channel_id)
    now = dt.datetime.now(dt.timezone.utc)
    if row is not None:
        age = (now - row.last_refreshed_at).total_seconds()
        if age < max_age_seconds:
            return row

    info = slack_client.conversations_info(channel_id)
    name = info["channel"]["name"]

    if row is None:
        row = SlackChannelCache(id=channel_id, name=name, last_refreshed_at=now)
        session.add(row)
    else:
        row.name = name
        row.last_refreshed_at = now
    session.flush()
    return row


def list_bot_channels(slack_client) -> list[dict]:
    response = slack_client.conversations_list(types="public_channel,private_channel")
    return [{"id": c["id"], "name": c["name"]} for c in response["channels"]]
