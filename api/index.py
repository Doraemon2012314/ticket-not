import sys
import os

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import the bot (which will auto-start)
from main import bot, TOKEN

from http.server import BaseHTTPRequestHandler
import json
import asyncio
import threading

# Start bot in background if not already running
def run_bot():
    try:
        asyncio.run(bot.start(TOKEN))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(bot.start(TOKEN))

if TOKEN and TOKEN != 'YOUR_BOT_TOKEN_HERE':
    thread = threading.Thread(target=run_bot, daemon=True)
    thread.start()

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        response = {"status": "online", "bot": str(bot.user.name) if bot.user else "starting"}
        self.wfile.write(json.dumps(response).encode())
