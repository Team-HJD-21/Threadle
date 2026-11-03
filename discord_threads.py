"""Route GitHub metadata into durable per-topic Discord threads. Standard library only."""
import hashlib
import json
import logging
import os
import re
import socket
import sys
import time
import datetime as dt
from dataclasses import dataclass, field
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    def __init__(self, service, status, code=None, retry_after=None):
        self.status, self.code = status, code
        self.retry_after = retry_after
        super().__init__(f"{service} API failed (HTTP {status}, code {code}).")


class Api:
    def __init__(self, service, base, token, scheme, sleep=time.sleep):
        self.service, self.base, self.token = service, base, token
        self.scheme, self.sleep = scheme, sleep

    def call(self, method, path, data=None):
        if isinstance(data, dict) and data.get('_files'):
            if self.service != 'Discord' or not ((method == 'POST' and re.fullmatch(r'/channels/\d+/messages', path)) or (method == 'PATCH' and re.fullmatch(r'/channels/\d+/messages/\d+', path))):
                raise ValueError('Attachments are only supported for Discord channel messages')
            from notification_media import multipart_message
            encoded, content_type = multipart_message(data)
        else:
            encoded = None if data is None else json.dumps(data).encode()
            content_type = 'application/json'
        from api_cooldowns import remaining,yield_delivery,delay as retry_delay,remember
        cooldown=remaining(self,path)
        if cooldown>0:
            if yield_delivery():raise ApiError(self.service,429,retry_after=cooldown)
            self.sleep(cooldown)
        for attempt in range(6):
            headers = {"User-Agent": "TeamHJD-Discord-Threads/1.0", "Content-Type": content_type}
            if self.token:
                headers['Authorization'] = f'{self.scheme} {self.token}'
            if self.service == "GitHub":
                headers.update({"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10" if "/issue-field-values" in path else "2022-11-28"})
            request = Request(self.base + path, data=encoded,
                              headers=headers, method=method)
            try:
                with urlopen(request, timeout=30) as response:
                    raw = response.read()
                    self.last_link = response.headers.get("Link", "")
                    result=json.loads(raw) if raw else None
                    if self.service=='Discord':
                        from delivery_metrics import discord_write
                        discord_write(method,path)
                    return result
            except HTTPError as error:
                try:
                    detail = json.loads(error.read())
                except (ValueError, UnicodeError):
                    detail = {}
                if not isinstance(detail,dict):detail={}
                explicit,wait=retry_delay(error.headers,detail,attempt)
                limited=error.code==429 or error.code==403 and (explicit is not None or error.headers.get('X-RateLimit-Remaining')=='0')
                if limited:
                    global_limit=self.service=='GitHub' or bool(detail.get('global')) or error.headers.get('X-RateLimit-Global')=='true'
                    remember(self,path,wait,global_limit)
                if yield_delivery() and (limited or error.code>=500):
                    raise ApiError(self.service,error.code,detail.get('code'),wait if limited else explicit) from None
                if attempt < 5 and (limited or error.code>=500):
                    self.sleep(max(.1,wait))
                    continue
                raise ApiError(self.service,error.code,detail.get('code'),wait if limited else explicit) from None
            except (URLError, TimeoutError):
                if yield_delivery() or attempt == 5:
                    raise ApiError(self.service, "network") from None
                self.sleep(2 ** attempt)
        raise AssertionError("Unreachable")

    def pages(self, path):
        for page in range(1, 1001):
            separator = "&" if "?" in path else "?"
            rows = self.call("GET", f"{path}{separator}per_page=100&page={page}")
            yield from rows
            if len(rows) < 100:
                return
        raise RuntimeError("GitHub pagination limit reached; refusing a partial route.")

    def create_channel(self, guild, data):
        request = Request(self.base + '/guilds/' + guild + '/channels',
                          data=json.dumps(data).encode(), method='POST', headers={
                              'Authorization': f'{self.scheme} {self.token}',
                              'User-Agent': 'TeamHJD-Discord-Threads/1.0', 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=30) as response:
                return json.loads(response.read())
        except HTTPError as error:
            try:
                detail = json.loads(error.read(16384))
                if not isinstance(detail, dict):
                    detail = {}
            except (ValueError, UnicodeError):
                detail = {}
            retry_after = error.headers.get('Retry-After', detail.get('retry_after'))
            error.close()
            raise ApiError(self.service, error.code, detail.get('code'), retry_after) from None
        except URLError as error:
            status = 'not-sent' if isinstance(error.reason, (socket.gaierror, ConnectionRefusedError)) else 'network'
            raise ApiError(self.service, status) from None
        except (TimeoutError, OSError, ValueError):
            raise ApiError(self.service, 'network') from None


@dataclass(frozen=True)
class Topic:
    repo: str
    kind: str
    number: int
    title: str
    url: str
    metadata: dict = field(default_factory=dict, compare=False)

    @property
    def key(self):
        return f"github-thread:{self.repo}:{self.kind}:{self.number}"

    @property
    def prefix(self):
        return f"[{self.kind.upper()} #{self.number}] "

    @property
    def name(self):
        from topic_state import thread_title
        return thread_title(self)


def clip(value, limit):
    value = str(value or "")
    result, used = [], 0
    for char in value:
        units = 2 if ord(char) > 0xFFFF else 1
        if used + units > limit:
            break
        result.append(char)
        used += units
    return "".join(result)


def nonce(key):
    return hashlib.sha256(key.encode()).hexdigest()[:24]


def topic_from(repo, kind, obj):
    number = int(obj["number"])
    if number <= 0 or not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
        raise ValueError("Invalid repository/topic identity.")
    path = {"issue": "issues", "pr": "pull", "discussion": "discussions"}[kind]
    return Topic(repo, kind, number, obj.get("title", ""),
                 f"https://github.com/{repo}/{path}/{number}", obj)


def timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return ''
        return f'<t:{int(parsed.timestamp())}:F>'
    except (ValueError, TypeError, OverflowError, OSError):
        return ''


def root_description(topic, english=False):
    obj = topic.metadata
    lines = []
    if topic.kind == 'pr':
        head, base = obj.get('head', {}).get('ref'), obj.get('base', {}).get('ref')
        if head and base:
            lines.append('`' + str(head).replace('`', '') + '` → `' + str(base).replace('`', '') + '`')
    return '\n'.join(lines)


def iso_timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(dt.timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def silent_review(event, payload):
    if event=='discussion' and payload.get('action') in ('pinned','unpinned'):return True
    review = payload.get('review', {})
    return (event == 'pull_request_review' and payload.get('action') == 'submitted'
            and str(review.get('state', '')).lower() == 'commented' and not str(review.get('body') or '').strip())


def route(event, payload, github):
    repo = payload["repository"]["full_name"]
    if event == "workflow_run":
        run = payload["workflow_run"]
        if run.get("event") != "pull_request" or run.get("conclusion") != "failure":
            return []
        prs = run.get("pull_requests", [])
        if not prs:
            sha = run.get("head_sha", "")
            if not re.fullmatch(r"[a-fA-F0-9]{40,64}", sha):
                raise ValueError("PR CI run has no valid commit identity.")
            prs = [pr for pr in github.pages(f"/repos/{repo}/commits/{sha}/pulls")
                   if pr.get("base", {}).get("repo", {}).get("full_name", "").lower() == repo.lower()]
        if not prs:
            raise RuntimeError("Failed PR CI run could not be linked to a PR; no unrelated channel fallback was used.")
        numbers = sorted({int(pr["number"]) for pr in prs})
        return [topic_from(repo, "pr", github.call("GET", f"/repos/{repo}/pulls/{number}")) for number in numbers]
    if event == "issues":
        return [topic_from(repo, "issue", payload["issue"])]
    if event == "issue_comment":
        obj = payload["issue"]
        return [topic_from(repo, "pr" if "pull_request" in obj else "issue", obj)]
    if event in ("pull_request_target", "pull_request", "pull_request_review", "pull_request_review_comment"):
        return [topic_from(repo, "pr", payload["pull_request"])]
    if event in ("discussion", "discussion_comment"):
        return [topic_from(repo, "discussion", payload["discussion"])]
    return []


def current_closed(topic, github):
    if topic.kind == "discussion":
        owner, name = topic.repo.split("/")
        result = github.call("POST", "/graphql", {
            "query": "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){discussion(number:$number){closed}}}",
            "variables": {"owner": owner, "name": name, "number": topic.number}})
        if result.get("errors"):
            raise RuntimeError("Could not resolve the current Discussion state.")
        discussion = (result.get("data", {}).get("repository") or {}).get("discussion")
        return True if discussion is None else discussion["closed"]
    path = "pulls" if topic.kind == "pr" else "issues"
    try:
        return github.call("GET", f"/repos/{topic.repo}/{path}/{topic.number}")["state"] == "closed"
    except ApiError as error:
        if error.status == 404:
            return True
        raise


def marker(message):
    return [str(embed.get("footer", {}).get("text", "")).split("\n", 1)[0] for embed in message.get("embeds", [])]


class Threads:
    def __init__(self, discord, guild, channels, language='ko'):
        self.api, self.guild, self.channels = discord, guild, channels
        self.language = language
        self.bot = None
        self.media_fetcher = None
        self.silent_notifications = False

    def notifications_suppressed(self):
        return bool(self.silent_notifications() if callable(self.silent_notifications) else self.silent_notifications)

    def check(self):
        self.bot = self.api.call("GET", "/users/@me")
        if not self.bot.get("bot"):
            raise ValueError("Use a Discord bot token, not a user token.")
        for kind, channel_id in self.channels.items():
            channel = self.api.call("GET", f"/channels/{channel_id}")
            if channel.get("guild_id") != self.guild or channel.get("type") not in (0, 5):
                raise ValueError(f"{kind} must be a text/announcement channel in the configured server.")
            # These reads check channel visibility/history without posting test messages.
            self.api.call("GET", f"/channels/{channel_id}/messages?limit=1")
            self.api.call("GET", f"/channels/{channel_id}/threads/archived/public?limit=2")
        self.api.call("GET", f"/guilds/{self.guild}/threads/active")

    def messages(self, channel):
        before = None
        while True:
            query = {"limit": 100}
            if before:
                query["before"] = before
            rows = self.api.call("GET", f"/channels/{channel}/messages?{urlencode(query)}")
            yield from rows
            if len(rows) < 100:
                return
            cursor = rows[-1]["id"]
            if cursor == before:
                raise RuntimeError("Discord message history did not advance.")
            before = cursor

    def matches(self, channel, thread, topic):
        name=thread.get("name", "")
        status_prefix=f"[{topic.kind.upper()} #{topic.number} · "
        if thread.get("parent_id") != channel or not (name.startswith(topic.prefix) or name.startswith(status_prefix)):
            return False
        root = self.api.call("GET", f"/channels/{channel}/messages/{thread['id']}")
        return root.get("author", {}).get("id") == self.bot["id"] and topic.key in marker(root)

    def find(self, channel, topic):
        active = self.api.call("GET", f"/guilds/{self.guild}/threads/active")
        for thread in active["threads"]:
            if self.matches(channel, thread, topic):
                return thread
        before = None
        while True:
            query = {"limit": 100}
            if before:
                query["before"] = before
            page = self.api.call("GET", f"/channels/{channel}/threads/archived/public?{urlencode(query)}")
            for thread in page["threads"]:
                if self.matches(channel, thread, topic):
                    return thread
            if not page.get("has_more"):
                break
            cursor = page["threads"][-1]["thread_metadata"]["archive_timestamp"] if page["threads"] else None
            if not cursor or cursor == before:
                raise RuntimeError("Discord archived history did not advance.")
            before = cursor
        # Recover an interrupted first delivery or a manually renamed thread using its immutable root marker.
        for root in self.messages(channel):
            if root.get("author", {}).get("id") == self.bot["id"] and topic.key in marker(root):
                return self.start(channel, root["id"], topic)
        return None

    def start(self, channel, root_id, topic):
        before=getattr(self,'before_thread_start',None)
        if callable(before) and before(channel,root_id):
            from deferred_threads import DeferredThread
            raise DeferredThread()
        data={"name":topic.name,"auto_archive_duration":1440}
        if self.notifications_suppressed():
            # Experimental for ordinary text threads; Discord may ignore this undocumented field.
            data['flags']=1<<12
            logging.getLogger('discord-threads').info('Thread create silent requested channel=%s source=%s',channel,root_id)
        try:
            try:
                return self.api.call("POST", f"/channels/{channel}/messages/{root_id}/threads",data)
            except ApiError as error:
                if 'flags' not in data or error.status!=400 or error.code not in (None,50035):raise
                logging.getLogger('discord-threads').warning('Thread create silent rejected; falling back channel=%s source=%s code=%s',channel,root_id,error.code)
                fallback={key:value for key,value in data.items() if key!='flags'}
                return self.api.call("POST", f"/channels/{channel}/messages/{root_id}/threads",fallback)
        except ApiError as error:
            if error.status != 400 or error.code != 160004:
                raise
            # Discord permits exactly one thread for a source message, including concurrent first deliveries.
            thread = self.api.call("GET", f"/channels/{root_id}")
            if thread.get("parent_id") != channel:
                raise ValueError("The existing thread belongs to another channel.")
            return thread

    def ensure(self, topic):
        channel = self.channels[topic.kind]
        existing = self.find(channel, topic)
        if existing:
            return existing
        from root_status import starter_embed
        root = self.api.call("POST", f"/channels/{channel}/messages", {
            "allowed_mentions": {"parse": []}, "nonce": nonce(channel+":"+topic.key), "enforce_nonce": True,
            **({"flags":1<<12} if self.notifications_suppressed() else {}),
            "embeds":[starter_embed(topic)]})
        return self.start(channel, root["id"], topic)

    def delivered(self, thread, receipt):
        return any(receipt in marker(message) for message in self.messages(thread))

    def remember_delivery(self, receipt, message):
        pass

    def deliver(self, topic, embed, event_key, closed):
        thread = self.ensure(topic)
        thread_id = thread["id"]
        if thread.get("thread_metadata", {}).get("locked"):
            raise RuntimeError("The topic thread is moderator-locked; refusing to create a duplicate or unlock it.")
        if thread.get("thread_metadata", {}).get("archived"):
            self.api.call("PATCH", f"/channels/{thread_id}", {"archived": False})
        from notification_layout import message_pages
        pages = message_pages(embed)
        for index, embeds in enumerate(pages):
            page_key = event_key if index == 0 else event_key + ':page:' + str(index)
            receipt = f"github-event:{nonce(topic.key + ':' + page_key)}"
            if not self.delivered(thread_id, receipt):
                embeds = [dict(item) for item in embeds]
                posted = next((item['timestamp'] for item in reversed(embeds) if item.get('timestamp')),None)
                for item in embeds:
                    item.pop('timestamp',None)
                if posted:
                    embeds[-1]['timestamp']=posted
                embeds[-1]['footer'] = {'text': receipt}
                from notification_media import attach_public_images
                files = attach_public_images(embeds, self.media_fetcher) if isinstance(self.api, Api) else []
                sent = self.api.call("POST", f"/channels/{thread_id}/messages", {
                    "embeds": embeds, "allowed_mentions": {"parse": []},
                    **({'_files': files} if files else {}),
                    "nonce": nonce(thread_id+":"+receipt), "enforce_nonce": True, **({"flags":1<<12} if self.notifications_suppressed() else {})})
                self.remember_delivery(receipt, sent["id"])
        # Read current GitHub state after sending, not an older queued close/reopen event.
        if closed():
            self.api.call("PATCH", f"/channels/{thread_id}", {"archived": True})
        print(f"Delivered {topic.kind} #{topic.number} to its Discord thread.")


def make_embed(topic, event, payload, language='ko'):
    english = language == 'en'
    from notification_layout import creation_embed, bundle_embed, field_details
    if event == 'threadle_bundle':
        return bundle_embed(topic, payload, language)
    if event in ('issues', 'pull_request', 'pull_request_target', 'discussion') and payload.get('action') in ('opened', 'created'):
        return creation_embed(topic, payload, language)
    obj = payload.get("comment") or payload.get("review") or payload.get("discussion") or payload.get("pull_request") or payload.get("issue") or {}
    action = payload.get("action", "updated")
    if event == "workflow_run":
        run = payload["workflow_run"]
        status = 'CI Failed'
        description = (f"{run.get('name', 'PR CI')} failed · {run.get('head_sha', '')[:12]}\nOpen the run to see which checks failed." if english else f"{run.get('name', 'PR CI')} 실패 · {run.get('head_sha', '')[:12]}\n실행 결과에서 실패한 검사를 확인하세요.")
        return {"title": clip(f"PR #{topic.number} · {status}", 230), "url": run["html_url"],
                "description": clip(description, 3350), "author":{"name":clip(payload.get('sender',{}).get('login','GitHub'),100)},
                **({"timestamp": iso_timestamp(run.get("updated_at") or run.get("created_at"))} if iso_timestamp(run.get("updated_at") or run.get("created_at")) else {}),
                "color": 0xD1242F}
    if payload.get("pull_request", {}).get("merged") and action == "closed":
        action = "merged"
    actor = payload.get("sender", {}).get("login", "GitHub")
    body = obj.get("body") or ""
    if event in ('issues', 'discussion', 'pull_request', 'pull_request_target') and action in ('review_requested', 'review_request_removed', 'synchronize', 'assigned', 'unassigned', 'milestoned', 'demilestoned', 'labeled', 'unlabeled', 'converted_to_draft', 'typed', 'untyped', 'field_added', 'field_removed', 'closed', 'merged', 'reopened', 'deleted', 'answered', 'unanswered', 'locked', 'unlocked'):
        body = ''
    if event in ('pull_request', 'pull_request_target') and action == 'synchronize':
        from commit_notifications import render_commits
        body = render_commits(topic.repo,payload,english)
    if event == "pull_request_review" and str(obj.get("state", "")).lower() != "commented":
        body = ('Review: ' if english else '리뷰 결과: ') + obj.get('state', 'unknown') + '\n\n' + body
    if obj.get("path"):
        body = f"`{clip(obj['path'], 220)}:{obj.get('line') or obj.get('original_line') or ''}`\n\n" + body
    extras = field_details(payload, english) if event == "issues" else []
    for key in ("label", "assignee", "requested_reviewer", "milestone"):
        if payload.get(key):
            item = payload[key]
            labels = {'label': ('Label','라벨'), 'assignee': ('Assignee','담당자'), 'requested_reviewer': ('Reviewer','리뷰어'), 'milestone': ('Milestone','마일스톤')}
            extras.append(labels[key][0] + ': ' + str(item.get('name') or item.get('login') or item.get('title', '')))
    if payload.get('requested_team'):
        extras.append('requested_team: ' + str(payload['requested_team'].get('slug', '')))
    if extras:
        body = "\n".join(extras) + ("\n\n" + body if body.strip() else "")
    posted = iso_timestamp(obj.get('submitted_at') or obj.get('updated_at') or obj.get('created_at'))
    if english:
        from alert_feed import english_activity
        label = english_activity(event, payload)
    else:
        from alert_feed import activity
        label = activity(event, payload)
    from topic_state import event_color
    return {"title": clip(f"{topic.kind.upper()} #{topic.number} · {label}", 230),
            "url": obj.get("html_url") or topic.url,
            "description": body,
            "author": {"name": clip(actor,100)},
            "color": event_color(topic.kind,event,payload),
            **({"timestamp": posted} if posted else {})}


def main():
    config = json.loads((Path(__file__).parents[1] / "discord-thread-channels.json").read_text())
    token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not token:
        raise ValueError("Register DISCORD_BOT_TOKEN as an Actions repository secret first.")
    discord = Api("Discord", "https://discord.com/api/v10", token, "Bot")
    threads = Threads(discord, config["guild_id"], config["channels"], config.get('language', 'ko'))
    threads.check()
    if os.environ.get("DISCORD_CHECK_ONLY") == "true":
        print("Bot identity and configured channel history are accessible; no messages sent. Test write permissions during cutover.")
        return
    event = os.environ["GITHUB_EVENT_NAME"]
    if event == "workflow_dispatch":
        repo = os.environ["GITHUB_REPOSITORY"]
        for kind in ("issue", "pr", "discussion"):
            topic = Topic(repo, kind, 0, "[TEST] 알림 스레드 연결 확인", f"https://github.com/{repo}")
            threads.deliver(topic, {"title": "[TEST] Discord 알림 연결 확인", "description": "실제 GitHub 이슈·PR·Discussion을 만들지 않는 연결 테스트입니다."},
                            os.environ["GITHUB_RUN_ID"], lambda: True)
        return
    payload = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_text())
    if payload.get("repository", {}).get("full_name") != os.environ["GITHUB_REPOSITORY"]:
        raise ValueError("Event repository mismatch.")
    github = Api("GitHub", "https://api.github.com", os.environ["GITHUB_TOKEN"], "Bearer")
    topics = route(event, payload, github)
    if not topics:
        print("No associated PR/topic found; no unrelated channel fallback was used.")
        return
    # run_id is stable across a GitHub Actions rerun; each topic has its own persistent receipt.
    event_key = os.environ["GITHUB_RUN_ID"]
    for topic in topics:
        threads.deliver(topic, make_embed(topic, event, payload, config.get('language', 'ko')), event_key,
                        lambda topic=topic: current_closed(topic, github))


if __name__ == "__main__":
    try:
        main()
    except (ApiError, ValueError, RuntimeError, KeyError) as error:
        print(f"Notification failed: {error}", file=sys.stderr)
        sys.exit(1)
