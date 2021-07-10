"""
MIT License

Copyright (c) 2020-present shay (shayypy)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

------------------------------------------------------------------------------

This project includes code from https://github.com/Rapptz/discord.py, which is
available under the MIT license:

The MIT License (MIT)

Copyright (c) 2015-present Rapptz

Permission is hereby granted, free of charge, to any person obtaining a
copy of this software and associated documentation files (the "Software"),
to deal in the Software without restriction, including without limitation
the rights to use, copy, modify, merge, publish, distribute, sublicense,
and/or sell copies of the Software, and to permit persons to whom the
Software is furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in
all copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS
OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
DEALINGS IN THE SOFTWARE.
"""

import aiohttp
import asyncio
import concurrent.futures
import datetime
import json
import logging
import sys
import threading
import traceback

from guilded.abc import TeamChannel

from .errors import GuildedException
from .channel import DMChannel, Thread
from .message import Message
from .presence import Presence
from .user import Member, User

log = logging.getLogger(__name__)


class WebSocketClosure(Exception):
    """An exception to make up for the fact that aiohttp doesn't signal closure."""
    pass

class GuildedWebSocket:
    """Implements Guilded's global gateway as well as team websocket connections."""
    HEARTBEAT_PAYLOAD = '2'
    def __init__(self, socket, client, *, loop):
        self.client = client
        self.loop = loop
        self._heartbeater = None

        # ws
        self.socket = socket
        self._close_code = None
        self.team_id = None

        # actual gateway garbage
        self.sid = None
        self.upgrades = []

        # I'm aware of the python-engineio package but 
        # have opted not to use it so as to have fewer 
        # dependencies, and thus fewer links in the chain.

    async def send(self, payload, *, raw=False):
        if raw is False:
            payload = f'42{json.dumps(payload)}'

        self.client.dispatch('socket_raw_send', payload)
        return await self.socket.send_str(payload)

    @property
    def latency(self):
        return float('inf') if self._heartbeater is None else self._heartbeater.latency

    @classmethod
    async def build(cls, client, *, loop=None, **gateway_args):
        log.info('Connecting to the gateway with args %s', gateway_args)
        try:
            socket = await client.http.ws_connect(**gateway_args)
        except aiohttp.client_exceptions.WSServerHandshakeError as exc:
            log.error('Failed to connect: %s', exc)
            return exc
        else:
            log.info('Connected')

        ws = cls(socket, client, loop=loop or asyncio.get_event_loop())
        ws.team_id = gateway_args.get('teamId')
        ws._parsers = WebSocketEventParsers(client)
        await ws.send(GuildedWebSocket.HEARTBEAT_PAYLOAD, raw=True)
        await ws.poll_event()

        return ws

    def _pretty_event(self, payload):
        if isinstance(payload, list):
            payload = payload[1]
        if not payload.get('type'):
            return payload

        return {
            'type': payload.pop('type'),
            'data': {k: v for k, v in payload.items()}
        }

    def _full_event_parse(self, payload):
        for char in payload:
            if char.isdigit(): payload = payload.replace(char, '', 1)
            else: break
        data = json.loads(payload)
        return self._pretty_event(data)

    async def received_event(self, payload):
        if payload.isdigit():
            return

        self.client.dispatch('socket_raw_receive', payload)
        data = self._full_event_parse(payload)
        self.client.dispatch('socket_response', data)
        log.debug('Received %s', data)

        if data.get('sid') is not None:
            # hello
            self.sid = data['sid']
            self.upgrades = data['upgrades']
            self._heartbeater = Heartbeater(ws=self, interval=data['pingInterval'] / 1000)
            # maybe implement timeout later, idk
            
            #await self.send(self.HEARTBEAT_PAYLOAD, raw=True)
            # not sure if a heartbeat should be sent here since it's sent when starting the heartbeater anyway
            # (doing so results in double heartbeat)
            self._heartbeater.start()
            return

        event = self._parsers.get(data['type'], data['data'])
        if event is None:
            # ignore unhandled events
            return
        try:
            await event
        except GuildedException as e:
            self.client.dispatch('error', e)
            raise
        except Exception as e:
            # wrap error if not already from the lib
            exc = GuildedException(e)
            self.client.dispatch('error', exc)
            raise exc from e

    async def poll_event(self):
        msg = await self.socket.receive()
        if msg.type is aiohttp.WSMsgType.TEXT:
            await self.received_event(msg.data)
        elif msg.type is aiohttp.WSMsgType.ERROR:
            raise msg.data
        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSING, aiohttp.WSMsgType.CLOSE):
            raise WebSocketClosure('Socket is in a closed or closing state.')
        return None

    async def close(self, code=1000):
        self._close_code = code
        await self.send(['logout'])
        await self.socket.close(code=code)

class WebSocketEventParsers:
    def __init__(self, client):
        self.client = client
        self._state = client.http

    def get(self, event_name, data):
        coro = getattr(self, event_name, None)
        if not coro:
            return None
        return coro(data)

    async def ChatMessageCreated(self, data):
        channelId = data.get('channelId', data.get('message', {}).get('channelId'))
        teamId = data.get('teamId', data.get('message', {}).get('teamId'))
        createdBy = data.get('createdBy', data.get('message', {}).get('createdBy'))
        channel, author, team = None, None, None

        if channelId is not None:
            try: channel = await self.client.getch_channel(channelId)
            except: channel = None

        if teamId is not None:
            if channel:
                team = channel.team
            else:
                try: team = await self.client.getch_team(teamId)
                except: team = None

        def create_faux_user():
            """Create a fake user with only `.id` and `.bot` to cut down on `message.author` being None.
            Although admittedly this isn't much more useful, it prevents the unexpected type change
            and helps use cases where only an ID is checked anyway.
            """
            return User(state=self._state, data={'id': createdBy}, bot=(data.get('webhookId') is not None or data.get('botId') is not None))

        if createdBy is not None and data.get('webhookId') is None and data.get('botId') is None:
            if channel:
                try: author = await channel.team.getch_member(createdBy)
                except:
                    try: author = await self.client.getch_user(createdBy)
                    except: author = create_faux_user()
            elif team:
                try: author = await team.getch_member(createdBy)
                except:
                    try: author = await self.client.getch_user(createdBy)
                    except: author = create_faux_user()
            else:
                try: author = await self.client.getch_user(createdBy)
                except: author = create_faux_user()

        elif createdBy is not None and (data.get('webhookId') is not None or data.get('botId') is not None):
            # in the case of webhook/flowbot messages, both webhookId/botId and createdBy are
            # returned, which i don't really know what to do with. fetching createdBy
            # as though it's a user will return a seemingly?-valid user object for 'Gil',
            # so we do it anyway (and skip member fetching, hence separate elif statement).
            try: author = await self.client.getch_user(createdBy)
            except: author = create_faux_user()
            # (for webhooks,) in the future this will probably getch the webhook (with a similar interface to
            # User) instead, since it makes more sense, but in the meantime, we'll just take
            # advantage of the createdBy attr that gets returned, even though its probably(?) the same Gil user every time

        if not channel:
            if data.get('channelType', '').lower() == 'team':
                try:
                    channel = await team.getch_channel(channelId)
                except:
                    channel = TeamChannel(state=self._state, group=None, data={'id': channelId}, team=team)
            else:
                channel = DMChannel(state=self._state, data={'id': channelId, 'users': []})

        message = self._state.create_message(channel=channel, data=data, author=author, team=team)
        self._state.add_to_message_cache(message)
        self.client.dispatch('message', message)

    async def ChatChannelTyping(self, data):
        self.client.dispatch('typing', data['channelId'], data['userId'], datetime.datetime.utcnow())

    async def ChatMessageDeleted(self, data):
        message = self.client.get_message(data['message']['id'])
        data['cached_message'] = message
        self.client.dispatch('raw_message_delete', data)
        if message is not None:
            try:
                self.client.cached_messages.remove(message)
            except:
                pass
            finally:
                self.client.dispatch('message_delete', message)

    async def ChatPinnedMessageCreated(self, data):
        if data.get('channelType') == 'Team':
            self.client.dispatch('raw_team_message_pinned', data)
        else:
            self.client.dispatch('raw_dm_message_pinned', data)
        message = self.client.get_message(data['message']['id'])
        if message is None:
            return
        
        if message.team is not None:
            self.client.dispatch('team_message_pinned', message)
        else:
            self.client.dispatch('dm_message_pinned', message)

    async def ChatPinnedMessageDeleted(self, data):
        if data.get('channelType') == 'Team':
            self.client.dispatch('raw_team_message_unpinned', data)
        else:
            self.client.dispatch('raw_dm_message_unpinned', data)
        message = self.client.get_message(data['message']['id'])
        if message is None:
            return#message = PartialMessage()

        if message.team is not None:
            self.client.dispatch('team_message_unpinned', message)
        else:
            self.client.dispatch('dm_message_unpinned', message)

    async def ChatMessageUpdated(self, data):
        self.client.dispatch('raw_message_edit', data)
        before = self.client.get_message(data['message']['id'])
        if before is None:
            return

        data['webhookId'] = before.webhook_id
        data['createdAt'] = before._raw.get('createdAt')

        after = Message(state=self.client.http, channel=before.channel, author=before.author, data=data)
        self._state.add_to_message_cache(after)
        self.client.dispatch('message_edit', before, after)

    async def TeamXpSet(self, data):
        if not data.get('amount'): return
        team = self.client.get_team(data['teamId'])
        if team is None: return
        before = team.get_member(data['userIds'][0] if data.get('userIds') else data['userId'])
        if before is None: return

        after = team.get_member(before.id)
        after.xp = data['amount']
        self._state.add_to_member_cache(after)
        self.client.dispatch('member_update', before, after)

    async def TeamMemberUpdated(self, data):
        raw_after = Member(state=self._state, data=data)
        self.client.dispatch('raw_member_update', raw_after)

        team = self.client.get_team(data['teamId'])
        if team is None: return
        if data.get('userId'):
            before = team.get_member(data.get('userId'))
        else:
            # probably includes userIds instead, which i don't plan on handling yet
            return
        if before is None:
            return

        for key, val in data['userInfo'].items():
            after = team.get_member(data['userId'])
            setattr(after, key, val)
            self._state.add_to_member_cache(after)

        self.client.dispatch('member_update', before, after)

    async def teamRolesUpdates(self, data):
        try: team = await self.client.getch_team(data['teamId'])
        except: return

        for updated in data['memberRoleIds']:
            before = team.get_member(updated['userId'])
            if not before: continue

            after = team.get_member(before.id)
            after.roles = updated['roleIds']
            self._state.add_to_member_cache(after)
            self.client.dispatch('member_update', before, after)

    async def TemporalChannelCreated(self, data):
        if data.get('channelType', '').lower() == 'team':
            try: team = await self.client.getch_team(data['teamId'])
            except: return

            thread = Thread(state=self._state, group=None, data=data.get('channel', data), team=team)
            self.client.dispatch('team_thread_created', thread)

    async def TeamMemberRemoved(self, data):
        team_id = data.get('teamId')
        user_id = data.get('userId')
        self._state.remove_from_member_cache(team_id, user_id)
        #self.client.dispatch('member_remove', user)

    async def TeamMemberJoined(self, data):
        try: team = await self.client.getch_team(data['teamId'])
        except: team = None
        member = Member(state=self._state, data=data['user'], team=team)
        self._state.add_to_member_cache(member)
        self.client.dispatch('member_join', member)

    async def USER_UPDATED(self, data):
        # transient status update handling
        # also happens in TeamMemberUpdated
        # this might just be yourself?
        pass

    async def USER_PRESENCE_MANUALLY_SET(self, data):
        status = data.get('status', 1)
        self.client.user.presence = Presence.from_value(status)
        
        #self.client.dispatch('self_presence_set', self.client.user.presence)
        # not sure if an event should be dispatched for this
        # it happens when you set your own presence

class Heartbeater(threading.Thread):
    def __init__(self, ws, *, interval):
        self.ws = ws
        self.interval = interval
        #self.heartbeat_timeout = timeout
        threading.Thread.__init__(self)

        self.msg = 'Keeping websocket alive with sequence %s.'
        self.block_msg = 'Websocket heartbeat blocked for more than %s seconds.'
        self.behind_msg = 'Can\'t keep up, websocket is %.1fs behind.'
        self._stop_ev = threading.Event()
        self.latency = float('inf')

    def run(self):
        log.debug('Started heartbeat thread')
        while not self._stop_ev.wait(self.interval):
            log.debug('Sending heartbeat')
            coro = self.ws.send(GuildedWebSocket.HEARTBEAT_PAYLOAD, raw=True)
            f = asyncio.run_coroutine_threadsafe(coro, loop=self.ws.loop)
            try:
                total = 0
                while True:
                    try:
                        f.result(10)
                        break
                    except concurrent.futures.TimeoutError:
                        total += 10
                        try:
                            frame = sys._current_frames()[self._main_thread_id]
                        except KeyError:
                            msg = self.block_msg
                        else:
                            stack = traceback.format_stack(frame)
                            msg = '%s\nLoop thread traceback (most recent call last):\n%s' % (self.block_msg, ''.join(stack))
                        log.warning(msg, total)

            except Exception:
                self.stop()

    def stop(self):
        self._stop_ev.set()
