import discord
from discord import app_commands
from discord.ext import commands
import os
import asyncio
import logging
import sys
from datetime import datetime, timedelta
import traceback
from typing import List, Optional, Union, Dict, Any
import aiohttp
import json
import firebase_admin
from firebase_admin import credentials, firestore, initialize_app

# Add this at the VERY TOP of bot.py, right after all the imports
from http.server import BaseHTTPRequestHandler
import json

# This is what Vercel is looking for
class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        response = {"status": "online", "message": "Bot is running"}
        self.wfile.write(json.dumps(response).encode())
    
    def do_POST(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")
        
# --- DEBUG CONFIGURATION ---
DEBUG_MODE = True
LOG_TO_FILE = True
LOG_TO_CONSOLE = True

# Setup logging
class DebugLogger:
    def __init__(self):
        self.logger = logging.getLogger('TicketBot')
        self.logger.setLevel(logging.DEBUG if DEBUG_MODE else logging.INFO)
        
        if LOG_TO_CONSOLE:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(logging.DEBUG)
            console_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
            console_handler.setFormatter(console_format)
            self.logger.addHandler(console_handler)
        
        if LOG_TO_FILE:
            file_handler = logging.FileHandler(f'ticket_bot_debug_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log')
            file_handler.setLevel(logging.DEBUG)
            file_format = logging.Formatter('%(asctime)s [%(levelname)s] %(message)s')
            file_handler.setFormatter(file_format)
            self.logger.addHandler(file_handler)
    
    def debug(self, message, extra=None):
        if extra:
            self.logger.debug(f"{message} | Extra: {extra}")
        else:
            self.logger.debug(message)
    
    def info(self, message):
        self.logger.info(message)
    
    def warning(self, message):
        self.logger.warning(message)
    
    def error(self, message, exc_info=False):
        self.logger.error(message, exc_info=exc_info)
    
    def critical(self, message):
        self.logger.critical(message)

logger = DebugLogger()

# --- FIREBASE INITIALIZATION ---
# You'll need to download your Firebase service account key JSON file
# and add it to your environment variables or as a file
firebase_initialized = False
db = None

def init_firebase():
    global firebase_initialized, db
    try:
        # Try to get credentials from environment variable
        firebase_creds = os.getenv('FIREBASE_CREDENTIALS')
        
        if firebase_creds:
            # Parse from environment variable
            creds_dict = json.loads(firebase_creds)
            cred = credentials.Certificate(creds_dict)
        elif os.path.exists('firebase-credentials.json'):
            # Load from file
            cred = credentials.Certificate('firebase-credentials.json')
        else:
            logger.warning("No Firebase credentials found. Using mock database for testing.")
            # For testing without Firebase, we'll use an in-memory dict
            # In production, you should set up Firebase properly
            class MockDB:
                def __init__(self):
                    self.data = {}
                
                def collection(self, name):
                    return MockCollection(self.data, name)
            
            class MockCollection:
                def __init__(self, data, name):
                    self.data = data
                    self.name = name
                    if name not in data:
                        data[name] = {}
                
                def document(self, doc_id):
                    return MockDocument(self.data[self.name], doc_id)
                
                def where(self, field, op, value):
                    return MockQuery(self.data[self.name], field, op, value)
            
            class MockDocument:
                def __init__(self, collection, doc_id):
                    self.collection = collection
                    self.doc_id = doc_id
                
                def set(self, data):
                    self.collection[self.doc_id] = data
                    return self
                
                def get(self):
                    class MockSnapshot:
                        def __init__(self, data):
                            self._data = data
                        
                        def exists(self):
                            return self._data is not None
                        
                        def to_dict(self):
                            return self._data
                    
                    return MockSnapshot(self.collection.get(self.doc_id))
                
                def update(self, data):
                    if self.doc_id in self.collection:
                        self.collection[self.doc_id].update(data)
                    else:
                        self.collection[self.doc_id] = data
                    return self
                
                def delete(self):
                    if self.doc_id in self.collection:
                        del self.collection[self.doc_id]
                    return self
            
            class MockQuery:
                def __init__(self, collection, field, op, value):
                    self.collection = collection
                    self.field = field
                    self.op = op
                    self.value = value
                
                def get(self):
                    results = []
                    for doc_id, data in self.collection.items():
                        if self.op == "==" and data.get(self.field) == self.value:
                            results.append(MockQuerySnapshot(doc_id, data))
                        elif self.op == "array-contains" and self.value in data.get(self.field, []):
                            results.append(MockQuerySnapshot(doc_id, data))
                    return results
            
            class MockQuerySnapshot:
                def __init__(self, doc_id, data):
                    self.id = doc_id
                    self._data = data
                
                def exists(self):
                    return True
                
                def to_dict(self):
                    return self._data
            
            db = MockDB()
            firebase_initialized = True
            logger.info("Using mock database (no Firebase connection)")
            return
        
        # Initialize Firebase
        if not firebase_admin._apps:
            initialize_app(cred)
        
        db = firestore.client()
        firebase_initialized = True
        logger.info("Firebase initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Firebase: {e}")
        firebase_initialized = False

init_firebase()

# --- DEFAULT CONFIGURATION ---
DEFAULT_GUILD_CONFIG = {
    "ticket_category_id": None,
    "transcript_channel_id": None,
    "rating_channel_id": None,
    "status_control_role_id": None,
    "admin_role_id": None,
    "staff_role_id": None,
    "moderation_role_id": None,
    "marketing_role_id": None,
    "development_role_id": None,
    "special_role_1_id": None,
    "special_role_2_id": None,
    "panel_messages": {},  # Store panel message IDs for editing
    "ticket_categories": [],  # Custom categories per server
    "auto_close_days": 7,
    "max_tickets_per_user": 3,
    "enable_transcripts": True,
    "enable_ratings": True,
    "enable_auto_mention": True
}

# --- STORE CONFIGS IN MEMORY (cached) ---
guild_configs = {}
active_tickets = {}
ticket_timers = {}

# --- GUILD CONFIGURATION MANAGER ---
class GuildConfigManager:
    @staticmethod
    async def get_config(guild_id: int) -> dict:
        """Get guild configuration from Firebase"""
        if guild_id in guild_configs:
            return guild_configs[guild_id]
        
        try:
            if db:
                doc_ref = db.collection('guild_configs').document(str(guild_id))
                doc = doc_ref.get()
                if doc.exists:
                    config = doc.to_dict()
                    guild_configs[guild_id] = config
                    return config
        except Exception as e:
            logger.error(f"Error getting config for guild {guild_id}: {e}")
        
        # Return default config
        config = DEFAULT_GUILD_CONFIG.copy()
        guild_configs[guild_id] = config
        return config
    
    @staticmethod
    async def update_config(guild_id: int, updates: dict):
        """Update guild configuration"""
        try:
            config = await GuildConfigManager.get_config(guild_id)
            config.update(updates)
            
            if db:
                doc_ref = db.collection('guild_configs').document(str(guild_id))
                doc_ref.set(config, merge=True)
            
            guild_configs[guild_id] = config
            logger.info(f"Updated config for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error updating config for guild {guild_id}: {e}")
            return False
    
    @staticmethod
    async def delete_config(guild_id: int):
        """Delete guild configuration"""
        try:
            if guild_id in guild_configs:
                del guild_configs[guild_id]
            
            if db:
                doc_ref = db.collection('guild_configs').document(str(guild_id))
                doc_ref.delete()
            
            logger.info(f"Deleted config for guild {guild_id}")
            return True
        except Exception as e:
            logger.error(f"Error deleting config for guild {guild_id}: {e}")
            return False

# --- STATUS CONFIGURATION (Per Guild) ---
class TicketStatus:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        self.ticket_count_mode = "auto"
        self.service_mode = "auto"
        self.manual_ticket_status = "green"
        self.manual_service_status = "green"
        self.last_updated = datetime.now()
        self.total_tickets_created = 0
        self.tickets_closed_today = 0
        self.last_reset = datetime.now().date()
    
    def get_ticket_count_status(self, current_tickets):
        if self.ticket_count_mode == "manual":
            return self.manual_ticket_status
        
        if current_tickets < 10:
            return "green"
        elif current_tickets < 20:
            return "yellow"
        elif current_tickets >= 20:
            return "red"
        return "green"
    
    def get_service_status(self, current_tickets):
        if self.service_mode == "manual":
            return self.manual_service_status
        
        if current_tickets < 10:
            return "green"
        elif current_tickets < 15:
            return "yellow"
        elif current_tickets >= 15:
            return "red"
        return "green"
    
    def get_status_emoji(self, status):
        status_emojis = {
            "green": "🟢",
            "yellow": "🟡", 
            "red": "🔴",
            "black": "⚫"
        }
        return status_emojis.get(status, "⚪")
    
    def get_status_text(self, status, status_type):
        if status_type == "ticket":
            texts = {
                "green": "Low Load - Quick responses expected",
                "yellow": "Moderate Load - Slight delays possible",
                "red": "High Load - Extended wait times",
                "black": "Tickets Paused - No new tickets"
            }
        else:
            texts = {
                "green": "Normal Service - No delays",
                "yellow": "Minor Delays - Slightly slower responses",
                "red": "Major Delays - Response may take days",
                "black": "Service Shutdown - No responses"
            }
        return texts.get(status, "Status Unknown")
    
    def can_create_tickets(self):
        if self.ticket_count_mode == "manual" and self.manual_ticket_status == "black":
            return False
        if self.service_mode == "manual" and self.manual_service_status == "black":
            return False
        return True

# Store status per guild
guild_statuses = {}

def get_guild_status(guild_id: int) -> TicketStatus:
    """Get or create status for a guild"""
    if guild_id not in guild_statuses:
        guild_statuses[guild_id] = TicketStatus(guild_id)
    return guild_statuses[guild_id]

# --- TICKET BOT ---
class TicketBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.start_time = datetime.now()
    
    async def setup_hook(self):
        logger.info("Starting bot setup...")
        try:
            # Sync commands globally (they'll work in all servers)
            await self.tree.sync()
            logger.info("Successfully synced commands globally")
            
            # Add persistent views
            self.add_view(TicketControls())
            self.add_view(TicketDropdownView())
            self.add_view(ConfirmationView())
            
        except Exception as e:
            logger.error(f"Failed to sync commands: {e}", exc_info=True)
    
    async def on_ready(self):
        logger.info(f"✅ Bot is online as {self.user.name} (ID: {self.user.id})")
        logger.info(f"Connected to {len(self.guilds)} guilds")
        
        # Load configurations for all guilds
        for guild in self.guilds:
            await GuildConfigManager.get_config(guild.id)
        
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="🎫 for tickets | /setup"
            )
        )
    
    async def on_guild_join(self, guild):
        """Handle bot joining a new server"""
        logger.info(f"Bot joined new guild: {guild.name} (ID: {guild.id})")
        await GuildConfigManager.get_config(guild.id)
        
        # Send welcome message to system channel or first available channel
        if guild.system_channel:
            embed = discord.Embed(
                title="🎫 Thanks for adding me!",
                description=(
                    "I'm a ticket management bot. To get started:\n\n"
                    "1. Use `/settings` to configure roles and channels\n"
                    "2. Use `/setup` to create the ticket panel\n"
                    "3. Use `/help` to see all commands\n\n"
                    "**Need help?** Visit our support server!"
                ),
                color=0x5865f2
            )
            await guild.system_channel.send(embed=embed)

bot = TicketBot()

# --- RATING SYSTEM ---
class RatingView(discord.ui.View):
    def __init__(self, ticket_data):
        super().__init__(timeout=604800)
        self.ticket_data = ticket_data
        self.add_item(RatingSelect(ticket_data))

class RatingSelect(discord.ui.Select):
    def __init__(self, ticket_data):
        super().__init__(placeholder="⭐ Select your rating...", min_values=1, max_values=1)
        self.ticket_data = ticket_data
        for i in range(5, 0, -1):
            stars = "⭐" * i
            labels = ['Poor', 'Fair', 'Good', 'Very Good', 'Excellent']
            self.add_option(label=f"{stars} - {labels[i-1]}", value=str(i), description=f"{i}/5 Stars", emoji="⭐")
    
    async def callback(self, interaction: discord.Interaction):
        rating = int(self.values[0])
        await submit_rating(interaction, rating, self.ticket_data)

async def submit_rating(interaction: discord.Interaction, rating: int, ticket_data: dict):
    """Submit and log the rating"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    
    stars = "⭐" * rating + "☆" * (5 - rating)
    
    embed = discord.Embed(
        title="📊 New Ticket Rating Received",
        color=0xf1c40f,
        timestamp=datetime.now()
    )
    
    embed.add_field(name="🎫 Ticket", value=f"#{ticket_data['channel_name']}", inline=True)
    embed.add_field(name="📋 Category", value=ticket_data['category'], inline=True)
    embed.add_field(name="⭐ Rating", value=f"{stars} ({rating}/5)", inline=True)
    embed.add_field(name="👤 User", value=interaction.user.mention, inline=True)
    embed.add_field(name="🆔 User ID", value=f"`{interaction.user.id}`", inline=True)
    
    embed.set_footer(text=f"Rated by {interaction.user.name}")
    
    if config.get('rating_channel_id'):
        rating_channel = interaction.guild.get_channel(config['rating_channel_id'])
        if rating_channel:
            await rating_channel.send(embed=embed)
            logger.info(f"Rating sent to #{rating_channel.name}")
    
    thank_you = discord.Embed(
        title="✅ Thank You for Your Feedback!",
        description=f"Your {stars} rating has been recorded. We appreciate your input!",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=thank_you, ephemeral=True)
    
    logger.info(f"Rating received from {interaction.user.name}: {rating}/5")

# --- AUTO-MENTION SYSTEM ---
async def start_auto_mention_timer(channel, category_data, user_id, guild_id):
    """Start a timer to auto-mention staff if no response"""
    config = await GuildConfigManager.get_config(guild_id)
    
    if not config.get('enable_auto_mention', True):
        return
    
    if not category_data.get('auto_mention', False):
        return
    
    excluded_categories = ["careers", "management"]
    if category_data['value'] in excluded_categories:
        return
    
    async def auto_mention_cycle():
        mention_count = 0
        max_mentions = 1
        
        while mention_count < max_mentions:
            try:
                current_tickets = len([c for c in channel.guild.channels if c.category_id == config.get('ticket_category_id')])
                guild_status = get_guild_status(guild_id)
                service_status = guild_status.get_service_status(current_tickets)
                
                if service_status == "red":
                    delay = 48 * 60 * 60
                    hours_text = "48 hours"
                else:
                    delay = 24 * 60 * 60
                    hours_text = "24 hours"
                
                await asyncio.sleep(delay)
                
                if channel.id not in active_tickets:
                    return
                
                ticket_data = active_tickets.get(channel.id, {})
                if ticket_data.get('claimed_by'):
                    return
                
                staff_responded = False
                try:
                    async for message in channel.history(limit=100):
                        if message.author.bot:
                            continue
                        if isinstance(message.author, discord.Member):
                            view_roles = category_data.get('view_roles', [])
                            if any(message.author.get_role(role_id) for role_id in view_roles if role_id):
                                staff_responded = True
                                break
                except discord.Forbidden:
                    staff_responded = True
                
                if staff_responded:
                    return
                
                ping_mentions = []
                for role_id in category_data.get('ping_roles', []):
                    role = channel.guild.get_role(role_id)
                    if role:
                        ping_mentions.append(role.mention)
                
                if ping_mentions:
                    embed = discord.Embed(
                        title="⏰ No Response Detected",
                        description=f"This ticket has been waiting for a response for {hours_text}.",
                        color=0xff9900,
                        timestamp=datetime.now()
                    )
                    
                    status_emoji = guild_status.get_status_emoji(service_status)
                    embed.add_field(
                        name="📊 Current Status",
                        value=f"{status_emoji} {service_status.upper()} - {guild_status.get_status_text(service_status, 'service')}",
                        inline=False
                    )
                    
                    await channel.send(content=" ".join(ping_mentions), embed=embed)
                
                mention_count += 1
                
            except asyncio.CancelledError:
                return
            except Exception as e:
                logger.error(f"Error in auto-mention cycle: {e}")
                return
    
    task = asyncio.create_task(auto_mention_cycle())
    ticket_timers[channel.id] = task

# --- CONFIRMATION VIEW ---
class ConfirmationView(discord.ui.View):
    def __init__(self, action: str, target_data: dict, callback_func):
        super().__init__(timeout=60)
        self.action = action
        self.target_data = target_data
        self.callback_func = callback_func
    
    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.callback_func(interaction, self.target_data, confirmed=True)
        self.stop()
    
    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.callback_func(interaction, self.target_data, confirmed=False)
        self.stop()

# --- DROPDOWN MENU FOR TICKET CATEGORIES ---
class TicketDropdown(discord.ui.Select):
    def __init__(self, categories):
        self.categories = categories
        options = []
        for category in categories:
            options.append(
                discord.SelectOption(
                    label=category["name"],
                    value=category["value"],
                    description=category["description"][:100],
                    emoji=category["emoji"]
                )
            )
        
        super().__init__(
            placeholder="🎫 Select a ticket category...",
            min_values=1,
            max_values=1,
            options=options,
            custom_id="ticket_category_select"
        )
    
    async def callback(self, interaction: discord.Interaction):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        guild_status = get_guild_status(interaction.guild_id)
        
        if not guild_status.can_create_tickets():
            embed = discord.Embed(
                title="⛔ Tickets Currently Paused",
                description="Ticket creation is currently disabled. Please try again later.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        selected_value = self.values[0]
        selected_category = next(
            (cat for cat in self.categories if cat["value"] == selected_value),
            None
        )
        
        if selected_category:
            await create_ticket(interaction, selected_category, config)

class TicketDropdownView(discord.ui.View):
    def __init__(self, categories):
        super().__init__(timeout=None)
        self.add_item(TicketDropdown(categories))

async def update_status_embeds(guild, config):
    """Update all status embeds when status changes"""
    if guild.id not in config.get('panel_messages', {}):
        return
    
    for channel_id, message_id in config.get('panel_messages', {}).items():
        try:
            channel = guild.get_channel(channel_id)
            if not channel:
                continue
            
            message = await channel.fetch_message(message_id)
            if not message:
                continue
            
            current_tickets = len([c for c in guild.channels if c.category_id == config.get('ticket_category_id')])
            guild_status = get_guild_status(guild.id)
            ticket_count_status = guild_status.get_ticket_count_status(current_tickets)
            service_status = guild_status.get_service_status(current_tickets)
            
            embeds = await create_status_embeds(guild, config, current_tickets, ticket_count_status, service_status)
            
            await message.edit(embeds=embeds)
            logger.info(f"Updated status embeds in #{channel.name}")
            
        except Exception as e:
            logger.error(f"Failed to update status embed: {e}")

async def create_status_embeds(guild, config, current_tickets, ticket_count_status, service_status):
    """Create the 4 status embeds"""
    categories = config.get('ticket_categories', [])
    guild_status = get_guild_status(guild.id)
    
    embed1 = discord.Embed(
        title=f"🏠 Welcome to **{guild.name}** Support Center",
        description="Hello and welcome to our official support system!\n\nWe're here to help you with any questions or issues you might have.",
        color=0x5865f2,
        timestamp=datetime.now()
    )
    if guild.icon:
        embed1.set_thumbnail(url=guild.icon.url)
    embed1.set_footer(text="Your satisfaction is our priority")
    
    categories_text = ""
    for cat in categories:
        categories_text += f"{cat['emoji']} **{cat['name']}**\n└ {cat['description']}\n"
        if cat.get('warning'):
            categories_text += f"└ *{cat['warning']}*\n"
        categories_text += "\n"
    
    embed2 = discord.Embed(
        title="📋 **Available Ticket Categories**",
        description=categories_text[:4096] or "No categories configured. Please ask an admin to set up categories using `/settings`.",
        color=0x3498db,
        timestamp=datetime.now()
    )
    
    embed3 = discord.Embed(
        title="📌 **Support Guidelines**",
        description=(
            "**✅ DO's:**\n"
            "• Be clear and detailed about your issue\n"
            "• Provide relevant screenshots if needed\n"
            "• Wait patiently for staff response\n"
            "• Rate your experience when ticket is closed\n\n"
            "**❌ DON'Ts:**\n"
            "• Create multiple tickets for the same issue\n"
            "• Be rude or disrespectful to staff\n"
            "• Share personal information publicly"
        ),
        color=0x2ecc71,
        timestamp=datetime.now()
    )
    
    ticket_emoji = guild_status.get_status_emoji(ticket_count_status)
    service_emoji = guild_status.get_status_emoji(service_status)
    
    status_text = (
        f"**📊 Ticket Load Status:** {ticket_emoji}\n"
        f"└ {guild_status.get_status_text(ticket_count_status, 'ticket')}\n"
        f"└ Current Tickets: **{current_tickets}**\n\n"
        f"**⏱️ Service Status:** {service_emoji}\n"
        f"└ {guild_status.get_status_text(service_status, 'service')}\n\n"
    )
    
    if guild_status.ticket_count_mode == "manual":
        status_text += "*🎛️ Ticket status set manually*\n"
    if guild_status.service_mode == "manual":
        status_text += "*🎛️ Service status set manually*\n"
    
    embed4 = discord.Embed(
        title="📊 **Current Support Status**",
        description=status_text,
        color=0xf1c40f,
        timestamp=datetime.now()
    )
    
    return [embed1, embed2, embed3, embed4]

def get_account_age(created_at):
    """Calculate account age in a readable format"""
    now = datetime.now(created_at.tzinfo) if created_at.tzinfo else datetime.now()
    delta = now - created_at
    
    years = delta.days // 365
    months = (delta.days % 365) // 30
    days = delta.days % 30
    
    parts = []
    if years > 0:
        parts.append(f"{years} year{'s' if years > 1 else ''}")
    if months > 0:
        parts.append(f"{months} month{'s' if months > 1 else ''}")
    if days > 0 and years == 0:
        parts.append(f"{days} day{'s' if days > 1 else ''}")
    
    if not parts:
        return "Less than a day"
    
    return ", ".join(parts[:2])

def get_user_badges(member):
    """Get user badges/roles info"""
    badges = []
    
    if member.premium_since:
        badges.append("✨ Server Booster")
    
    if member.bot:
        badges.append("🤖 Bot")
    
    if len(member.roles) > 1:
        top_roles = [role.mention for role in member.roles[1:4]]
        if top_roles:
            badges.append(f"📋 Roles: {', '.join(top_roles)}")
    
    return badges

# --- BUTTONS UI FOR TICKET CONTROL ---
class TicketControls(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="📋 Claim", style=discord.ButtonStyle.secondary, custom_id="claim_ticket")
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            ticket_data = active_tickets.get(interaction.channel.id, {})
            
            view_roles = ticket_data.get('view_roles', [])
            has_access = any(
                interaction.guild.get_role(role_id) in interaction.user.roles 
                for role_id in view_roles if role_id
            )
            
            if not has_access:
                return await interaction.response.send_message("❌ You don't have permission to claim this ticket!", ephemeral=True)
            
            if ticket_data.get('claimed_by'):
                claimer = interaction.guild.get_member(ticket_data['claimed_by'])
                return await interaction.response.send_message(f"⚠️ This ticket is already claimed by {claimer.mention if claimer else 'someone'}!", ephemeral=True)
            
            await interaction.channel.edit(name=f"📋-claimed-{interaction.user.name}")
            
            active_tickets[interaction.channel.id]['claimed_by'] = interaction.user.id
            active_tickets[interaction.channel.id]['claimed_at'] = datetime.now()
            
            if interaction.channel.id in ticket_timers:
                ticket_timers[interaction.channel.id].cancel()
                del ticket_timers[interaction.channel.id]
            
            embed = discord.Embed(
                description=f"✅ **Ticket Claimed**\nThis ticket has been claimed by {interaction.user.mention}",
                color=discord.Color.green(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in claim: {e}", exc_info=True)
            await interaction.response.send_message("❌ Failed to claim ticket.", ephemeral=True)

    @discord.ui.button(label="⬆️ Escalate", style=discord.ButtonStyle.primary, custom_id="escalate_ticket")
    async def escalate(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            config = await GuildConfigManager.get_config(interaction.guild_id)
            ticket_data = active_tickets.get(interaction.channel.id, {})
            view_roles = ticket_data.get('view_roles', [])
            
            has_access = any(
                interaction.guild.get_role(role_id) in interaction.user.roles 
                for role_id in view_roles if role_id
            )
            
            if not has_access:
                return await interaction.response.send_message("❌ Staff only!", ephemeral=True)

            admin_role = interaction.guild.get_role(config.get('admin_role_id'))
            if admin_role:
                await interaction.channel.set_permissions(admin_role, read_messages=True, send_messages=True, read_message_history=True)
            
            embed = discord.Embed(
                description="⬆️ **Ticket Escalated**\nThis ticket has been escalated to **Higher Management**.",
                color=discord.Color.orange(),
                timestamp=datetime.now()
            )
            
            await interaction.response.send_message(embed=embed)
            if admin_role:
                await interaction.channel.send(f"{admin_role.mention} A ticket has been escalated!")
            
        except Exception as e:
            logger.error(f"Error in escalate: {e}", exc_info=True)
            await interaction.response.send_message("❌ Failed to escalate ticket.", ephemeral=True)

    @discord.ui.button(label="🔒 Close", style=discord.ButtonStyle.danger, custom_id="close_ticket")
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        ticket_data = active_tickets.get(interaction.channel.id, {})
        is_owner = ticket_data.get('user_id') == interaction.user.id
        view_roles = ticket_data.get('view_roles', [])
        
        has_staff_access = any(
            interaction.guild.get_role(role_id) in interaction.user.roles 
            for role_id in view_roles if role_id
        )
        
        if not (is_owner or has_staff_access):
            return await interaction.response.send_message("❌ You don't have permission to close this ticket!", ephemeral=True)
        
        embed = discord.Embed(
            title="⚠️ Confirm Ticket Closure",
            description="Are you sure you want to close this ticket? This action cannot be undone.",
            color=0xff0000,
            timestamp=datetime.now()
        )
        
        view = ConfirmationView("close", {"channel": interaction.channel, "user": interaction.user}, handle_close_confirmation)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @discord.ui.button(label="↩️ Unclaim", style=discord.ButtonStyle.secondary, custom_id="unclaim_ticket")
    async def unclaim(self, interaction: discord.Interaction, button: discord.ui.Button):
        try:
            config = await GuildConfigManager.get_config(interaction.guild_id)
            ticket_data = active_tickets.get(interaction.channel.id, {})
            
            is_claimer = ticket_data.get('claimed_by') == interaction.user.id
            is_admin = interaction.guild.get_role(config.get('admin_role_id')) in interaction.user.roles
            
            if not (is_claimer or is_admin):
                return await interaction.response.send_message("❌ You can only unclaim tickets you have claimed!", ephemeral=True)
            
            if not ticket_data.get('claimed_by'):
                return await interaction.response.send_message("⚠️ This ticket isn't claimed by anyone!", ephemeral=True)
            
            original_name = interaction.channel.name.replace("📋-claimed-", "").replace("claimed-", "")
            await interaction.channel.edit(name=original_name)
            
            ticket_data['claimed_by'] = None
            ticket_data['claimed_at'] = None
            
            embed = discord.Embed(
                description=f"↩️ **Ticket Unclaimed**\nThis ticket has been unclaimed by {interaction.user.mention}",
                color=discord.Color.gold(),
                timestamp=datetime.now()
            )
            await interaction.response.send_message(embed=embed)
            
        except Exception as e:
            logger.error(f"Error in unclaim: {e}", exc_info=True)
            await interaction.response.send_message("❌ Failed to unclaim ticket.", ephemeral=True)

async def handle_close_confirmation(interaction: discord.Interaction, data: dict, confirmed: bool):
    if not confirmed:
        embed = discord.Embed(description="✅ **Ticket closure cancelled.**", color=discord.Color.green())
        await interaction.response.edit_message(embed=embed, view=None)
        return
    
    channel = data["channel"]
    closer = data["user"]
    config = await GuildConfigManager.get_config(interaction.guild_id)
    
    await interaction.response.edit_message(content="💾 **Saving transcript and closing in 5 seconds...**", embed=None, view=None)
    
    try:
        if channel.id in ticket_timers:
            ticket_timers[channel.id].cancel()
            del ticket_timers[channel.id]
        
        messages = []
        async for message in channel.history(limit=200, oldest_first=True):
            messages.append(message)
        
        html_content = generate_transcript(channel, messages, closer)
        file_name = f"transcript-{channel.id}-{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        with open(file_name, "w", encoding="utf-8") as f:
            f.write(html_content)
        
        if config.get('transcript_channel_id') and config.get('enable_transcripts', True):
            log_channel = interaction.guild.get_channel(config['transcript_channel_id'])
            if log_channel:
                ticket_data = active_tickets.get(channel.id, {})
                embed = discord.Embed(title="📋 Ticket Closed", color=discord.Color.dark_grey(), timestamp=datetime.now())
                embed.add_field(name="🎫 Ticket", value=channel.name, inline=True)
                embed.add_field(name="👤 Closed By", value=closer.mention, inline=True)
                embed.add_field(name="💬 Messages", value=str(len(messages)), inline=True)
                if ticket_data:
                    embed.add_field(name="📝 Type", value=ticket_data.get('type_name', 'Unknown'), inline=True)
                
                await log_channel.send(embed=embed, file=discord.File(file_name))
        
        if channel.id in active_tickets and config.get('enable_ratings', True):
            ticket_data = active_tickets[channel.id]
            owner_id = ticket_data.get('user_id')
            if owner_id:
                try:
                    owner = await interaction.guild.fetch_member(owner_id)
                    if owner:
                        rating_embed = discord.Embed(
                            title="⭐ How was your experience?",
                            description=f"Your ticket **#{channel.name}** has been closed.\n\nPlease rate your experience below!",
                            color=0xf1c40f,
                            timestamp=datetime.now()
                        )
                        rating_data = {
                            'channel_name': channel.name,
                            'category': ticket_data.get('type_name', 'Unknown'),
                            'closed_by': closer.name
                        }
                        await owner.send(embed=rating_embed, view=RatingView(rating_data))
                except:
                    pass
        
        if channel.id in active_tickets:
            guild_status = get_guild_status(interaction.guild_id)
            del active_tickets[channel.id]
            guild_status.total_tickets_created -= 1
        
        await asyncio.sleep(5)
        if os.path.exists(file_name):
            os.remove(file_name)
        
        await channel.delete()
        await update_status_embeds(interaction.guild, config)
        
    except Exception as e:
        logger.error(f"Error in close: {e}", exc_info=True)

def generate_transcript(channel, messages, closer):
    import html
    html_content = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Ticket Transcript - {channel.name}</title>
    <style>
        body {{ background: #36393f; color: #dcddde; font-family: Arial, sans-serif; padding: 20px; }}
        .header {{ background: #2f3136; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
        .message {{ background: #2f3136; padding: 10px; margin: 10px 0; border-radius: 5px; }}
        .author {{ font-weight: bold; color: #fff; }}
        .timestamp {{ color: #72767d; font-size: 12px; margin-left: 10px; }}
    </style>
</head>
<body>
    <div class="header">
        <h1>📋 Ticket Transcript: {html.escape(channel.name)}</h1>
        <p>Closed by: {html.escape(closer.name)}</p>
        <p>Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
        <p>Total Messages: {len(messages)}</p>
    </div>
"""
    for msg in messages:
        html_content += f"""
    <div class="message">
        <div class="author">{html.escape(msg.author.display_name)} <span class="timestamp">{msg.created_at.strftime('%Y-%m-%d %H:%M:%S')}</span></div>
        <div class="content">{html.escape(msg.content or "*No content*")}</div>
    </div>
"""
    html_content += "</body></html>"
    return html_content

async def create_ticket(interaction: discord.Interaction, category: dict, config: dict):
    """Create a new ticket"""
    try:
        guild = interaction.guild
        category_channel = guild.get_channel(config.get('ticket_category_id'))
        member = interaction.user
        
        # Check user's ticket limit
        user_tickets = [t for t in active_tickets.values() if t.get('user_id') == member.id]
        if len(user_tickets) >= config.get('max_tickets_per_user', 3):
            embed = discord.Embed(
                title="❌ Too Many Tickets",
                description=f"You already have {len(user_tickets)} open tickets. Maximum is {config.get('max_tickets_per_user', 3)}.",
                color=discord.Color.red()
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        
        view_roles = []
        for role_id in category.get('view_roles', []):
            role = guild.get_role(role_id)
            if role:
                view_roles.append(role)
        
        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            member: discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True)
        }
        
        for role in view_roles:
            overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True, manage_messages=True)
        
        admin_role = guild.get_role(config.get('admin_role_id'))
        if admin_role:
            overwrites[admin_role] = discord.PermissionOverwrite(read_messages=True, send_messages=True, read_message_history=True, attach_files=True, embed_links=True, manage_messages=True, manage_channels=True)
        
        safe_name = f"{category['emoji']}-{category['value']}-{member.name}".lower()
        safe_name = ''.join(c for c in safe_name if c.isalnum() or c in '-')[:95]
        
        channel = await guild.create_text_channel(
            name=safe_name,
            topic=f"Category: {category['name']} | User: {member.name} | ID: {member.id}",
            category=category_channel,
            overwrites=overwrites,
            reason=f"Ticket created by {member.name}"
        )
        
        active_tickets[channel.id] = {
            'user_id': member.id,
            'user_name': member.name,
            'type': category['value'],
            'type_name': category['name'],
            'created_at': datetime.now(),
            'claimed_by': None,
            'claimed_at': None,
            'view_roles': category.get('view_roles', []),
            'color': category.get('color', 0x3498db)
        }
        
        embed = discord.Embed(
            title=f"{category['emoji']} {category['name']} Ticket",
            description=f"Welcome {member.mention}! The appropriate staff team will assist you shortly.",
            color=category.get('color', 0x3498db),
            timestamp=datetime.now()
        )
        
        embed.add_field(name="📋 Category", value=category['name'], inline=True)
        embed.add_field(name="👤 User", value=member.mention, inline=True)
        
        if category.get('warning'):
            embed.add_field(name="⚠️ Important", value=category['warning'], inline=False)
        
        ping_mentions = []
        for role_id in category.get('ping_roles', []):
            role = guild.get_role(role_id)
            if role:
                ping_mentions.append(role.mention)
        
        ping_content = f"{member.mention} {' '.join(ping_mentions)}" if ping_mentions else member.mention
        
        await channel.send(content=ping_content, embed=embed, view=TicketControls())
        
        asyncio.create_task(start_auto_mention_timer(channel, category, member.id, guild.id))
        
        success_embed = discord.Embed(
            title="✅ Ticket Created",
            description=f"Your ticket has been created: {channel.mention}",
            color=discord.Color.green()
        )
        await interaction.response.send_message(embed=success_embed, ephemeral=True)
        
        await update_status_embeds(guild, config)
        
    except Exception as e:
        logger.error(f"Failed to create ticket: {e}", exc_info=True)
        await interaction.response.send_message("❌ Failed to create ticket.", ephemeral=True)

# --- SLASH COMMANDS ---

@bot.tree.command(name="settings", description="⚙️ Configure the ticket system for this server")
@app_commands.default_permissions(administrator=True)
async def settings(interaction: discord.Interaction):
    """Open settings menu to configure the bot"""
    
    embed = discord.Embed(
        title="⚙️ Ticket System Settings",
        description="Select a category to configure:",
        color=0x5865f2,
        timestamp=datetime.now()
    )
    
    embed.add_field(
        name="📁 Channels & Roles",
        value="Configure ticket category, transcript channel, rating channel, and staff roles",
        inline=False
    )
    embed.add_field(
        name="📋 Ticket Categories",
        value="Add, remove, or modify ticket categories",
        inline=False
    )
    embed.add_field(
        name="⚙️ System Settings",
        value="Configure auto-close, ticket limits, and other settings",
        inline=False
    )
    embed.add_field(
        name="📊 View Current Config",
        value="See your current server configuration",
        inline=False
    )
    
    view = SettingsView()
    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class SettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
    
    @discord.ui.button(label="📁 Channels & Roles", style=discord.ButtonStyle.primary, emoji="📁")
    async def channels_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📁 Channels & Roles Setup",
            description="Use the buttons below to configure each setting:",
            color=0x3498db
        )
        
        view = ChannelsRolesView()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="📋 Ticket Categories", style=discord.ButtonStyle.primary, emoji="📋")
    async def ticket_categories(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="📋 Ticket Categories",
            description="Manage your ticket categories:",
            color=0x9b59b6
        )
        
        view = CategoriesView()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="⚙️ System Settings", style=discord.ButtonStyle.primary, emoji="⚙️")
    async def system_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        
        embed = discord.Embed(
            title="⚙️ System Settings",
            color=0xf1c40f,
            timestamp=datetime.now()
        )
        
        embed.add_field(name="📝 Auto-Close Days", value=f"`{config.get('auto_close_days', 7)} days`", inline=True)
        embed.add_field(name="🔢 Max Tickets Per User", value=f"`{config.get('max_tickets_per_user', 3)}`", inline=True)
        embed.add_field(name="📄 Enable Transcripts", value=f"`{config.get('enable_transcripts', True)}`", inline=True)
        embed.add_field(name="⭐ Enable Ratings", value=f"`{config.get('enable_ratings', True)}`", inline=True)
        embed.add_field(name="⏰ Enable Auto-Mention", value=f"`{config.get('enable_auto_mention', True)}`", inline=True)
        
        view = SystemSettingsView()
        await interaction.response.edit_message(embed=embed, view=view)
    
    @discord.ui.button(label="📊 View Config", style=discord.ButtonStyle.secondary, emoji="📊")
    async def view_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        
        embed = discord.Embed(
            title="📊 Current Server Configuration",
            color=0x2ecc71,
            timestamp=datetime.now()
        )
        
        # Channels
        channels_text = ""
        channel_configs = {
            "Ticket Category": config.get('ticket_category_id'),
            "Transcript Channel": config.get('transcript_channel_id'),
            "Rating Channel": config.get('rating_channel_id')
        }
        for name, channel_id in channel_configs.items():
            if channel_id:
                channel = interaction.guild.get_channel(channel_id)
                channels_text += f"✅ {name}: {channel.mention if channel else f'`{channel_id}`'}\n"
            else:
                channels_text += f"❌ {name}: Not set\n"
        embed.add_field(name="📁 Channels", value=channels_text, inline=False)
        
        # Roles
        roles_text = ""
        role_configs = {
            "Admin": config.get('admin_role_id'),
            "Staff": config.get('staff_role_id'),
            "Moderation": config.get('moderation_role_id'),
            "Marketing": config.get('marketing_role_id'),
            "Development": config.get('development_role_id'),
            "Status Control": config.get('status_control_role_id')
        }
        for name, role_id in role_configs.items():
            if role_id:
                role = interaction.guild.get_role(role_id)
                roles_text += f"✅ {name}: {role.mention if role else f'`{role_id}`'}\n"
            else:
                roles_text += f"❌ {name}: Not set\n"
        embed.add_field(name="👥 Roles", value=roles_text, inline=False)
        
        # Categories
        categories = config.get('ticket_categories', [])
        categories_text = f"Total categories: {len(categories)}\n"
        for cat in categories[:5]:
            categories_text += f"• {cat['emoji']} {cat['name']}\n"
        if len(categories) > 5:
            categories_text += f"...and {len(categories) - 5} more"
        embed.add_field(name="📋 Categories", value=categories_text, inline=False)
        
        # System Settings
        embed.add_field(name="⚙️ Settings", value=f"Auto-close: {config.get('auto_close_days', 7)} days\nMax tickets/user: {config.get('max_tickets_per_user', 3)}", inline=False)
        
        await interaction.response.edit_message(embed=embed, view=SettingsView())

class ChannelsRolesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
    
    @discord.ui.button(label="📁 Set Ticket Category", style=discord.ButtonStyle.secondary, emoji="📁")
    async def set_ticket_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Set Ticket Category",
            description="Please mention the category where tickets should be created.\n\nExample: `#tickets` or use the channel ID.",
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_id = None
            if msg.channel_mentions:
                channel_id = msg.channel_mentions[0].id
            elif msg.content.isdigit():
                channel_id = int(msg.content)
            
            if channel_id:
                await GuildConfigManager.update_config(interaction.guild_id, {'ticket_category_id': channel_id})
                await msg.add_reaction("✅")
                await interaction.followup.send("✅ Ticket category set!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid channel!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="📄 Set Transcript Channel", style=discord.ButtonStyle.secondary, emoji="📄")
    async def set_transcript_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Set Transcript Channel",
            description="Please mention the channel where transcripts should be sent.",
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_id = None
            if msg.channel_mentions:
                channel_id = msg.channel_mentions[0].id
            elif msg.content.isdigit():
                channel_id = int(msg.content)
            
            if channel_id:
                await GuildConfigManager.update_config(interaction.guild_id, {'transcript_channel_id': channel_id})
                await msg.add_reaction("✅")
                await interaction.followup.send("✅ Transcript channel set!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid channel!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="⭐ Set Rating Channel", style=discord.ButtonStyle.secondary, emoji="⭐")
    async def set_rating_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Set Rating Channel",
            description="Please mention the channel where ratings should be sent.",
            color=0x3498db
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            channel_id = None
            if msg.channel_mentions:
                channel_id = msg.channel_mentions[0].id
            elif msg.content.isdigit():
                channel_id = int(msg.content)
            
            if channel_id:
                await GuildConfigManager.update_config(interaction.guild_id, {'rating_channel_id': channel_id})
                await msg.add_reaction("✅")
                await interaction.followup.send("✅ Rating channel set!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid channel!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="👥 Set Roles", style=discord.ButtonStyle.primary, emoji="👥")
    async def set_roles(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="Set Staff Roles",
            description="Which role would you like to set?\n\n"
                       "1️⃣ Admin Role\n"
                       "2️⃣ Staff Role\n"
                       "3️⃣ Moderation Role\n"
                       "4️⃣ Marketing Role\n"
                       "5️⃣ Development Role\n"
                       "6️⃣ Status Control Role\n\n"
                       "Please send the number of the role you want to set.",
            color=0x9b59b6
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            role_type = msg.content.strip()
            
            role_map = {
                "1": "admin_role_id",
                "2": "staff_role_id", 
                "3": "moderation_role_id",
                "4": "marketing_role_id",
                "5": "development_role_id",
                "6": "status_control_role_id"
            }
            
            if role_type not in role_map:
                await interaction.followup.send("❌ Invalid option!", ephemeral=True)
                return
            
            await interaction.followup.send("Please mention the role now:", ephemeral=True)
            role_msg = await bot.wait_for('message', timeout=60, check=check)
            
            role_id = None
            if role_msg.role_mentions:
                role_id = role_msg.role_mentions[0].id
            
            if role_id:
                await GuildConfigManager.update_config(interaction.guild_id, {role_map[role_type]: role_id})
                await role_msg.add_reaction("✅")
                await interaction.followup.send("✅ Role set!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid role!", ephemeral=True)
                
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, emoji="🔙")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Ticket System Settings",
            description="Select a category to configure:",
            color=0x5865f2
        )
        await interaction.response.edit_message(embed=embed, view=SettingsView())

class CategoriesView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
    
    @discord.ui.button(label="➕ Add Category", style=discord.ButtonStyle.success, emoji="➕")
    async def add_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        categories = config.get('ticket_categories', [])
        
        if len(categories) >= 25:
            await interaction.response.send_message("❌ Maximum 25 categories allowed!", ephemeral=True)
            return
        
        await interaction.response.send_message(
            "Please provide category details in this format:\n"
            "`name|value|description|emoji|color_hex`\n\n"
            "Example: `General Support|general|General questions|❓|#3498db`\n\n"
            "Color hex can be: #3498db, #2ecc71, #e74c3c, etc.",
            ephemeral=True
        )
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel
        
        try:
            msg = await bot.wait_for('message', timeout=120, check=check)
            parts = msg.content.split('|')
            
            if len(parts) >= 4:
                name = parts[0].strip()
                value = parts[1].strip().lower().replace(' ', '_')
                description = parts[2].strip()
                emoji = parts[3].strip() if len(parts) > 3 else "📝"
                color_hex = parts[4].strip() if len(parts) > 4 else "#3498db"
                
                # Convert hex to int
                color = int(color_hex.lstrip('#'), 16)
                
                new_category = {
                    "name": name,
                    "value": value,
                    "description": description,
                    "emoji": emoji,
                    "color": color,
                    "view_roles": [config.get('staff_role_id'), config.get('admin_role_id')],
                    "ping_roles": [config.get('staff_role_id')],
                    "auto_mention": True
                }
                
                categories.append(new_category)
                await GuildConfigManager.update_config(interaction.guild_id, {'ticket_categories': categories})
                await msg.add_reaction("✅")
                await interaction.followup.send(f"✅ Category **{name}** added!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid format! Please use: `name|value|description|emoji|color`", ephemeral=True)
                
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="🗑️ Remove Category", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def remove_category(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        categories = config.get('ticket_categories', [])
        
        if not categories:
            await interaction.response.send_message("❌ No categories to remove!", ephemeral=True)
            return
        
        category_list = "\n".join([f"{i+1}. {cat['emoji']} {cat['name']}" for i, cat in enumerate(categories)])
        embed = discord.Embed(
            title="Remove Category",
            description=f"Which category would you like to remove?\n\n{category_list}\n\nSend the number of the category.",
            color=0xe74c3c
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and m.content.isdigit()
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            index = int(msg.content) - 1
            
            if 0 <= index < len(categories):
                removed = categories.pop(index)
                await GuildConfigManager.update_config(interaction.guild_id, {'ticket_categories': categories})
                await msg.add_reaction("✅")
                await interaction.followup.send(f"✅ Category **{removed['name']}** removed!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Invalid number!", ephemeral=True)
                
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout! Please try again.", ephemeral=True)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, emoji="🔙")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Ticket System Settings",
            description="Select a category to configure:",
            color=0x5865f2
        )
        await interaction.response.edit_message(embed=embed, view=SettingsView())

class SystemSettingsView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
    
    @discord.ui.button(label="📝 Set Auto-Close Days", style=discord.ButtonStyle.secondary, emoji="📝")
    async def set_autoclose(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please enter the number of days after which tickets auto-close (1-30):", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and m.content.isdigit()
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            days = int(msg.content)
            
            if 1 <= days <= 30:
                await GuildConfigManager.update_config(interaction.guild_id, {'auto_close_days': days})
                await msg.add_reaction("✅")
                await interaction.followup.send(f"✅ Auto-close set to {days} days!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Please enter a number between 1 and 30!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout!", ephemeral=True)
    
    @discord.ui.button(label="🔢 Set Max Tickets", style=discord.ButtonStyle.secondary, emoji="🔢")
    async def set_maxtickets(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Please enter the maximum number of tickets per user (1-10):", ephemeral=True)
        
        def check(m):
            return m.author == interaction.user and m.channel == interaction.channel and m.content.isdigit()
        
        try:
            msg = await bot.wait_for('message', timeout=60, check=check)
            max_tickets = int(msg.content)
            
            if 1 <= max_tickets <= 10:
                await GuildConfigManager.update_config(interaction.guild_id, {'max_tickets_per_user': max_tickets})
                await msg.add_reaction("✅")
                await interaction.followup.send(f"✅ Max tickets set to {max_tickets} per user!", ephemeral=True)
            else:
                await interaction.followup.send("❌ Please enter a number between 1 and 10!", ephemeral=True)
        except asyncio.TimeoutError:
            await interaction.followup.send("⏰ Timeout!", ephemeral=True)
    
    @discord.ui.button(label="📄 Toggle Transcripts", style=discord.ButtonStyle.secondary, emoji="📄")
    async def toggle_transcripts(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        current = config.get('enable_transcripts', True)
        new_value = not current
        await GuildConfigManager.update_config(interaction.guild_id, {'enable_transcripts': new_value})
        await interaction.response.send_message(f"✅ Transcripts {'enabled' if new_value else 'disabled'}!", ephemeral=True)
    
    @discord.ui.button(label="⭐ Toggle Ratings", style=discord.ButtonStyle.secondary, emoji="⭐")
    async def toggle_ratings(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        current = config.get('enable_ratings', True)
        new_value = not current
        await GuildConfigManager.update_config(interaction.guild_id, {'enable_ratings': new_value})
        await interaction.response.send_message(f"✅ Ratings {'enabled' if new_value else 'disabled'}!", ephemeral=True)
    
    @discord.ui.button(label="⏰ Toggle Auto-Mention", style=discord.ButtonStyle.secondary, emoji="⏰")
    async def toggle_automention(self, interaction: discord.Interaction, button: discord.ui.Button):
        config = await GuildConfigManager.get_config(interaction.guild_id)
        current = config.get('enable_auto_mention', True)
        new_value = not current
        await GuildConfigManager.update_config(interaction.guild_id, {'enable_auto_mention': new_value})
        await interaction.response.send_message(f"✅ Auto-mention {'enabled' if new_value else 'disabled'}!", ephemeral=True)
    
    @discord.ui.button(label="🔙 Back", style=discord.ButtonStyle.secondary, emoji="🔙")
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button):
        embed = discord.Embed(
            title="⚙️ Ticket System Settings",
            description="Select a category to configure:",
            color=0x5865f2
        )
        await interaction.response.edit_message(embed=embed, view=SettingsView())

@bot.tree.command(name="setup", description="📋 Deploy the ticket panel with 4 embeds")
@app_commands.default_permissions(administrator=True)
async def setup(interaction: discord.Interaction):
    """Deploy the ticket panel"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    
    if not config.get('ticket_categories'):
        return await interaction.response.send_message(
            "❌ Please set up ticket categories first using `/settings`!",
            ephemeral=True
        )
    
    if not config.get('ticket_category_id'):
        return await interaction.response.send_message(
            "❌ Please set the ticket category first using `/settings`!",
            ephemeral=True
        )
    
    current_tickets = len([c for c in interaction.guild.channels if c.category_id == config.get('ticket_category_id')])
    guild_status = get_guild_status(interaction.guild_id)
    ticket_count_status = guild_status.get_ticket_count_status(current_tickets)
    service_status = guild_status.get_service_status(current_tickets)
    
    embeds = await create_status_embeds(interaction.guild, config, current_tickets, ticket_count_status, service_status)
    
    await interaction.response.send_message("✅ **Panel deployed!**", ephemeral=True)
    message = await interaction.channel.send(embeds=embeds, view=TicketDropdownView(config.get('ticket_categories', [])))
    
    if interaction.guild_id not in config.get('panel_messages', {}):
        config['panel_messages'] = {}
    config['panel_messages'][interaction.channel.id] = message.id
    await GuildConfigManager.update_config(interaction.guild_id, {'panel_messages': config['panel_messages']})

@bot.tree.command(name="announce", description="📢 Send announcement to tickets")
@app_commands.default_permissions(administrator=True)
async def announce(interaction: discord.Interaction, message: str, category: str = "all"):
    """Send announcement to tickets"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    
    admin_role = interaction.guild.get_role(config.get('admin_role_id'))
    if admin_role not in interaction.user.roles:
        return await interaction.response.send_message("❌ Admin only!", ephemeral=True)
    
    all_tickets = [c for c in interaction.guild.channels if c.category_id == config.get('ticket_category_id')]
    
    if category != "all":
        tickets = [t for t in all_tickets if f"-{category}-" in t.name]
    else:
        tickets = all_tickets
    
    if not tickets:
        return await interaction.response.send_message("📭 No tickets found.", ephemeral=True)
    
    embed = discord.Embed(title="📢 **Announcement**", description=message, color=0xf1c40f, timestamp=datetime.now())
    embed.set_footer(text=f"Announced by {interaction.user.name}", icon_url=interaction.user.display_avatar.url)
    
    success_count = 0
    await interaction.response.send_message(f"📢 Sending announcement to {len(tickets)} tickets...", ephemeral=True)
    
    for ticket in tickets:
        try:
            await ticket.send(embed=embed)
            success_count += 1
            await asyncio.sleep(0.5)
        except:
            pass
    
    await interaction.followup.send(f"✅ Announcement sent to {success_count}/{len(tickets)} tickets!", ephemeral=True)

@bot.tree.command(name="set-ticket-status", description="🎛️ Manually set ticket count status")
@app_commands.default_permissions(administrator=True)
async def set_ticket_status(interaction: discord.Interaction, status: str):
    """Manually set the ticket count status"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    status_role = interaction.guild.get_role(config.get('status_control_role_id'))
    admin_role = interaction.guild.get_role(config.get('admin_role_id'))
    
    if (status_role not in interaction.user.roles and admin_role not in interaction.user.roles):
        return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)
    
    guild_status = get_guild_status(interaction.guild_id)
    guild_status.ticket_count_mode = "manual"
    guild_status.manual_ticket_status = status
    guild_status.last_updated = datetime.now()
    
    current_tickets = len([c for c in interaction.guild.channels if c.category_id == config.get('ticket_category_id')])
    
    embed = discord.Embed(
        title="✅ Ticket Status Updated",
        description=f"Ticket count status set to: **{status.upper()}** {guild_status.get_status_emoji(status)}\nMode: **Manual**\nCurrent tickets: **{current_tickets}**",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await update_status_embeds(interaction.guild, config)

@bot.tree.command(name="set-service-status", description="🎛️ Manually set service status")
@app_commands.default_permissions(administrator=True)
async def set_service_status(interaction: discord.Interaction, status: str):
    """Manually set the service status"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    status_role = interaction.guild.get_role(config.get('status_control_role_id'))
    admin_role = interaction.guild.get_role(config.get('admin_role_id'))
    
    if (status_role not in interaction.user.roles and admin_role not in interaction.user.roles):
        return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)
    
    guild_status = get_guild_status(interaction.guild_id)
    guild_status.service_mode = "manual"
    guild_status.manual_service_status = status
    guild_status.last_updated = datetime.now()
    
    embed = discord.Embed(
        title="✅ Service Status Updated",
        description=f"Service status set to: **{status.upper()}** {guild_status.get_status_emoji(status)}\nMode: **Manual**",
        color=discord.Color.green(),
        timestamp=datetime.now()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await update_status_embeds(interaction.guild, config)

@bot.tree.command(name="auto", description="🤖 Switch status back to automatic mode")
@app_commands.default_permissions(administrator=True)
async def set_auto_mode(interaction: discord.Interaction):
    """Switch both statuses back to automatic mode"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    status_role = interaction.guild.get_role(config.get('status_control_role_id'))
    admin_role = interaction.guild.get_role(config.get('admin_role_id'))
    
    if (status_role not in interaction.user.roles and admin_role not in interaction.user.roles):
        return await interaction.response.send_message("❌ You don't have permission!", ephemeral=True)
    
    guild_status = get_guild_status(interaction.guild_id)
    guild_status.ticket_count_mode = "auto"
    guild_status.service_mode = "auto"
    guild_status.last_updated = datetime.now()
    
    embed = discord.Embed(
        title="🤖 Automatic Mode Restored",
        description="Both status systems have been switched back to **automatic mode**.",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)
    await update_status_embeds(interaction.guild, config)

@bot.tree.command(name="status", description="📊 View current system status")
async def view_status(interaction: discord.Interaction):
    """View current ticket system status"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    current_tickets = len([c for c in interaction.guild.channels if c.category_id == config.get('ticket_category_id')])
    guild_status = get_guild_status(interaction.guild_id)
    ticket_stat = guild_status.get_ticket_count_status(current_tickets)
    service_stat = guild_status.get_service_status(current_tickets)
    
    embed = discord.Embed(title="📊 **System Status Overview**", color=0x5865f2, timestamp=datetime.now())
    embed.add_field(name=f"{guild_status.get_status_emoji(ticket_stat)} Ticket Load", 
                   value=f"**Status:** {ticket_stat.upper()}\n**Current:** {current_tickets} tickets", inline=True)
    embed.add_field(name=f"{guild_status.get_status_emoji(service_stat)} Service Status", 
                   value=f"**Status:** {service_stat.upper()}", inline=True)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="help", description="❓ Show all commands")
async def help_command(interaction: discord.Interaction):
    """Show all available commands"""
    embed = discord.Embed(title="🎫 Ticket Bot Commands", color=0x5865f2, timestamp=datetime.now())
    embed.add_field(name="/setup", value="Deploy the ticket panel", inline=False)
    embed.add_field(name="/settings", value="Configure the bot for your server", inline=False)
    embed.add_field(name="/announce", value="Send announcement to all tickets", inline=False)
    embed.add_field(name="/set-ticket-status", value="Manually set ticket load status", inline=False)
    embed.add_field(name="/set-service-status", value="Manually set service status", inline=False)
    embed.add_field(name="/auto", value="Switch back to automatic status mode", inline=False)
    embed.add_field(name="/status", value="View current system status", inline=False)
    embed.add_field(name="/refresh-panels", value="Refresh all status panels", inline=False)
    embed.add_field(name="/help", value="Show this help message", inline=False)
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="refresh-panels", description="🔄 Refresh all status panels")
@app_commands.default_permissions(administrator=True)
async def refresh_panels(interaction: discord.Interaction):
    """Manually refresh all status panels"""
    config = await GuildConfigManager.get_config(interaction.guild_id)
    await interaction.response.send_message("🔄 Refreshing panels...", ephemeral=True)
    await update_status_embeds(interaction.guild, config)
    await interaction.followup.send("✅ Panels refreshed!", ephemeral=True)

# --- ERROR HANDLER ---
@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f"Command error: {error}")
    embed = discord.Embed(title="❌ Error", description=str(error), color=discord.Color.red())
    if not interaction.response.is_done():
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.followup.send(embed=embed, ephemeral=True)

# --- EVENT HANDLERS ---
@bot.event
async def on_guild_channel_delete(channel):
    if channel.id in active_tickets:
        if channel.id in ticket_timers:
            ticket_timers[channel.id].cancel()
            del ticket_timers[channel.id]
        del active_tickets[channel.id]

# At the VERY BOTTOM of bot.py - REPLACE the existing run block with this
import asyncio
import threading

TOKEN = os.getenv('DISCORD_BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')

def start_bot():
    asyncio.run(bot.start(TOKEN))

# Start bot in background thread
if TOKEN and TOKEN != 'YOUR_BOT_TOKEN_HERE':
    thread = threading.Thread(target=start_bot, daemon=True)
    thread.start()
    print("Bot started in background thread")
