import sys
import os
import asyncio

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import your bot
from bot import bot, TOKEN

# Create a simple HTTP handler for Vercel
from http.server import BaseHTTPRequestHandler

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/plain')
        self.end_headers()
        self.wfile.write(b"Ticket Bot is running!")
    
    def do_POST(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

# Start the bot in the background
def start_bot():
    try:
        asyncio.get_event_loop().run_until_complete(bot.start(TOKEN))
    except:
        pass

# Run bot in background thread
import threading
thread = threading.Thread(target=start_bot, daemon=True)
thread.start()
