from http.server import BaseHTTPRequestHandler
import json
import os
import sys
import asyncio
import threading

# Add parent directory to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Global variable to track bot status
bot_started = False

class handler(BaseHTTPRequestHandler):
    def do_GET(self):
        """Handle GET requests to keep bot alive"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        
        # Start bot if not already started
        global bot_started
        if not bot_started:
            bot_started = True
            thread = threading.Thread(target=self.start_bot, daemon=True)
            thread.start()
        
        response = {
            "status": "online",
            "message": "Ticket bot is running!",
            "bot_started": bot_started
        }
        self.wfile.write(json.dumps(response).encode())
    
    def do_POST(self):
        """Handle POST requests"""
        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({"status": "ok"}).encode())
    
    def start_bot(self):
        """Start the Discord bot in a separate event loop"""
        try:
            # Import here to avoid circular imports
            from bot import bot, TOKEN
            
            # Create new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            # Run the bot
            loop.run_until_complete(bot.start(TOKEN))
        except Exception as e:
            print(f"Error starting bot: {e}")

# Required for Vercel
app = handler
