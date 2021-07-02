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

from .message import Message
from .utils import ISO8601


class ChannelType(Enum):
    chat = 'chat'
    voice = 'voice'
    forum = 'forum'
    docs = 'doc'
    thread = 'temporal'
    dm = 'DM'
    announcement = 'announcement'
    team_announcement = 'team_announcement'

class ChatChannel(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.type = ChannelType.chat

class Thread(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        data = fields.get('data') or fields.get('channel', {})  # i mean, just in case
        self.type = ChannelType.thread
        self.participants = []
        if data.get('type').lower() == 'team':
            self.team = fields.get('team') or self._state._get_team(data.get('teamId'))
            team_id = getattr(self.team, 'id', None) or data.get('teamId')
            self.parent = fields.get('parent') or self._state._get_team_channel(data.get('parentChannelId'))
            self.created_by = fields.get('created_by') or self._state._get_team_member(team_id, data.get('createdBy'))
            for user in data.get('participants'):
                _id = user.get('id')
                user = self._state._get_team_member(team_id, data.get('createdBy'))
                if user is not None: self.participants.append(user)

        else:  # realistically this should only ever be DM, but process for any non-team context instead anyway
            self.team = None
            self.parent = fields.get('parent') or self._state._get_dm_channel(data.get('parentChannelId'))
            self.created_by = fields.get('created_by') or self._state._get_user(data.get('createdBy'))
            for user in data.get('participants'):
                _id = user.get('id')
                user = self._state._get_user(data.get('createdBy'))
                if user is not None: self.participants.append(user)

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

class AnnouncementChannel(guilded.abc.Messageable):
    def __init__(self, *, state, data):
        super().__init__(state=state, data=data)
        self.type = ChannelType.announcement

class TeamAnnouncementChannel(guilded.abc.TeamChannel):
    def __init__(self, **fields):
        super().__init__(**fields)
        self.type = ChannelType.team_announcement