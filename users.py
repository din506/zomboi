from dataclasses import dataclass, field
from datetime import datetime
import discord
from discord.ext import tasks, commands
from file_read_backwards import FileReadBackwards
import glob
import os
import re
from tabulate import tabulate
from typing import List

@dataclass
class User:
    """A class representing a user"""

    name: str
    hoursAlive: int = 0
    recordHoursAlive: int = 0
    perks: dict = field(default_factory=lambda: dict())
    online: bool = False
    lastSeen: datetime = datetime(1, 1, 1)
    lastLocation: tuple = (0, 0)
    died: List[datetime] = field(default_factory=lambda: [])


class UserHandler(commands.Cog):
    """Handles all the info we get from the user log files"""

    def __init__(self, bot, logPath):
        self.bot = bot
        self.logPath = logPath
        self.lastUpdateTimestamp = datetime.now()
        self.users = {}
        self.notifyDisconnect = os.getenv("DISCONNECTS", "True") == "True"
        self.loadHistory()
        self.update.start()
        self.onlineCount = None
        return

    def getUser(self, name: str):
        """Get a user from a name, will create if it doesn't exist"""
        if not name in self.users:
            self.users[name] = User(name)
        return self.users[name]

    def splitLine(self, line: str):
        """Split a log line into a timestamp and the remaining message"""
        timestampStr, message = line.strip()[1:].split("]", 1)
        timestamp = datetime.strptime(timestampStr, "%d-%m-%y %H:%M:%S.%f")
        return timestamp, message

    @tasks.loop(seconds=2)
    async def update(self):
        """Update from the log file anything since the last update"""
        files = glob.glob(self.logPath + "/*user.txt")
        if len(files) > 0:
            with FileReadBackwards(files[0]) as f:
                newTimestamp = self.lastUpdateTimestamp
                for line in f:
                    timestamp, message = self.splitLine(line)
                    if timestamp > newTimestamp:
                        newTimestamp = timestamp
                    if timestamp > self.lastUpdateTimestamp:
                        message = self.handleLog(timestamp, message)
                        if message is not None and self.bot.channel is not None:
                            await self.bot.channel.send(message)
                    else:
                        break
                self.lastUpdateTimestamp = newTimestamp

        # Also update the bot activity here
        onlineCount = len([user for user in self.users if self.users[user].online])
        if onlineCount != self.onlineCount:
            playerString = "nobody" if onlineCount == 0 else f"{onlineCount} survivors"
            # have to abbreviate or it gets truncated
            await self.bot.change_presence(
                activity=discord.Game(f"PZ with {playerString}")
            )
            self.onlineCount = onlineCount

    def loadHistory(self):
        """Go through all log files and load the info"""
        self.bot.log.info("Loading user history...")
        files = glob.glob(self.logPath + "/**/*user.txt", recursive=True)
        files.sort(key=os.path.getmtime)
        for file in files:
            with open(file) as f:
                for line in f:
                    self.handleLog(*self.splitLine(line))
        self.bot.log.info("User history loaded")

    def handleLog(self, timestamp: datetime, message: str):
        """Parse the log message and store any useful info. Returns a message string if relevant"""

        if "disconnected" in message:
            matches = re.search(r"\"(.*)\".*\((\d+),(\d+),\d+\)", message)
            name = matches.group(1)
            user = self.getUser(name)
            if timestamp > user.lastSeen:
                user.online = False
                user.lastSeen = timestamp
                user.lastLocation = (matches.group(2), matches.group(3))
            if timestamp > self.lastUpdateTimestamp:
                self.bot.log.info(f"{user.name} disconnected")
                if self.notifyDisconnect:
                    return f":person_running: {user.name} has left"

        elif "fully connected" in message:
            matches = re.search(r"\"(.*)\".*\((\d+),(\d+)", message)
            name = matches.group(1)
            user = self.getUser(name)
            if timestamp > user.lastSeen:
                user.online = True
                user.lastSeen = timestamp
                user.lastLocation = (matches.group(2), matches.group(3))
            if timestamp > self.lastUpdateTimestamp:
                self.bot.log.info(f"{user.name} connected")
        else:
            # Ignore but mirror log if it's new
            if timestamp > self.lastUpdateTimestamp:
                self.bot.log.debug(f"Ignored: {message}")

    @commands.command()
    async def users(self, ctx):
        """Return a list of users on the server with basic info"""
        table = [["Name", "Online", "Last Seen", "Hours survived"]]
        for user in self.users.values():
            table.append(
                [
                    user.name,
                    "Yes" if user.online else "No",
                    user.lastSeen.strftime("%d/%m at %H:%M"),
                    user.hoursAlive,
                ]
            )
        await ctx.send(
            f'```\n{tabulate(table,headers="firstrow", tablefmt="fancy_grid")}\n```'
        )

    @commands.command()
    async def info(self, ctx, name=None):
        """Get detailed user info

        Provide a username, or leave blank to show the user matching your discord name
        """
        if name is None:
            name = ctx.author.name
        if name in self.users:
            user = self.users[name]
            table = []
            table.append(["Name", user.name])
            table.append(
                [
                    "Hours survived",
                    f"{user.hoursAlive} (record: {user.recordHoursAlive})",
                ]
            )
            table.append(["Online", "Yes" if user.online else "No"])
            table.append(["Last Seen", user.lastSeen.strftime("%d/%m at %H:%M")])
            table.append(["Deaths", len(user.died)])
            for perk in user.perks:
                if int(user.perks[perk]) != 0:
                    table.append([perk, user.perks[perk]])
            await ctx.send(f'```\n{tabulate(table, tablefmt="fancy_grid")}\n```')
