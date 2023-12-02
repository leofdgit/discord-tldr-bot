import asyncio
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List

from discord import (
    ChannelType,
    Client,
    HTTPException,
    Interaction,
    NotFound,
    TextChannel,
    app_commands,
    utils,
)

from src.config import DiscordClientConfig
from src.summarizer import Summarizer

LINK_PATTERN = r"https://discord\.com/channels/(\d+)/(\d+)/(\d+)"


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    id: int


@dataclass
class ChannelMessage:
    author: str
    content: str
    channel: ChannelInfo
    timestamp: datetime


def format_summary_for_discord(summary: str) -> str:
    # Discord message size is at most 2000 characters. This crude crop may remove part of the summary.
    return summary[:2000]


class DiscordClient(Client):
    def __init__(
        self, config: DiscordClientConfig, summarizer: Summarizer, *args, **kwargs
    ):
        super().__init__(*args, **kwargs)
        self.tree = app_commands.CommandTree(self)
        self.config = config
        self.summarizer = summarizer

    async def setup_hook(self) -> None:
        guild = await self.fetch_guild(self.config.guild_id)
        await guild.fetch_channels()
        if not guild:
            raise Exception(f"Failed to get guild with id {self.config.guild_id}")
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        self.bg_task = self.loop.create_task(self.my_background_task())

    async def on_ready(self):
        assert self.user is not None, f"Not logged in!"
        print(f"Logged in as {self.user} (ID: {self.user.id})")

    async def my_background_task(self):
        await self.wait_until_ready()
        while not self.is_closed():
            await self.summarise()
            await asyncio.sleep(self.config.summary_interval)

    async def summarise_messages(
        self, messages: List[ChannelMessage], output_channel: TextChannel
    ):
        msgs_in_batch: List[str] = []
        # Initialize with token allocation for the prompts and a prefix, and OPENAI_TOKEN_BUFFER.
        num_tokens_in_batch = 0
        while len(messages) > 0:
            msg = messages[0]
            formatted_msg = f"{msg.timestamp.isoformat(timespec='seconds')}:{msg.channel.name}:{msg.author}:{msg.content}\n"
            msg_tokens = len(self.summarizer.encoding.encode(formatted_msg))
            # Edge case: need to handle this somehow
            if msg_tokens > self.summarizer.max_msg_tokens:
                raise Exception(
                    f"Message too long to process: channel={msg.channel.name} num_tokens={msg_tokens}"
                )
            if msg_tokens + num_tokens_in_batch > self.summarizer.max_msg_tokens:
                summary = await self.summarizer.summarize(
                    msgs_in_batch,
                )
                await output_channel.send(format_summary_for_discord(summary))
                msgs_in_batch = []
                num_tokens_in_batch = 0
            else:
                num_tokens_in_batch += msg_tokens
                msgs_in_batch.append(formatted_msg)
                messages.pop(0)
        # Process final batch
        if msgs_in_batch:
            summary = await self.summarizer.summarize(
                msgs_in_batch,
            )
            await output_channel.send(format_summary_for_discord(summary))

    async def summarise(self):
        if not self.application_id:
            raise Exception("Not logged in!")
        guild = self.get_guild(self.config.guild_id)
        if not guild:
            raise Exception(f"Failed to get guild with id {self.config.guild_id}")
        output_channel = utils.get(
            guild.channels, id=self.config.summary_output_channel_id
        )
        if output_channel is None:
            raise Exception(f"Error: could not find channel with name {output_channel}")

        if not isinstance(output_channel, TextChannel):
            raise Exception("Output channel must be a text channel.")
        since = datetime.now() - timedelta(seconds=self.config.summary_interval)
        await output_channel.send(
            f"""
            Summarizing server activity since <t:{str(since.timestamp()).split('.')[0]}>. Any channels with fewer than {self.config.summary_msg_lower_limit} messages in this period will be ignored.
            """
        )
        channels = guild.text_channels
        for channel in channels:
            bot_member = utils.get(guild.members, id=self.application_id)
            if not bot_member:
                raise Exception("Unable to find bot Discord user.")
            permissions = channel.permissions_for(bot_member)
            if not permissions.read_messages:
                continue
            # This stops the bot summarizing previous summaries.
            if channel.id == self.config.summary_output_channel_id:
                continue
            print(f"Processing messages channel={channel.name}")
            messages = [
                ChannelMessage(
                    msg.author.name,
                    msg.content,
                    ChannelInfo(channel.name, channel.id),
                    msg.created_at,
                )
                async for msg in channel.history(after=since, limit=None)
            ]
            if len(messages) < self.config.summary_msg_lower_limit:
                continue
            await output_channel.send(f"Summary of <#{channel.id}>:\n\n")
            msgs_in_batch = []
            # Initialize with token allocation for the prompts and a prefix, and OPENAI_TOKEN_BUFFER.
            num_tokens_in_batch = 0
            while len(messages) > 0:
                msg = messages[0]
                formatted_msg = f"{msg.timestamp.isoformat(timespec='seconds')}:{msg.channel.name}:{msg.author}:{msg.content}\n"
                msg_tokens = len(self.summarizer.encoding.encode(formatted_msg))
                # Edge case: need to handle this somehow
                if msg_tokens > self.summarizer.max_msg_tokens:
                    raise Exception(
                        f"Message too long to process: channel={channel.name} num_tokens={msg_tokens}"
                    )
                if msg_tokens + num_tokens_in_batch > self.summarizer.max_msg_tokens:
                    summary = await self.summarizer.summarize(
                        msgs_in_batch,
                    )
                    await output_channel.send(format_summary_for_discord(summary))
                    msgs_in_batch = []
                    num_tokens_in_batch = 0
                else:
                    num_tokens_in_batch += msg_tokens
                    msgs_in_batch.append(formatted_msg)
                    messages.pop(0)
            # Process final batch
            if msgs_in_batch:
                summary = await self.summarizer.summarize(
                    msgs_in_batch,
                )
                await output_channel.send(format_summary_for_discord(summary))


def register_commands(client: DiscordClient) -> None:
    @app_commands.describe(
        message_link="Discord link to a message in this server. All messages in the channel that the message was sent in after and including the linked message will be summarized.",
    )
    @client.tree.command(name="tldr")
    async def tldr(interaction: Interaction, message_link: str):
        if (
            len((authd_users := client.config.authorized_user_ids)) > 0
            and str(interaction.user.id) not in authd_users
        ):
            await interaction.response.send_message(
                "You do not have permission to use this command."
            )
            return
        match = re.match(LINK_PATTERN, message_link)
        if not match:
            await interaction.response.send_message("Invalid message link.")
            return

        guild_id, channel_id, message_id = match.groups()
        guild_id = int(guild_id)
        channel_id = int(channel_id)
        message_id = int(message_id)

        # Do not allow summaries of messages from other guilds
        if guild_id != client.config.guild_id:
            await interaction.response.send_message(
                "Linked message is not from this server."
            )
            return

        guild = client.get_guild(guild_id)
        if not guild:
            await interaction.response.send_message("An error occurred.")
            print(f"Failed to get guild with id {client.config.guild_id}")
            return

        # Check that the bot has permission to write to the channel from which the interaction was sent
        bot_member = utils.get(guild.members, id=client.application_id)
        if not bot_member:
            await interaction.response.send_message("An error occurred.")
            print(f"Unable to find bot Discord user id = {client.application_id}.")
            return
        output_channel = interaction.channel
        if output_channel is None:
            await interaction.response.send_message("An error occurred.")
            print(f"Unable to find output channel.")
            return
        bot_permissions_output = output_channel.permissions_for(bot_member)
        if not bot_permissions_output.send_messages:
            await interaction.response.send_message(
                "I don't have permission to read that channel."
            )
            return

        # Check output channel is of type TextChannel
        if output_channel.type != ChannelType.text:
            await interaction.response.send_message(
                "tl;dr must be used in a text channel."
            )
            return

        # Check that the channel exists
        channel = utils.get(guild.text_channels, id=channel_id)
        if channel is None:
            await interaction.response.send_message("Could not find message channel.")
            print(f"Error: could not find channel with id {channel_id}")
            return

        # Check that everyone has permission to read the channel
        # Without this, tl;drs of hidden channels may be exposed
        everyone_role = utils.get(guild.roles, name="@everyone")
        if everyone_role is None:
            await interaction.response.send_message("An error occurred.")
            print(
                f"Unable to find @everyone Discord role guild_id = {client.config.guild_id}."
            )
            return
        everyone_permissions = channel.permissions_for(everyone_role)
        if not everyone_permissions.read_messages:
            await interaction.response.send_message("Could not find message channel.")
            return

        # Check that the bot has permission to read the channel
        bot_permissions = channel.permissions_for(bot_member)
        if not bot_permissions.read_messages:
            await interaction.response.send_message(
                "I don't have permission to read that channel."
            )
            return

        try:
            # Fetch the starting message by ID
            starting_message = await channel.fetch_message(message_id)
        except NotFound:
            # If the message is not found in the channel
            await interaction.response.send_message("Message not found.")
            return
        except HTTPException:
            # If fetching the message failed due to other reasons
            await interaction.response.send_message("Failed to fetch the message.")
            return

        messages = [
            ChannelMessage(
                starting_message.author.name,
                starting_message.content,
                ChannelInfo(channel.name, channel.id),
                starting_message.created_at,
            )
        ] + [
            ChannelMessage(
                msg.author.name,
                msg.content,
                ChannelInfo(channel.name, channel.id),
                msg.created_at,
            )
            async for msg in channel.history(after=starting_message, limit=None)
        ]
        # Ignore last message, which is the /tldr command.
        messages = messages[:-1] if len(messages) > 1 else messages
        await interaction.response.send_message(
            f"Summarizing {len(messages)} messages."
        )
        await client.summarise_messages(messages, output_channel)
