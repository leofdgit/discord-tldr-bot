from discord import Intents, NotFound, HTTPException
from discord.ext import commands
from openai import OpenAI
import re
import os
from math import inf
from dotenv import load_dotenv

# Require permissions int: 34359938048

LINK_PATTERN = r"https://discord\.com/channels/(?:\d+)/(\d+)/(\d+)"

# Attempts to load environment variables from the file specified by environment variable
# ENV_FILE if it is set.
if ENV_FILE := os.getenv("ENV_FILE"):
    load_dotenv(ENV_FILE)

# Read the list of authorized user IDs from an environment variable.
# The value of the AUTHORIZED_USERS env var should be a csv-delimited array
# of Discord IDs.
# E.g. 12345,67890
# which would allow the two users whose Discord IDs are 12345 and 67890 to
# use the command.
AUTHORIZED_USERS = [u for u in os.getenv("AUTHORIZED_USERS", "").split(",") if u != ""]
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES") or 100000)
MAX_MESSAGE_COMBINED_LENGTH = int(
    os.getenv("MAX_MESSAGE_COMBINED_LENGTH") or (100000 * 1000)
)
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "200"))
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-3.5-turbo")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_BOT_KEY = os.getenv("DISCORD_BOT_KEY")


# Create an instance of a bot
intents = Intents.default()
intents.message_content = True
intents.messages = True
intents.guild_messages = True
intents.guild_reactions = True
bot = commands.Bot(command_prefix="/", intents=intents)

# OpenAI Client
client = OpenAI(api_key=OPENAI_API_KEY)


# Event listener for when the bot has switched from offline to online.
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}.")


@bot.command(name="tldr")
async def tldr(ctx, message_link: str, language: str = "English"):
    try:
        await _tldr(ctx, message_link, language)
    except Exception as error:
        print(f"An error occurred: {str(error)}")
        await ctx.send("Error: unable to process request.")


async def _tldr(ctx, message_link: str, language):
    print(f"Processing tl;dr message_link={message_link} language={language}")

    # Check if the user is authorized
    if len(AUTHORIZED_USERS) > 0 and str(ctx.author.id) not in AUTHORIZED_USERS:
        await ctx.send("You do not have permission to use this command.")
        return

    # Validate and parse message_link
    match = re.match(LINK_PATTERN, message_link)
    if not match:
        await ctx.send("Invalid message link.")
        return

    channel_id, message_id = match.groups()

    # Verify if the message is in the same channel
    if str(ctx.channel.id) != channel_id:
        await ctx.send("Message link is not from this channel.")
        return

    starting_message = None
    try:
        # Fetch the starting message by ID
        starting_message = await ctx.channel.fetch_message(int(message_id))
    except NotFound:
        # If the message is not found in the channel
        await ctx.send("Message not found in this channel.")
        return
    except HTTPException:
        # If fetching the message failed due to other reasons
        await ctx.send("Failed to fetch the message.")
        return

    bot_response = await ctx.send(
        f"Processing tl;dr of chat after message <{message_link}>..."
    )

    messages = [f"{starting_message.author}: {starting_message.content}\n"]
    async for message in ctx.channel.history(limit=None, after=starting_message):
        if len(messages) > MAX_MESSAGES:
            await bot_response.edit(
                content=bot_response.content
                + "\n\nToo many messages to tl;dr! I can summarize at most {MAX_MESSAGES} messages."
            )
            return
        if message.author.bot and message.author.id == bot.application_id:
            continue
        messages.append(f"{message.author}: {message.content}\n")
    # Ignore last message, which is the /tldr command.
    messages = messages[:-1] if len(messages) > 1 else messages

    if len("".join(messages)) > MAX_MESSAGE_COMBINED_LENGTH:
        await bot_response.edit(
            content=bot_response.content
            + "\n\nToo much text to tl;dr! I can summarize at most {MAX_MESSAGE_COMBINED_LENGTH} characters."
        )
        return

    # OpenAI call to generate summary
    response = client.chat.completions.create(
        max_tokens=MAX_TOKENS,
        model=OPENAI_MODEL,
        messages=[
            {
                "role": "system",
                "content": f"""Summarize the text using bullet points. The text may not be in English.
                MENTION NAMES EXPLICITLY AND EXACTLY AS WRITTEN IN THE MESSAGES. Be succinct:
                use between 1 and 4 bullet points in your response, depending on how many distinct topics
                there are to summarize. Interpret messages starting with '/' as Discord bot commands.
                /tldr <link> means that a request to summarize all messages in the channel following the
                Discord post referenced by <link> was made (to you!).
                Quoting particularly funny, extraordinary or shocking lines from
                the messages is a bonus. Write the summary in {language}; if {language} is
                not a language you know then revert to English.""",
            },
            {
                "role": "user",
                "content": f"Summarize the following conversation:\n\n{''.join(messages)}",
            },
        ],
    )
    await bot_response.edit(
        content=bot_response.content + "\n\n" + response.choices[0].message.content
    )


bot.run(DISCORD_BOT_KEY)
