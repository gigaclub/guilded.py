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

from enum import Enum

import guilded.abc

from .embed import _EmptyEmbed, Embed
from .file import MediaType, FileType, File
from .message import Message
from .utils import ISO8601


class ChannelType(Enum):
    chat = 'chat'
    voice = 'voice'
    forum = 'forum'
    docs = 'doc'
    announcements = 'announcements'
    news = announcements
    thread = 'temporal'
    dm = 'DM'
    announcement = 'announcement'
    team_announcement = 'team_announcement'

class AnnouncementChannel(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.type = ChannelType.announcement

    async def post_announcement(self, title: str, dont_send_notifications: bool, *content, **kwargs):
        """|coro|

                Send a announcement to a Guilded channel.

                .. note::

                    Guilded supports embeds/attachments/strings in any order, which is
                    not practically possible with keyword arguments. For this reason,
                    it is recommended that you pass arguments positionally instead.

                """
        content = list(content)
        if kwargs.get('file'):
            file = kwargs.get('file')
            file.set_media_type(MediaType.attachment)
            if file.url is None:
                await file._upload(self._state)
            content.append(file)
        for file in kwargs.get('files') or []:
            file.set_media_type(MediaType.attachment)
            if file.url is None:
                await file._upload(self._state)
            content.append(file)

        def embed_attachment_uri(embed):
            # pseudo-support attachment:// URI for use in embeds
            for slot in [('image', 'url'), ('thumbnail', 'url'), ('author', 'icon_url'), ('footer', 'icon_url')]:
                url = getattr(getattr(embed, slot[0]), slot[1])
                if isinstance(url, _EmptyEmbed):
                    continue
                if url.startswith('attachment://'):
                    filename = url.strip('attachment://')
                    for node in content:
                        if isinstance(node, File) and node.filename == filename:
                            getattr(embed, f'_{slot[0]}')[slot[1]] = node.url
                            content.remove(node)
                            break

            return embed

        # upload Files passed positionally
        for node in content:
            if isinstance(node, File) and node.url is None:
                node.set_media_type(MediaType.attachment)
                await node._upload(self._state)

        # handle attachment URIs for Embeds passed positionally
        # this is a separate loop to ensure that all files are uploaded first
        for node in content:
            if isinstance(node, Embed):
                content[content.index(node)] = embed_attachment_uri(node)

        if kwargs.get('embed'):
            content.append(embed_attachment_uri(kwargs.get('embed')))

        for embed in kwargs.get('embeds') or []:
            content.append(embed_attachment_uri(embed))

        response_coro, payload = self._state.post_announcement(self._channel_id, title, content, dont_send_notifications)
        response = await response_coro
        payload = response['announcement']
        payload['createdAt'] = response.pop('announcement', response or {}).pop('createdAt', None)
        try:
            payload['channelId'] = getattr(self, 'id', getattr(self, 'channel', None).id)
        except AttributeError:
            payload['channelId'] = None
        payload['teamId'] = self.team.id if self.team else None
        payload['createdBy'] = self._state.my_id
        if payload['teamId'] is not None:
            args = (payload['teamId'], payload['createdBy'])
            try:
                author = self._state._get_team_member(*args) or await self._state.get_team_member(*args, as_object=True)
            except:
                author = None
        if author is None or payload['teamId'] is None:
            try:
                author = self._state._get_user(payload['createdBy']) or await self._state.get_user(payload['createdBy'],
                                                                                                   as_object=True)
            except:
                author = None

        return Message(state=self._state, channel=self, data=payload, author=author)

    @classmethod
    def from_str(self, string):
        return getattr(self, string, None)

class ChatChannel(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.type = ChannelType.chat

class Thread(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        data = fields.get('data') or fields.get('channel', {})  # i mean, just in case
        self.type = ChannelType.thread

        self._message_count = data.get('messageCount') or 0
        self.initial_message_id = data.get('threadMessageId')
        self._initial_message = self._state._get_message(self.initial_message_id)
        # this is unlikely to not be None given the temporal nature of message
        # cache but may as well try anyway

        self.participants = set()
        participants = data.get('participants')
        if participants is None:
            participants = [{'id': user_id} for user_id in data.get('userIds')]
        for member_obj in (participants or []):
            member = self._state._get_team_member(self.team_id, member_obj.get('id'))
            if member is None:
                # it's just an empty member with only ID, better than nothing?
                member = self._state.create_member(member_obj)

            self.participants.add(member)

    @property
    def message_count(self):
        return int(self._message_count)

    @property
    def initial_message(self):
        return self._initial_message

    async def archive(self):
        """|coro|

        Archive this thread.
        """
        request = self._state.archive_team_thread(self.team_id, self.group_id, self.id)
        await request

    async def restore(self):
        """|coro|

        Restore this thread.
        """
        request = self._state.restore_team_thread(self.team_id, self.group_id, self.id)
        await request

    async def fetch_initial_message(self):
        """|coro|

        Fetch the initial message in this channel. Sometimes this may be
        available via :attr:`Thread.initial_message`, but it is unlikely
        when dealing with existing threads because it relies on message cache.

        Roughly equivilent to:

        .. python3::
            initial_message = await thread.fetch_message(thread.initial_message_id)
        """
        data = await self._state.get_message(self.id, self.initial_message_id)
        message = self._state.create_message(data)
        return message

class DMChannel(guilded.abc.Messageable):
    def __init__(self, *, state, data):
        super().__init__(state=state, data=data)
        self.type = ChannelType.dm
        self.users = []
        self.recipient = None
        self.team = None
        for user_data in data.get('users', []):
            user = self._state._get_user(user_data.get('id'))
            if user:
                self.users.append(user)
                if user.id != self._state.my_id:
                    self.recipient = user

        self.created_at = ISO8601(data.get('createdAt'))
        self.updated_at = ISO8601(data.get('updatedAt'))
        self.deleted_at = ISO8601(data.get('deletedAt'))
        self.archived_at = ISO8601(data.get('archivedAt'))
        self.auto_archive_at = ISO8601(data.get('autoArchiveAt'))
        self.voice_participants = data.get('voiceParticipants')
        self.last_message = None
        if data.get('lastMessage'):
            message_data = data.get('lastMessage')
            author = self._state._get_user(message_data.get('createdBy'))
            message = self._state._get_message(message_data.get('id')) or Message(state=self._state, channel=self, data=message_data, author=author)
            self.last_message = message
